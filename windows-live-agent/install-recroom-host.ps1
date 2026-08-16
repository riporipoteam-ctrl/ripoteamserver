param(
  [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "RipoTeam\RecRoomHost"),
  [string]$SteamUsername = "",
  [string]$PairingCode = "",
  [switch]$TrySteamDownload,
  [switch]$Start
)

$ErrorActionPreference = "Stop"
$Repo = "riporipoteam-ctrl/ripoteamserver"
$Ref = "main"
$ToolsRepo = "riporipoteam-ctrl/recroomfluxgame"
$ToolsRef = "main"

function Resolve-GitHubCommit([string]$Repository, [string]$Reference) {
  $encodedRef = [Uri]::EscapeDataString($Reference)
  $uri = "https://api.github.com/repos/$Repository/commits/$encodedRef"
  $result = Invoke-RestMethod -UseBasicParsing -Headers @{
    "User-Agent" = "RipoTeam-RecRoom-Host"
    "Accept" = "application/vnd.github+json"
    "Cache-Control" = "no-cache"
  } -Uri $uri -TimeoutSec 30
  $sha = [string]$result.sha
  if ($sha -notmatch '^[0-9a-fA-F]{40}$') { throw "Could not resolve $Repository@$Reference to an immutable Git commit." }
  return $sha.ToLowerInvariant()
}

$hostSha = Resolve-GitHubCommit $Repo $Ref
$toolsSha = Resolve-GitHubCommit $ToolsRepo $ToolsRef
Write-Host "Host tools revision: $hostSha" -ForegroundColor DarkGray
Write-Host "Rec Room proxy tools revision: $toolsSha" -ForegroundColor DarkGray

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

$files = @(
  "start-recroom-host.ps1",
  "bootstrap-recroom-host.ps1",
  "download-recroom-client.ps1",
  "identify-recroom-client.ps1",
  "update-recroom-host.ps1",
  "recroom-agent.ps1",
  "recroom-capture-agent.ps1",
  "playtest-recroom-client.ps1",
  "start-recroom-browser-stream.ps1",
  "stop-recroom-browser-stream.ps1",
  "recroom-web-stream.py",
  "requirements.txt",
  "recroom-agent-config.example.json"
)

foreach ($name in $files) {
  $url = "https://raw.githubusercontent.com/$Repo/$hostSha/windows-live-agent/$name"
  $destination = Join-Path $InstallDir $name
  Write-Host "Fetching $name..." -ForegroundColor DarkCyan
  Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $destination -TimeoutSec 60
  if (-not (Test-Path $destination) -or (Get-Item $destination).Length -le 0) {
    throw "Host tool download failed: $name"
  }
}

$toolsDir = Join-Path $InstallDir "recroom-tools"
New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
foreach ($name in @("host-proxy.mjs", "redirect-client-urls.mjs", "verify-client.mjs", "scan-client-urls.mjs")) {
  $url = "https://raw.githubusercontent.com/$ToolsRepo/$toolsSha/scripts/$name"
  $destination = Join-Path $toolsDir $name
  Write-Host "Fetching tool $name..." -ForegroundColor DarkCyan
  Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $destination -TimeoutSec 60
  if (-not (Test-Path $destination) -or (Get-Item $destination).Length -le 0) {
    throw "Rec Room client tool download failed: $name"
  }
}

$installState = [ordered]@{
  installedAt = [DateTimeOffset]::UtcNow.ToString("o")
  hostRepository = $Repo
  hostRef = $Ref
  hostCommit = $hostSha
  toolsRepository = $ToolsRepo
  toolsRef = $ToolsRef
  toolsCommit = $toolsSha
}
$installState | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $InstallDir "recroom-install-state.json") -Encoding UTF8

$bootstrap = Join-Path $InstallDir "bootstrap-recroom-host.ps1"
$config = Join-Path $InstallDir "recroom-agent-config.json"
$arguments = @(
  "-NoProfile", "-ExecutionPolicy", "Bypass",
  "-File", $bootstrap,
  "-Config", $config
)
if ($TrySteamDownload) { $arguments += "-TrySteamDownload" }
if ($SteamUsername) { $arguments += @("-SteamUsername", $SteamUsername) }
if ($PairingCode) { $arguments += @("-PairingCode", $PairingCode) }

& powershell.exe @arguments
if ($LASTEXITCODE -ne 0) { throw "Rec Room host bootstrap failed." }

Write-Host "Flux Rec Room Windows host installed at $InstallDir" -ForegroundColor Green
Write-Host "Strict build identification, client redirect, browser controls, and the real-client playtest harness are installed." -ForegroundColor Green
Write-Host "The client is never uploaded by this installer. Steam authentication, if requested, happens locally on this PC." -ForegroundColor DarkGray

if ($Start) {
  $starter = Join-Path $InstallDir "start-recroom-host.ps1"
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $starter -Config $config
} else {
  Write-Host "Start command:" -ForegroundColor Cyan
  Write-Host "  powershell -ExecutionPolicy Bypass -File `"$InstallDir\start-recroom-host.ps1`""
  Write-Host "Identify client command:" -ForegroundColor Cyan
  Write-Host "  powershell -ExecutionPolicy Bypass -File `"$InstallDir\identify-recroom-client.ps1`" -Scan"
  Write-Host "Playtest command:" -ForegroundColor Cyan
  Write-Host "  powershell -ExecutionPolicy Bypass -File `"$InstallDir\playtest-recroom-client.ps1`" -ClientDir <May-2022-client> -GatewayUrl <gateway> -SessionToken <token>"
}

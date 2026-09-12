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

function Get-FileSha256([string]$Path) {
  $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
  try {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $bytes = $sha.ComputeHash($stream) } finally { $sha.Dispose() }
  } finally { $stream.Dispose() }
  return (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")
}

function Download-RepoSnapshot([string]$Repository, [string]$Reference, [string]$DestinationRoot) {
  $parts = $Repository.Split('/')
  if ($parts.Count -ne 2) { throw "Invalid GitHub repository: $Repository" }
  $owner = $parts[0]; $name = $parts[1]
  New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
  $zip = Join-Path $DestinationRoot ($name + ".zip")
  $extract = Join-Path $DestinationRoot ($name + "-extract")
  $nonce = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
  $uri = "https://github.com/$owner/$name/archive/refs/heads/$Reference.zip?download=$nonce"
  Write-Host "Downloading coherent snapshot $Repository@$Reference..." -ForegroundColor DarkCyan
  Invoke-WebRequest -UseBasicParsing -Headers @{ "Cache-Control" = "no-cache" } -Uri $uri -OutFile $zip -TimeoutSec 120
  if (-not (Test-Path $zip) -or (Get-Item $zip).Length -lt 256) { throw "GitHub snapshot download failed for $Repository@$Reference" }
  Expand-Archive -Path $zip -DestinationPath $extract -Force
  $root = Get-ChildItem -LiteralPath $extract -Directory | Select-Object -First 1
  if (-not $root) { throw "GitHub snapshot for $Repository@$Reference had no repository root." }
  return [pscustomobject]@{ Root=$root.FullName; Zip=$zip; Sha256=(Get-FileSha256 $zip) }
}

$tempRoot = Join-Path $env:TEMP ("FluxRecRoomInstall-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
try {
  $hostSnapshot = Download-RepoSnapshot $Repo $Ref (Join-Path $tempRoot "host")
  $toolsSnapshot = Download-RepoSnapshot $ToolsRepo $ToolsRef (Join-Path $tempRoot "tools")

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
    $source = Join-Path $hostSnapshot.Root ("windows-live-agent\" + $name)
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Host snapshot omitted $name" }
    Copy-Item -LiteralPath $source -Destination (Join-Path $InstallDir $name) -Force
  }

  $toolsDir = Join-Path $InstallDir "recroom-tools"
  New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
  foreach ($name in @("host-proxy.mjs", "redirect-client-urls.mjs", "verify-client.mjs", "scan-client-urls.mjs", "patch-client-urls.mjs")) {
    $source = Join-Path $toolsSnapshot.Root ("scripts\" + $name)
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "Rec Room proxy snapshot omitted $name" }
    Copy-Item -LiteralPath $source -Destination (Join-Path $toolsDir $name) -Force
  }

  $installState = [ordered]@{
    installedAt = [DateTimeOffset]::UtcNow.ToString("o")
    hostRepository = $Repo
    hostRef = $Ref
    hostSnapshotSha256 = $hostSnapshot.Sha256
    toolsRepository = $ToolsRepo
    toolsRef = $ToolsRef
    toolsSnapshotSha256 = $toolsSnapshot.Sha256
  }
  $installState | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $InstallDir "recroom-install-state.json") -Encoding UTF8
} finally {
  Remove-Item $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

$bootstrap = Join-Path $InstallDir "bootstrap-recroom-host.ps1"
$config = Join-Path $InstallDir "recroom-agent-config.json"
$arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $bootstrap, "-Config", $config)
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

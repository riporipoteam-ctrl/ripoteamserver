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

New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null

$files = @(
  "start-recroom-host.ps1",
  "bootstrap-recroom-host.ps1",
  "download-recroom-client.ps1",
  "update-recroom-host.ps1",
  "recroom-agent.ps1",
  "recroom-capture-agent.ps1",
  "start-recroom-browser-stream.ps1",
  "stop-recroom-browser-stream.ps1",
  "recroom-web-stream.py",
  "requirements.txt",
  "recroom-agent-config.example.json"
)

foreach ($name in $files) {
  $url = "https://raw.githubusercontent.com/$Repo/$Ref/windows-live-agent/$name"
  $destination = Join-Path $InstallDir $name
  Write-Host "Fetching $name..." -ForegroundColor DarkCyan
  Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $destination -TimeoutSec 60
  if (-not (Test-Path $destination) -or (Get-Item $destination).Length -le 0) {
    throw "Host tool download failed: $name"
  }
}

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
Write-Host "The client is never uploaded by this installer. Steam authentication, if requested, happens locally on this PC." -ForegroundColor DarkGray

if ($Start) {
  $starter = Join-Path $InstallDir "start-recroom-host.ps1"
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $starter -Config $config
} else {
  Write-Host "Start command:" -ForegroundColor Cyan
  Write-Host "  powershell -ExecutionPolicy Bypass -File `"$InstallDir\start-recroom-host.ps1`""
}

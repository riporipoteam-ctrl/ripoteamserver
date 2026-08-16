param(
  [string]$Config = (Join-Path $PSScriptRoot "recroom-agent-config.json"),
  [string]$SteamUsername = "",
  [string]$PairingCode = "",
  [switch]$TrySteamDownload,
  [switch]$Start
)

$ErrorActionPreference = "Stop"
$TargetBuild = "recroom-2022-05-19"
$canonicalRoot = Join-Path $env:LOCALAPPDATA "FluxRecRoom\May 19 2022"
$identifier = Join-Path $PSScriptRoot "identify-recroom-client.ps1"

if (-not (Test-Path $identifier)) {
  throw "Missing strict Rec Room build identifier: $identifier"
}

function Get-IdentifiedClients {
  try {
    $json = @(& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $identifier -Scan -AsJson) | Select-Object -Last 1
    if (-not $json) { return @() }
    $parsed = $json | ConvertFrom-Json
    return @($parsed.clients)
  } catch {
    Write-Host ("Client identification failed: " + $_.Exception.Message) -ForegroundColor Yellow
    return @()
  }
}

function Find-Verified2022Client {
  foreach ($item in Get-IdentifiedClients) {
    if ($item.kind -eq "target-2022" -and $item.playableBy2022Agent) {
      return [string]$item.root
    }
  }
  return ""
}

function Find-TargetArchive {
  foreach ($root in @((Join-Path $env:USERPROFILE "Downloads"), (Join-Path $env:USERPROFILE "Desktop"))) {
    if (-not (Test-Path $root)) { continue }
    $zip = Get-ChildItem -Path $root -Filter "*.zip" -File -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match '(?i)rec.?room' -and $_.Name -match '(?i)(8751857|2022|may.?19)' } |
      Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($zip) { return $zip.FullName }
  }
  return ""
}

$clientDir = Find-Verified2022Client

# A locally supplied archive is allowed to be unpacked, but it is still not
# trusted merely because of its name. identify-recroom-client.ps1 must find the
# exact validated DepotDownloader manifest evidence before it can be selected.
if (-not $clientDir) {
  $archive = Find-TargetArchive
  if ($archive) {
    Write-Host "Found a May-2022-looking archive. Extracting it for strict verification..." -ForegroundColor DarkCyan
    New-Item -ItemType Directory -Path $canonicalRoot -Force | Out-Null
    Expand-Archive -Path $archive -DestinationPath $canonicalRoot -Force
    $env:FLUX_RECROOM_CLIENT_DIR = $canonicalRoot
    $clientDir = Find-Verified2022Client
    if (-not $clientDir) {
      Write-Host "Archive extracted, but strict manifest verification did not accept it. Folder/archive names alone are never trusted." -ForegroundColor Yellow
    }
  }
}

if (-not $clientDir -and $TrySteamDownload) {
  $downloadScript = Join-Path $PSScriptRoot "download-recroom-client.ps1"
  if (-not (Test-Path $downloadScript)) { throw "Missing $downloadScript" }
  Write-Host "No strictly verified local May 2022 client found; trying the exact Steam depot with your own Steam account..." -ForegroundColor Cyan
  $downloaded = @(& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $downloadScript -SteamUsername $SteamUsername -Destination $canonicalRoot)
  if ($LASTEXITCODE -ne 0) { throw "Licensed Steam client download attempt failed." }
  $candidate = [string]($downloaded | Select-Object -Last 1)
  if ($candidate) { $env:FLUX_RECROOM_CLIENT_DIR = $candidate }
  $clientDir = Find-Verified2022Client
}

if (-not $clientDir) {
  $identified = Get-IdentifiedClients
  $details = @($identified | ForEach-Object { "[$($_.kind)] $($_.root) - $($_.reason)" }) -join "`n"
  if (-not $details) { $details = "No complete Rec Room IL2CPP clients were detected." }
  throw "No strictly verified May 19 2022 client is available. A playable host requires depot 471711 / manifest 6337851004861751095 with a valid DepotDownloader manifest checksum, or a licensed Steam download through -TrySteamDownload.`n$details"
}

$server = if ($env:RECROOM_BROKER_URL) { [string]$env:RECROOM_BROKER_URL } else { "https://echoxr-ripoteam-cloud-pc.hf.space" }
$hostId = "ripo-" + $env:COMPUTERNAME.ToLowerInvariant().Replace('_','-')
$hostKey = if ($env:RECROOM_HOST_KEY) { [string]$env:RECROOM_HOST_KEY } else { "" }

if (Test-Path $Config) {
  $cfg = Get-Content $Config -Raw | ConvertFrom-Json
} else {
  $cfg = [pscustomobject]@{}
}
function Set-Property([string]$Name, $Value) {
  if ($cfg.PSObject.Properties[$Name]) { $cfg.$Name = $Value }
  else { $cfg | Add-Member -NotePropertyName $Name -NotePropertyValue $Value }
}

if (-not $hostKey -and $cfg.hostKey -and ([string]$cfg.hostKey) -notmatch '^SET_') {
  $hostKey = [string]$cfg.hostKey
}
if (-not $hostKey -and $PairingCode) {
  Write-Host "Claiming one-time Flux Windows host pairing code..." -ForegroundColor DarkCyan
  $pair = Invoke-RestMethod -Method Post -Uri ($server.TrimEnd('/') + "/api/recroom-public/host-pairing/claim") -ContentType "application/json" -Body (@{ pairingCode = $PairingCode } | ConvertTo-Json) -TimeoutSec 30
  if (-not $pair.ok -or -not $pair.hostKey) { throw "Flux host pairing did not return a host credential." }
  $hostKey = [string]$pair.hostKey
  Write-Host "Windows host paired with Flux." -ForegroundColor Green
}

Set-Property "server" $server
Set-Property "hostId" ($(if ($cfg.hostId) { [string]$cfg.hostId } else { $hostId }))
Set-Property "name" ($(if ($cfg.name) { [string]$cfg.name } else { "Ripo Windows Game Host" }))
if ($hostKey) { Set-Property "hostKey" $hostKey } elseif (-not $cfg.hostKey) { Set-Property "hostKey" "SET_RECROOM_HOST_KEY_ENV" }
Set-Property "clientDir" $clientDir
Set-Property "buildId" $TargetBuild
Set-Property "capacity" 1
Set-Property "streamUrl" ""
Set-Property "adapterCommand" 'node "%RECROOM_AGENT_DIR%\recroom-tools\host-proxy.mjs"'
Set-Property "streamStartCommand" 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%RECROOM_AGENT_DIR%\start-recroom-browser-stream.ps1"'
Set-Property "streamStopCommand" 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%RECROOM_AGENT_DIR%\stop-recroom-browser-stream.ps1"'
Set-Property "autoUpdate" $true
Set-Property "updateIntervalMinutes" 15
Set-Property "updateRepository" "riporipoteam-ctrl/ripoteamserver"
Set-Property "updateRef" "main"
Set-Property "toolsRepository" "riporipoteam-ctrl/recroomfluxgame"
Set-Property "toolsRef" "main"

$cfg | ConvertTo-Json -Depth 8 | Set-Content $Config -Encoding UTF8
Write-Host "Rec Room host configured with a strictly verified May 19 2022 client." -ForegroundColor Green
Write-Host "Client: $clientDir"
Write-Host "Config: $Config"
if (-not $hostKey -and ([string]$cfg.hostKey) -match '^SET_') {
  Write-Host "Generate a one-time Windows host pairing code from Flux Rec Room, then rerun this bootstrap with -PairingCode <code>." -ForegroundColor Yellow
}

if ($Start) {
  $startScript = Join-Path $PSScriptRoot "start-recroom-host.ps1"
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startScript -Config $Config
}

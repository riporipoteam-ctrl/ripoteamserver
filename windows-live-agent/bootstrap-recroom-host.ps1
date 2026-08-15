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

function Test-ClientLayout([string]$Root) {
  if (-not $Root -or -not (Test-Path $Root)) { return $false }
  $exe = @("RecRoom.exe", "Recroom_Release.exe") | ForEach-Object { Join-Path $Root $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $exe) { return $false }
  if (-not (Test-Path (Join-Path $Root "GameAssembly.dll"))) { return $false }
  $data = @("RecRoom_Data", "Recroom_Release_Data") | ForEach-Object { Join-Path $Root $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $data) { return $false }
  return Test-Path (Join-Path $data "il2cpp_data\Metadata\global-metadata.dat")
}

function Find-ClientDirectory {
  $candidates = New-Object System.Collections.Generic.List[string]
  if ($env:FLUX_RECROOM_CLIENT_DIR) { $candidates.Add([string]$env:FLUX_RECROOM_CLIENT_DIR) }
  if (Test-Path $Config) {
    try {
      $existing = Get-Content $Config -Raw | ConvertFrom-Json
      if ($existing.clientDir) { $candidates.Add([string]$existing.clientDir) }
    } catch {}
  }
  $candidates.Add($canonicalRoot)
  $candidates.Add("C:\Games\FluxRecRoom\May 19 2022")

  foreach ($candidate in $candidates | Select-Object -Unique) {
    if (Test-ClientLayout $candidate) { return (Resolve-Path $candidate).Path }
  }

  foreach ($root in @((Join-Path $env:USERPROFILE "Downloads"), (Join-Path $env:USERPROFILE "Desktop"))) {
    if (-not (Test-Path $root)) { continue }
    $exe = Get-ChildItem -Path $root -File -Recurse -ErrorAction SilentlyContinue |
      Where-Object {
        $_.Name -in @("RecRoom.exe", "Recroom_Release.exe") -and
        $_.DirectoryName -match '(?i)(8751857|2022|May.?19|Rec.?Room)'
      } | Select-Object -First 1
    if ($exe -and (Test-ClientLayout $exe.Directory.FullName)) { return $exe.Directory.FullName }
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

$clientDir = Find-ClientDirectory
if (-not $clientDir) {
  $archive = Find-TargetArchive
  if ($archive) {
    New-Item -ItemType Directory -Path $canonicalRoot -Force | Out-Null
    Expand-Archive -Path $archive -DestinationPath $canonicalRoot -Force
    if (Test-ClientLayout $canonicalRoot) {
      $clientDir = $canonicalRoot
    } else {
      $nestedExe = Get-ChildItem -Path $canonicalRoot -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -in @("RecRoom.exe", "Recroom_Release.exe") } | Select-Object -First 1
      if ($nestedExe -and (Test-ClientLayout $nestedExe.Directory.FullName)) { $clientDir = $nestedExe.Directory.FullName }
    }
  }
}

if (-not $clientDir -and $TrySteamDownload) {
  $downloadScript = Join-Path $PSScriptRoot "download-recroom-client.ps1"
  if (-not (Test-Path $downloadScript)) { throw "Missing $downloadScript" }
  Write-Host "No local May 2022 client found; trying the exact Steam depot with your own Steam account..." -ForegroundColor Cyan
  $downloaded = @(& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $downloadScript -SteamUsername $SteamUsername -Destination $canonicalRoot)
  if ($LASTEXITCODE -ne 0) { throw "Licensed Steam client download attempt failed." }
  $candidate = [string]($downloaded | Select-Object -Last 1)
  if ($candidate -and (Test-ClientLayout $candidate)) { $clientDir = $candidate }
  elseif (Test-ClientLayout $canonicalRoot) { $clientDir = $canonicalRoot }
}

if (-not $clientDir) {
  throw "May 19 2022 Rec Room client not found. Put your legally obtained build 8751857 folder/ZIP in Downloads/Desktop, set FLUX_RECROOM_CLIENT_DIR, or rerun bootstrap with -TrySteamDownload and your Steam username."
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
Write-Host "Rec Room host configured." -ForegroundColor Green
Write-Host "Client: $clientDir"
Write-Host "Config: $Config"
if (-not $hostKey -and ([string]$cfg.hostKey) -match '^SET_') {
  Write-Host "Generate a one-time Windows host pairing code from Flux Rec Room, then rerun this bootstrap with -PairingCode <code>." -ForegroundColor Yellow
}

if ($Start) {
  $startScript = Join-Path $PSScriptRoot "start-recroom-host.ps1"
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startScript -Config $Config
}

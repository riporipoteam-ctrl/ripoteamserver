param(
  [string]$Config = (Join-Path $PSScriptRoot "recroom-agent-config.json")
)

$ErrorActionPreference = "Stop"
$TargetBuild = "recroom-2022-05-19"
$script:GameProcess = $null
$script:AdapterProcess = $null
$script:ActiveSessionId = ""
$script:LastHeartbeat = [DateTimeOffset]::MinValue

function Load-Config {
  if (-not (Test-Path $Config)) {
    throw "Missing $Config. Copy recroom-agent-config.example.json to recroom-agent-config.json and edit it locally."
  }
  $cfg = Get-Content $Config -Raw | ConvertFrom-Json
  if ($env:RECROOM_HOST_KEY) { $cfg.hostKey = $env:RECROOM_HOST_KEY }
  if ($env:RECROOM_BROKER_URL) { $cfg.server = $env:RECROOM_BROKER_URL }
  if ($env:FLUX_RECROOM_CLIENT_DIR) { $cfg.clientDir = $env:FLUX_RECROOM_CLIENT_DIR }
  if ($env:FLUX_RECROOM_STREAM_URL) { $cfg.streamUrl = $env:FLUX_RECROOM_STREAM_URL }
  return $cfg
}

function Require-Config($cfg) {
  foreach ($name in @("server", "hostId", "hostKey", "clientDir")) {
    if (-not $cfg.$name) { throw "Rec Room agent config '$name' is required." }
  }
  if (($cfg.buildId) -and ([string]$cfg.buildId -ne $TargetBuild)) {
    throw "This agent currently targets $TargetBuild only."
  }
}

function Api-Get([string]$Path) {
  $headers = @{ "x-recroom-host-key" = [string]$script:cfg.hostKey }
  return Invoke-RestMethod -Method Get -Uri ($script:cfg.server.TrimEnd('/') + $Path) -Headers $headers -TimeoutSec 20
}

function Api-Post([string]$Path, $Body) {
  $headers = @{ "x-recroom-host-key" = [string]$script:cfg.hostKey }
  return Invoke-RestMethod -Method Post -Uri ($script:cfg.server.TrimEnd('/') + $Path) -Headers $headers -ContentType "application/json" -Body ($Body | ConvertTo-Json -Depth 8) -TimeoutSec 30
}

function Find-RecRoomExe {
  $root = [string]$script:cfg.clientDir
  if (-not (Test-Path $root)) { throw "Rec Room client folder does not exist: $root" }
  foreach ($name in @("RecRoom.exe", "Recroom_Release.exe")) {
    $path = Join-Path $root $name
    if (Test-Path $path) { return $path }
  }
  throw "No RecRoom.exe or Recroom_Release.exe found in $root"
}

function Verify-ClientLayout {
  $root = [string]$script:cfg.clientDir
  $exe = Find-RecRoomExe
  $assembly = Join-Path $root "GameAssembly.dll"
  if (-not (Test-Path $assembly)) { throw "GameAssembly.dll is missing from $root" }

  $dataCandidates = @(
    (Join-Path $root "RecRoom_Data"),
    (Join-Path $root "Recroom_Release_Data")
  )
  $data = $dataCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $data) { throw "RecRoom_Data / Recroom_Release_Data is missing from $root" }

  $metadata = Join-Path $data "il2cpp_data\Metadata\global-metadata.dat"
  if (-not (Test-Path $metadata)) { throw "IL2CPP global-metadata.dat is missing from $data" }

  return [pscustomobject]@{
    exe = $exe
    data = $data
    metadata = $metadata
  }
}

function Invoke-ConfiguredCommand([string]$Command) {
  if (-not $Command) { return $null }
  $expanded = [Environment]::ExpandEnvironmentVariables($Command)
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = "powershell.exe"
  $psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -Command `"$expanded`""
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $process = [System.Diagnostics.Process]::Start($psi)
  return $process
}

function Resolve-StreamUrl {
  if ($script:cfg.streamStartCommand) {
    $process = Invoke-ConfiguredCommand ([string]$script:cfg.streamStartCommand)
    if ($process) {
      if (-not $process.WaitForExit(20000)) {
        try { $process.Kill() } catch {}
        throw "streamStartCommand did not finish within 20 seconds."
      }
      if ($process.ExitCode -ne 0) {
        $err = $process.StandardError.ReadToEnd()
        throw "streamStartCommand failed: $err"
      }
      $candidate = $process.StandardOutput.ReadToEnd().Trim()
      if ($candidate) { return $candidate }
    }
  }
  return [string]$script:cfg.streamUrl
}

function Start-Adapter($job) {
  if (-not $script:cfg.adapterCommand) { return }

  $env:FLUX_RECROOM_GATEWAY_URL = [string]$job.gatewayUrl
  $env:FLUX_RECROOM_SESSION_TOKEN = [string]$job.recnetSessionToken
  $env:FLUX_RECROOM_CLIENT_DIR = [string]$script:cfg.clientDir
  $env:FLUX_RECROOM_BUILD = $TargetBuild

  $script:AdapterProcess = Invoke-ConfiguredCommand ([string]$script:cfg.adapterCommand)
  Start-Sleep -Milliseconds 700
  if ($script:AdapterProcess -and $script:AdapterProcess.HasExited -and $script:AdapterProcess.ExitCode -ne 0) {
    $err = $script:AdapterProcess.StandardError.ReadToEnd()
    throw "Rec Room adapter failed to start: $err"
  }
}

function Stop-CurrentSession {
  if ($script:GameProcess -and -not $script:GameProcess.HasExited) {
    try { Stop-Process -Id $script:GameProcess.Id -Force -ErrorAction SilentlyContinue } catch {}
  }
  if ($script:AdapterProcess -and -not $script:AdapterProcess.HasExited) {
    try { Stop-Process -Id $script:AdapterProcess.Id -Force -ErrorAction SilentlyContinue } catch {}
  }
  $script:GameProcess = $null
  $script:AdapterProcess = $null
  $script:ActiveSessionId = ""
}

function Fail-Session([string]$SessionId, [string]$Message) {
  try {
    [void](Api-Post "/api/recroom/hosts/$($script:cfg.hostId)/sessions/$SessionId/failed" @{
      error = $Message
    })
  } catch {
    Write-Host ("Could not report failure: " + $_.Exception.Message) -ForegroundColor Yellow
  }
}

function Start-Session($job) {
  if ($script:ActiveSessionId) {
    throw "Host is already serving session $($script:ActiveSessionId)."
  }

  $layout = Verify-ClientLayout
  $script:ActiveSessionId = [string]$job.sessionId

  try {
    # The adapter/proxy receives the verified Flux session token through local
    # environment only. It is never written to this repository or printed here.
    Start-Adapter $job

    $env:FLUX_RECNET_URL = [string]$job.gatewayUrl
    $env:FLUX_RECNET = [string]$job.gatewayUrl
    $env:FLUX_RECROOM_SESSION_TOKEN = [string]$job.recnetSessionToken
    $env:FLUX_RECROOM_BUILD = $TargetBuild
    $env:FLUX_PLAYER_ACCOUNT_ID = [string]$job.account.accountId
    $env:FLUX_PLAYER_USERNAME = [string]$job.account.username

    $args = @(
      "-screen-fullscreen", "0",
      "-screen-width", "1920",
      "-screen-height", "1080",
      "-force-d3d11"
    )
    $script:GameProcess = Start-Process -FilePath $layout.exe -WorkingDirectory ([string]$script:cfg.clientDir) -ArgumentList $args -PassThru

    Start-Sleep -Seconds 2
    if ($script:GameProcess.HasExited) {
      throw "Rec Room exited immediately with code $($script:GameProcess.ExitCode)."
    }

    $streamUrl = Resolve-StreamUrl
    if (-not $streamUrl -or -not ($streamUrl.StartsWith("https://") -or $env:RECROOM_ALLOW_HTTP_STREAMS -eq "1")) {
      throw "No HTTPS streamUrl is configured for this Windows host."
    }

    [void](Api-Post "/api/recroom/hosts/$($script:cfg.hostId)/sessions/$($job.sessionId)/ready" @{
      streamUrl = $streamUrl
      processId = $script:GameProcess.Id
      resolution = "1920x1080"
      streamer = "windows-host"
    })

    Write-Host "Rec Room session $($job.sessionId) is ready." -ForegroundColor Green
  } catch {
    $message = $_.Exception.Message
    Write-Host ("Rec Room start failed: " + $message) -ForegroundColor Red
    Fail-Session ([string]$job.sessionId) $message
    Stop-CurrentSession
  }
}

function Handle-Job($job) {
  if (-not $job) { return }
  $type = [string]$job.type
  switch ($type) {
    "start-session" { Start-Session $job; break }
    "stop-session" {
      if ([string]$job.sessionId -eq $script:ActiveSessionId) {
        Stop-CurrentSession
        Write-Host "Rec Room session stopped." -ForegroundColor Cyan
      }
      break
    }
    default { Write-Host "Unknown broker job: $type" -ForegroundColor Yellow }
  }
}

$script:cfg = Load-Config
Require-Config $script:cfg
$layout = Verify-ClientLayout
Write-Host "Flux Rec Room Windows Host" -ForegroundColor Cyan
Write-Host "Target build: May 19 2022 (8751857)"
Write-Host "Client: $($layout.exe)"
Write-Host "Broker: $($script:cfg.server)"

$register = Api-Post "/api/recroom/hosts/register" @{
  hostId = [string]$script:cfg.hostId
  name = [string]$script:cfg.name
  builds = @($TargetBuild)
  capacity = 1
  metadata = @{
    computer = $env:COMPUTERNAME
    os = [Environment]::OSVersion.VersionString
    clientDir = [string]$script:cfg.clientDir
  }
}
Write-Host "Registered host $($register.hostId)." -ForegroundColor Green

while ($true) {
  try {
    $now = [DateTimeOffset]::UtcNow
    if (($now - $script:LastHeartbeat).TotalSeconds -ge 10) {
      $gameRunning = [bool]($script:GameProcess -and -not $script:GameProcess.HasExited)
      [void](Api-Post "/api/recroom/hosts/$($script:cfg.hostId)/heartbeat" @{
        metadata = @{
          gameRunning = $gameRunning
          activeSessionId = $script:ActiveSessionId
        }
      })
      $script:LastHeartbeat = $now
    }

    if ($script:ActiveSessionId -and $script:GameProcess -and $script:GameProcess.HasExited) {
      $sid = $script:ActiveSessionId
      $code = $script:GameProcess.ExitCode
      Fail-Session $sid "Rec Room process exited unexpectedly with code $code."
      Stop-CurrentSession
    }

    $response = Api-Get "/api/recroom/hosts/$($script:cfg.hostId)/jobs"
    if ($response.job) { Handle-Job $response.job }
    Start-Sleep -Milliseconds 1000
  } catch {
    Write-Host ("Rec Room host reconnecting: " + $_.Exception.Message) -ForegroundColor Yellow
    Start-Sleep -Seconds 5
    try {
      [void](Api-Post "/api/recroom/hosts/register" @{
        hostId = [string]$script:cfg.hostId
        name = [string]$script:cfg.name
        builds = @($TargetBuild)
        capacity = 1
        metadata = @{ computer = $env:COMPUTERNAME }
      })
    } catch {}
  }
}

param(
  [string]$Config = (Join-Path $PSScriptRoot "recroom-agent-config.json")
)

$ErrorActionPreference = "Stop"
$TargetBuild = "recroom-2022-05-19"
$script:GameProcess = $null
$script:AdapterProcess = $null
$script:ActiveSessionId = ""
$script:LastHeartbeat = [DateTimeOffset]::MinValue
$script:RedirectState = $null

function Set-CfgProperty($cfg, [string]$Name, $Value) {
  if ($cfg.PSObject.Properties[$Name]) { $cfg.$Name = $Value }
  else { $cfg | Add-Member -NotePropertyName $Name -NotePropertyValue $Value }
}

function Load-Config {
  if (-not (Test-Path $Config)) { throw "Missing $Config. Run bootstrap-recroom-host.ps1 first." }
  $cfg = Get-Content $Config -Raw | ConvertFrom-Json
  if ($env:RECROOM_HOST_KEY) { Set-CfgProperty $cfg "hostKey" $env:RECROOM_HOST_KEY }
  if ($env:RECROOM_BROKER_URL) { Set-CfgProperty $cfg "server" $env:RECROOM_BROKER_URL }
  if ($env:FLUX_RECROOM_CLIENT_DIR) { Set-CfgProperty $cfg "clientDir" $env:FLUX_RECROOM_CLIENT_DIR }
  if ($env:FLUX_RECROOM_STREAM_URL) { Set-CfgProperty $cfg "streamUrl" $env:FLUX_RECROOM_STREAM_URL }
  if (-not $cfg.adapterCommand) { Set-CfgProperty $cfg "adapterCommand" 'node "%RECROOM_AGENT_DIR%\recroom-tools\host-proxy.mjs"' }
  if (-not $cfg.streamStartCommand -and -not $cfg.streamUrl) { Set-CfgProperty $cfg "streamStartCommand" 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%RECROOM_AGENT_DIR%\start-recroom-browser-stream.ps1"' }
  if (-not $cfg.streamStopCommand) { Set-CfgProperty $cfg "streamStopCommand" 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%RECROOM_AGENT_DIR%\stop-recroom-browser-stream.ps1"' }
  return $cfg
}

function Require-Config($cfg) {
  foreach ($name in @("server", "hostId", "hostKey")) { if (-not $cfg.$name) { throw "Rec Room agent config '$name' is required." } }
  if ([string]$cfg.hostKey -match '^SET_') { throw "RECROOM_HOST_KEY is not configured on this Windows host." }
  if (($cfg.buildId) -and ([string]$cfg.buildId -ne $TargetBuild)) { throw "This agent currently targets $TargetBuild only." }
}

function Test-ClientLayoutAt([string]$Root) {
  if (-not $Root -or -not (Test-Path $Root)) { return $false }
  $exe = @("RecRoom.exe", "Recroom_Release.exe") | ForEach-Object { Join-Path $Root $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $exe -or -not (Test-Path (Join-Path $Root "GameAssembly.dll"))) { return $false }
  $data = @("RecRoom_Data", "Recroom_Release_Data") | ForEach-Object { Join-Path $Root $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $data) { return $false }
  return Test-Path (Join-Path $data "il2cpp_data\Metadata\global-metadata.dat")
}

function Resolve-ClientDir {
  $candidates = New-Object System.Collections.Generic.List[string]
  if ($env:FLUX_RECROOM_CLIENT_DIR) { $candidates.Add([string]$env:FLUX_RECROOM_CLIENT_DIR) }
  if ($script:cfg.clientDir) { $candidates.Add([string]$script:cfg.clientDir) }
  if ($env:LOCALAPPDATA) { $candidates.Add((Join-Path $env:LOCALAPPDATA "FluxRecRoom\May 19 2022")) }
  $candidates.Add("C:\Games\FluxRecRoom\May 19 2022")
  foreach ($candidate in $candidates | Select-Object -Unique) {
    if (Test-ClientLayoutAt $candidate) {
      $resolved = (Resolve-Path $candidate).Path
      Set-CfgProperty $script:cfg "clientDir" $resolved
      return $resolved
    }
  }
  foreach ($root in @((Join-Path $env:USERPROFILE "Downloads"), (Join-Path $env:USERPROFILE "Desktop"))) {
    if (-not (Test-Path $root)) { continue }
    $exe = Get-ChildItem -Path $root -File -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Name -in @("RecRoom.exe", "Recroom_Release.exe") -and $_.DirectoryName -match '(?i)(8751857|2022|May.?19|Rec.?Room)' } | Select-Object -First 1
    if ($exe -and (Test-ClientLayoutAt $exe.Directory.FullName)) {
      $resolved = $exe.Directory.FullName
      Set-CfgProperty $script:cfg "clientDir" $resolved
      return $resolved
    }
  }
  throw "May 19 2022 Rec Room client was not found. Run bootstrap-recroom-host.ps1 or set FLUX_RECROOM_CLIENT_DIR to your legally obtained build 8751857 folder."
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
  $root = Resolve-ClientDir
  foreach ($name in @("RecRoom.exe", "Recroom_Release.exe")) {
    $path = Join-Path $root $name
    if (Test-Path $path) { return $path }
  }
  throw "No RecRoom.exe or Recroom_Release.exe found in $root"
}

function Verify-ClientLayout {
  $root = Resolve-ClientDir
  $exe = Find-RecRoomExe
  $assembly = Join-Path $root "GameAssembly.dll"
  if (-not (Test-Path $assembly)) { throw "GameAssembly.dll is missing from $root" }
  $data = @((Join-Path $root "RecRoom_Data"), (Join-Path $root "Recroom_Release_Data")) | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $data) { throw "RecRoom_Data / Recroom_Release_Data is missing from $root" }
  $metadata = Join-Path $data "il2cpp_data\Metadata\global-metadata.dat"
  if (-not (Test-Path $metadata)) { throw "IL2CPP global-metadata.dat is missing from $data" }
  return [pscustomobject]@{ exe = $exe; data = $data; metadata = $metadata }
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
  return [System.Diagnostics.Process]::Start($psi)
}

function Stop-ProcessTree($Process) {
  if (-not $Process) { return }
  try { $Process.Refresh() } catch {}
  if ($Process.HasExited) { return }
  try {
    $taskkill = Get-Command taskkill.exe -ErrorAction SilentlyContinue
    if ($taskkill) {
      & $taskkill.Source /PID ([string]$Process.Id) /T /F 2>$null | Out-Null
    } else {
      Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    }
  } catch {
    try { Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue } catch {}
  }
}

function Prepare-ClientRedirect {
  $root = Resolve-ClientDir
  $tool = Join-Path $PSScriptRoot "recroom-tools\redirect-client-urls.mjs"
  if (-not (Test-Path $tool)) { throw "Missing Rec Room client redirect tool: $tool. Run update-recroom-host.ps1." }
  if (-not (Get-Command node.exe -ErrorAction SilentlyContinue) -and -not (Get-Command node -ErrorAction SilentlyContinue)) { throw "Node.js is required to prepare the Rec Room client redirect." }
  $env:FLUX_RECROOM_LOCAL_BASE = "http://127.0.0.1:81"
  $process = Invoke-ConfiguredCommand "node `"$tool`" --root `"$root`""
  if (-not $process.WaitForExit(120000)) { Stop-ProcessTree $process; throw "Rec Room client redirect scan exceeded 120 seconds." }
  $stdout = $process.StandardOutput.ReadToEnd(); $stderr = $process.StandardError.ReadToEnd()
  if ($process.ExitCode -ne 0) { throw "Rec Room client redirect could not be verified (exit $($process.ExitCode)): $stderr" }
  try { $state = $stdout | ConvertFrom-Json } catch { throw "Rec Room client redirect returned invalid diagnostics: $stdout" }
  if (-not $state.ok -or [int]$state.preparedOccurrences -le 0) { throw "Rec Room client has no verified local rec.net redirects. Refusing to advertise it as playable." }
  $script:RedirectState = $state
  Write-Host "Verified Rec Room local redirects: $($state.preparedOccurrences) occurrence(s) across $($state.inspectedFiles) inspected files." -ForegroundColor Green
  return $state
}

function Resolve-StreamUrl {
  if ($script:cfg.streamStartCommand) {
    $process = Invoke-ConfiguredCommand ([string]$script:cfg.streamStartCommand)
    if ($process) {
      if (-not $process.WaitForExit(60000)) { Stop-ProcessTree $process; throw "streamStartCommand did not finish within 60 seconds." }
      $stdout = $process.StandardOutput.ReadToEnd(); $stderr = $process.StandardError.ReadToEnd()
      if ($process.ExitCode -ne 0) { throw "streamStartCommand failed: $stderr" }
      $matches = [regex]::Matches($stdout, 'https://[^\s"''<>]+')
      if ($matches.Count -gt 0) { return $matches[$matches.Count - 1].Value.Trim() }
      if ($stdout.Trim()) { return $stdout.Trim() }
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
  $env:FLUX_RECROOM_PROXY_PORT = "81"
  $script:AdapterProcess = Invoke-ConfiguredCommand ([string]$script:cfg.adapterCommand)
  Start-Sleep -Milliseconds 900
  if ($script:AdapterProcess -and $script:AdapterProcess.HasExited -and $script:AdapterProcess.ExitCode -ne 0) {
    $err = $script:AdapterProcess.StandardError.ReadToEnd()
    throw "Rec Room adapter failed to start: $err"
  }
  try {
    $health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:81/flux/local-health" -TimeoutSec 5
    if (-not $health.ok) { throw "proxy health returned not-ok" }
  } catch { throw "Rec Room local proxy is not reachable on port 81: $($_.Exception.Message)" }
}

function Stop-Stream {
  if (-not $script:cfg.streamStopCommand) { return }
  try {
    $process = Invoke-ConfiguredCommand ([string]$script:cfg.streamStopCommand)
    if ($process -and -not $process.WaitForExit(15000)) { Stop-ProcessTree $process }
  } catch { Write-Host ("Could not stop browser stream cleanly: " + $_.Exception.Message) -ForegroundColor Yellow }
}

function Stop-CurrentSession {
  Stop-ProcessTree $script:GameProcess
  Stop-ProcessTree $script:AdapterProcess
  Stop-Stream
  $script:GameProcess = $null
  $script:AdapterProcess = $null
  $script:ActiveSessionId = ""
  Remove-Item Env:FLUX_RECROOM_GAME_PID -ErrorAction SilentlyContinue
}

function Fail-Session([string]$SessionId, [string]$Message) {
  try { [void](Api-Post "/api/recroom/hosts/$($script:cfg.hostId)/sessions/$SessionId/failed" @{ error = $Message }) }
  catch { Write-Host ("Could not report failure: " + $_.Exception.Message) -ForegroundColor Yellow }
}

function Start-Session($job) {
  if ($script:ActiveSessionId) { throw "Host is already serving session $($script:ActiveSessionId)." }
  $layout = Verify-ClientLayout
  $script:ActiveSessionId = [string]$job.sessionId
  try {
    [void](Prepare-ClientRedirect)
    Start-Adapter $job
    $env:FLUX_RECNET_URL = "http://127.0.0.1:81"
    $env:FLUX_RECNET = "http://127.0.0.1:81"
    $env:FLUX_RECROOM_SESSION_TOKEN = [string]$job.recnetSessionToken
    $env:FLUX_RECROOM_BUILD = $TargetBuild
    $env:FLUX_PLAYER_ACCOUNT_ID = [string]$job.account.accountId
    $env:FLUX_PLAYER_USERNAME = [string]$job.account.username
    $script:GameProcess = Start-Process -FilePath $layout.exe -WorkingDirectory ([string]$script:cfg.clientDir) -ArgumentList @("-screen-fullscreen", "0", "-screen-width", "1920", "-screen-height", "1080", "-force-d3d11") -PassThru
    $env:FLUX_RECROOM_GAME_PID = [string]$script:GameProcess.Id
    Start-Sleep -Seconds 2
    if ($script:GameProcess.HasExited) { throw "Rec Room exited immediately with code $($script:GameProcess.ExitCode)." }
    $streamUrl = Resolve-StreamUrl
    if (-not $streamUrl -or -not ($streamUrl.StartsWith("https://") -or $env:RECROOM_ALLOW_HTTP_STREAMS -eq "1")) { throw "No HTTPS browser stream could be started for this Windows host." }
    [void](Api-Post "/api/recroom/hosts/$($script:cfg.hostId)/sessions/$($job.sessionId)/ready" @{
      streamUrl = $streamUrl; processId = $script:GameProcess.Id; resolution = "1920x1080"; streamer = "flux-browser-control-v1.2"; redirectOccurrences = [int]$script:RedirectState.preparedOccurrences; proxyPort = 81
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
  switch ([string]$job.type) {
    "start-session" { Start-Session $job; break }
    "stop-session" {
      if ([string]$job.sessionId -eq $script:ActiveSessionId) {
        Stop-CurrentSession
        Write-Host "Rec Room session stopped." -ForegroundColor Cyan
      }
      break
    }
    default { Write-Host "Unknown broker job: $($job.type)" -ForegroundColor Yellow }
  }
}

$script:cfg = Load-Config
Require-Config $script:cfg
$layout = Verify-ClientLayout
$redirect = Prepare-ClientRedirect
$capacity = if ($script:cfg.capacity) { [Math]::Max(1, [Math]::Min(8, [int]$script:cfg.capacity)) } else { 1 }
Write-Host "Flux Rec Room Windows Host" -ForegroundColor Cyan
Write-Host "Target build: May 19 2022 (8751857)"
Write-Host "Client: $($layout.exe)"
Write-Host "Broker: $($script:cfg.server)"
Write-Host "Client redirect: verified ($($redirect.preparedOccurrences) local endpoint occurrence(s))"
Write-Host "Browser stream: automatic HTTPS tunnel when a session starts"

$register = Api-Post "/api/recroom/hosts/register" @{
  hostId = [string]$script:cfg.hostId; name = [string]$script:cfg.name; builds = @($TargetBuild); capacity = $capacity
  metadata = @{ computer = $env:COMPUTERNAME; os = [Environment]::OSVersion.VersionString; clientDir = [string]$script:cfg.clientDir; browserStream = $true; touchControls = $true; targetSteamBuild = "8751857"; redirectReady = $true; redirectOccurrences = [int]$redirect.preparedOccurrences; localProxyPort = 81 }
}
Write-Host "Registered host $($register.hostId)." -ForegroundColor Green

while ($true) {
  try {
    $now = [DateTimeOffset]::UtcNow
    if (($now - $script:LastHeartbeat).TotalSeconds -ge 10) {
      $gameRunning = [bool]($script:GameProcess -and -not $script:GameProcess.HasExited)
      [void](Api-Post "/api/recroom/hosts/$($script:cfg.hostId)/heartbeat" @{
        metadata = @{ gameRunning = $gameRunning; activeSessionId = $script:ActiveSessionId; clientReady = $true; redirectReady = $true; redirectOccurrences = [int]$script:RedirectState.preparedOccurrences; browserStream = $true; touchControls = $true }
      })
      $script:LastHeartbeat = $now
    }
    if ($script:ActiveSessionId -and $script:GameProcess -and $script:GameProcess.HasExited) {
      $sid = $script:ActiveSessionId; $code = $script:GameProcess.ExitCode
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
        hostId = [string]$script:cfg.hostId; name = [string]$script:cfg.name; builds = @($TargetBuild); capacity = $capacity
        metadata = @{ computer = $env:COMPUTERNAME; clientReady = $true; redirectReady = $true; redirectOccurrences = [int]$script:RedirectState.preparedOccurrences; browserStream = $true; touchControls = $true }
      })
    } catch {}
  }
}

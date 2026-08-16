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
$script:ClientIdentity = $null

function Set-CfgProperty($cfg, [string]$Name, $Value) {
  if ($cfg.PSObject.Properties[$Name]) { $cfg.$Name = $Value }
  else { $cfg | Add-Member -NotePropertyName $Name -NotePropertyValue $Value }
}

function Save-Config($cfg) {
  $cfg | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Config -Encoding UTF8
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

function Ensure-ClientIdentifier {
  $finder = Join-Path $PSScriptRoot "identify-recroom-client.ps1"
  if (Test-Path -LiteralPath $finder -PathType Leaf) { return $finder }

  # Older host installations did not ship the identifier. The old updater
  # already refreshes recroom-agent.ps1, so bootstrap the one missing helper
  # here to make strict build validation self-healing without a reinstall.
  $repo = if ($script:cfg.updateRepository) { [string]$script:cfg.updateRepository } else { "riporipoteam-ctrl/ripoteamserver" }
  $ref = if ($script:cfg.updateRef) { [string]$script:cfg.updateRef } else { "main" }
  if ($repo -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw "Invalid updateRepository while retrieving client identifier." }
  if ($ref -notmatch '^[A-Za-z0-9_./-]+$') { throw "Invalid updateRef while retrieving client identifier." }
  $url = "https://raw.githubusercontent.com/$repo/$ref/windows-live-agent/identify-recroom-client.ps1"
  Write-Host "Strict Rec Room client identifier is missing; retrieving it from the configured host repository..." -ForegroundColor DarkCyan
  Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $finder -TimeoutSec 60
  if (-not (Test-Path -LiteralPath $finder -PathType Leaf) -or (Get-Item -LiteralPath $finder).Length -le 0) {
    throw "Could not retrieve strict Rec Room client identifier."
  }
  $tokens = $null; $errors = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile($finder, [ref]$tokens, [ref]$errors)
  if ($errors.Count -gt 0) {
    Remove-Item -LiteralPath $finder -Force -ErrorAction SilentlyContinue
    throw "Downloaded strict Rec Room client identifier failed PowerShell syntax validation."
  }
  return $finder
}

function Invoke-ClientIdentifier([string]$Candidate = "") {
  $finder = Ensure-ClientIdentifier
  $stdout = Join-Path $env:TEMP ("flux-recroom-identify-" + [guid]::NewGuid().ToString("N") + ".json")
  $stderr = "$stdout.err"
  try {
    $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$finder`"", "-AsJson")
    if ($Candidate) { $args += @("-Root", "`"$Candidate`"") } else { $args += "-Scan" }
    $process = Start-Process -FilePath "powershell.exe" -ArgumentList $args -Wait -PassThru -NoNewWindow -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    if ($process.ExitCode -ne 0) {
      $err = Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue
      throw "Rec Room client identifier failed with exit $($process.ExitCode): $err"
    }
    $raw = Get-Content -LiteralPath $stdout -Raw
    try { return $raw | ConvertFrom-Json } catch { throw "Rec Room client identifier returned invalid JSON: $raw" }
  } finally {
    Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
  }
}

function Resolve-ClientDir {
  if ($script:ClientIdentity -and $script:ClientIdentity.playableBy2022Agent) {
    return [string]$script:ClientIdentity.root
  }

  $candidate = ""
  if ($env:FLUX_RECROOM_CLIENT_DIR) { $candidate = [string]$env:FLUX_RECROOM_CLIENT_DIR }
  elseif ($script:cfg.clientDir) { $candidate = [string]$script:cfg.clientDir }

  $scan = Invoke-ClientIdentifier $candidate
  $target = @($scan.clients | Where-Object { $_.kind -eq "target-2022" -and $_.playableBy2022Agent }) | Select-Object -First 1
  if ($target) {
    $resolved = [string]$target.root
    $script:ClientIdentity = $target
    $env:FLUX_RECROOM_CLIENT_DIR = $resolved
    if ([string]$script:cfg.clientDir -ne $resolved) {
      Set-CfgProperty $script:cfg "clientDir" $resolved
      Save-Config $script:cfg
    }
    Write-Host "Strict client identity accepted: build 8751857 / manifest 6337851004861751095 at $resolved" -ForegroundColor Green
    return $resolved
  }

  $exact2023 = @($scan.clients | Where-Object { $_.kind -eq "fluxrec-2023" }) | Select-Object -First 1
  if ($exact2023) {
    throw "Exact FluxRec March 7 2023 build 10679392 detected at '$($exact2023.root)'. This agent targets May 19 2022 and will not patch a different IL2CPP build as 2022."
  }
  $claimed2023 = @($scan.clients | Where-Object { $_.kind -eq "unverified-2023" }) | Select-Object -First 1
  if ($claimed2023) {
    throw "A folder claims the March 2023 Rec Room build at '$($claimed2023.root)', but its pinned FluxRec hashes do not match. Refusing to launch it."
  }
  $unknown = @($scan.clients | Where-Object { $_.kind -eq "unknown" }) | Select-Object -First 1
  if ($unknown) {
    throw "Unknown Rec Room client at '$($unknown.root)' (exe SHA256 $($unknown.fingerprint.exeSha256)). It is not verified as build 8751857, so this host will not advertise it as playable."
  }
  throw "No verified May 19 2022 Rec Room client was found. Set FLUX_RECROOM_CLIENT_DIR/clientDir to the legally obtained build 8751857 folder, or place that build under an exact 8751857 / 6337851004861751095 / May 19 2022 path."
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
Write-Host "Client identity: $($script:ClientIdentity.confidence) / manifest $($script:ClientIdentity.manifestId)"
Write-Host "Broker: $($script:cfg.server)"
Write-Host "Client redirect: verified ($($redirect.preparedOccurrences) local endpoint occurrence(s))"
Write-Host "Browser stream: automatic HTTPS tunnel when a session starts"

$register = Api-Post "/api/recroom/hosts/register" @{
  hostId = [string]$script:cfg.hostId; name = [string]$script:cfg.name; builds = @($TargetBuild); capacity = $capacity
  metadata = @{ computer = $env:COMPUTERNAME; os = [Environment]::OSVersion.VersionString; clientDir = [string]$script:cfg.clientDir; browserStream = $true; touchControls = $true; targetSteamBuild = "8751857"; targetManifest = "6337851004861751095"; identityConfidence = [string]$script:ClientIdentity.confidence; exeSha256 = [string]$script:ClientIdentity.fingerprint.exeSha256; redirectReady = $true; redirectOccurrences = [int]$redirect.preparedOccurrences; localProxyPort = 81 }
}
Write-Host "Registered host $($register.hostId)." -ForegroundColor Green

while ($true) {
  try {
    $now = [DateTimeOffset]::UtcNow
    if (($now - $script:LastHeartbeat).TotalSeconds -ge 10) {
      $gameRunning = [bool]($script:GameProcess -and -not $script:GameProcess.HasExited)
      [void](Api-Post "/api/recroom/hosts/$($script:cfg.hostId)/heartbeat" @{
        metadata = @{ gameRunning = $gameRunning; activeSessionId = $script:ActiveSessionId; clientReady = $true; clientIdentity = [string]$script:ClientIdentity.confidence; clientExeSha256 = [string]$script:ClientIdentity.fingerprint.exeSha256; redirectReady = $true; redirectOccurrences = [int]$script:RedirectState.preparedOccurrences; browserStream = $true; touchControls = $true }
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
        metadata = @{ computer = $env:COMPUTERNAME; clientReady = $true; clientIdentity = [string]$script:ClientIdentity.confidence; clientExeSha256 = [string]$script:ClientIdentity.fingerprint.exeSha256; redirectReady = $true; redirectOccurrences = [int]$script:RedirectState.preparedOccurrences; browserStream = $true; touchControls = $true }
      })
    } catch {}
  }
}

param(
  [string]$ClientDir = $env:FLUX_RECROOM_CLIENT_DIR,
  [string]$GatewayUrl = $env:FLUX_RECROOM_GATEWAY_URL,
  [string]$SessionToken = $env:FLUX_RECROOM_SESSION_TOKEN,
  [int]$DurationSeconds = 35,
  [string]$OutputDir = "",
  [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"
$startedAt = [DateTimeOffset]::UtcNow
if (-not $OutputDir) {
  $OutputDir = Join-Path $env:TEMP ("FluxRecRoomPlaytest-" + $startedAt.ToString("yyyyMMdd-HHmmss"))
}
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$report = [ordered]@{
  ok = $false
  targetBuild = "recroom-2022-05-19"
  steamBuild = "8751857"
  startedAt = $startedAt.ToString("o")
  clientDir = ""
  executable = ""
  redirectOccurrences = 0
  proxyReady = $false
  gamePid = 0
  windowReady = $false
  streamReady = $false
  streamUrl = ""
  checkpoints = @()
  inputsSent = @()
  processExited = $false
  processExitCode = $null
  unityLogs = @()
  errors = @()
  finishedAt = $null
}

$proxyProcess = $null
$gameProcess = $null
$captureProcess = $null

function Save-Report {
  $report.finishedAt = [DateTimeOffset]::UtcNow.ToString("o")
  $path = Join-Path $OutputDir "playtest-report.json"
  $report | ConvertTo-Json -Depth 10 | Set-Content $path -Encoding UTF8
  return $path
}

function Require-Layout([string]$Root) {
  if (-not $Root -or -not (Test-Path $Root)) { throw "Rec Room client directory does not exist: $Root" }
  $resolved = (Resolve-Path $Root).Path
  $exe = @("RecRoom.exe", "Recroom_Release.exe") | ForEach-Object { Join-Path $resolved $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $exe) { throw "RecRoom.exe / Recroom_Release.exe is missing from $resolved" }
  if (-not (Test-Path (Join-Path $resolved "GameAssembly.dll"))) { throw "GameAssembly.dll is missing from $resolved" }
  $data = @("RecRoom_Data", "Recroom_Release_Data") | ForEach-Object { Join-Path $resolved $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $data) { throw "Rec Room Unity data directory is missing from $resolved" }
  $metadata = Join-Path $data "il2cpp_data\Metadata\global-metadata.dat"
  if (-not (Test-Path $metadata)) { throw "global-metadata.dat is missing from $data" }
  return [pscustomobject]@{ Root = $resolved; Exe = $exe; Data = $data; Metadata = $metadata }
}

function Run-NodeJson([string]$Script, [string[]]$Arguments) {
  $node = Get-Command node.exe -ErrorAction SilentlyContinue
  if (-not $node) { $node = Get-Command node -ErrorAction SilentlyContinue }
  if (-not $node) { throw "Node.js is required for the Rec Room playtest." }
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $node.Source
  $quoted = @("`"$Script`"") + ($Arguments | ForEach-Object { if ($_ -match '\s') { "`"$_`"" } else { $_ } })
  $psi.Arguments = ($quoted -join " ")
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $p = [System.Diagnostics.Process]::Start($psi)
  if (-not $p.WaitForExit(120000)) { try { $p.Kill() } catch {}; throw "Node helper timed out: $Script" }
  $stdout = $p.StandardOutput.ReadToEnd()
  $stderr = $p.StandardError.ReadToEnd()
  if ($p.ExitCode -ne 0) { throw "Node helper failed ($($p.ExitCode)): $stderr" }
  try { return $stdout | ConvertFrom-Json } catch { throw "Node helper returned invalid JSON: $stdout" }
}

function Start-Proxy([string]$ProxyScript, [string]$Gateway, [string]$Token) {
  if (-not $Gateway) { throw "GatewayUrl / FLUX_RECROOM_GATEWAY_URL is required for a full Rec Room playtest." }
  if (-not $Token) { throw "SessionToken / FLUX_RECROOM_SESSION_TOKEN is required for a full Rec Room playtest." }
  $node = Get-Command node.exe -ErrorAction SilentlyContinue
  if (-not $node) { $node = Get-Command node -ErrorAction SilentlyContinue }
  if (-not $node) { throw "Node.js is required for the Rec Room proxy." }
  $env:FLUX_RECROOM_GATEWAY_URL = $Gateway
  $env:FLUX_RECROOM_SESSION_TOKEN = $Token
  $env:FLUX_RECROOM_PROXY_PORT = "81"
  $env:FLUX_RECROOM_PROXY_LOG = "1"
  $stdout = Join-Path $OutputDir "host-proxy.out.log"
  $stderr = Join-Path $OutputDir "host-proxy.err.log"
  $p = Start-Process -FilePath $node.Source -ArgumentList @("`"$ProxyScript`"") -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
  for ($i = 0; $i -lt 40; $i++) {
    if ($p.HasExited) {
      $detail = ((Get-Content $stderr -Raw -ErrorAction SilentlyContinue) + "`n" + (Get-Content $stdout -Raw -ErrorAction SilentlyContinue)).Trim()
      throw "Rec Room proxy exited before health check: $detail"
    }
    try {
      $health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:81/flux/local-health" -TimeoutSec 2
      if ($health.ok) { return $p }
    } catch {}
    Start-Sleep -Milliseconds 250
  }
  throw "Rec Room proxy never became healthy on port 81."
}

function Start-LocalCapture([int]$GamePid) {
  $python = $null
  foreach ($candidate in @("python.exe", "python3.exe", "py.exe")) {
    $command = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($command) { $python = $command.Source; break }
  }
  if (-not $python) { throw "Python 3 is required for the Rec Room playtest capture server." }
  $prefix = @()
  if ((Split-Path $python -Leaf).ToLowerInvariant() -eq "py.exe") { $prefix = @("-3") }
  $check = Start-Process -FilePath $python -ArgumentList @($prefix + @("-c", '"from PIL import ImageGrab"')) -Wait -PassThru -NoNewWindow
  if ($check.ExitCode -ne 0) {
    $install = Start-Process -FilePath $python -ArgumentList @($prefix + @("-m", "pip", "install", "--disable-pip-version-check", "Pillow>=10,<12")) -Wait -PassThru -NoNewWindow
    if ($install.ExitCode -ne 0) { throw "Could not install Pillow for the Rec Room playtest capture server." }
  }

  $script = Join-Path $PSScriptRoot "recroom-web-stream.py"
  if (-not (Test-Path $script)) { throw "Missing Rec Room capture server: $script" }
  $bytes = New-Object byte[] 32
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  $token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
  $stdout = Join-Path $OutputDir "capture-server.out.log"
  $stderr = Join-Path $OutputDir "capture-server.err.log"
  $args = @($prefix + @("`"$script`"", "--pid", [string]$GamePid, "--port", "6081", "--token", $token, "--max-width", "1280", "--quality", "72"))
  $process = Start-Process -FilePath $python -ArgumentList $args -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
  for ($i = 0; $i -lt 80; $i++) {
    if ($process.HasExited) {
      $detail = ((Get-Content $stderr -Raw -ErrorAction SilentlyContinue) + "`n" + (Get-Content $stdout -Raw -ErrorAction SilentlyContinue)).Trim()
      throw "Rec Room capture server exited before finding the game window: $detail"
    }
    try {
      $health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:6081/health" -TimeoutSec 2
      if ($health.ok) {
        return [pscustomobject]@{
          Process = $process
          Token = $token
          Url = "http://127.0.0.1:6081/?token=$([Uri]::EscapeDataString($token))"
        }
      }
    } catch {}
    Start-Sleep -Milliseconds 250
  }
  try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch {}
  throw "Rec Room capture server could not find a visible game window."
}

function Send-Input([string]$InputUrl, $Payload) {
  Invoke-RestMethod -Method Post -Uri $InputUrl -ContentType "application/json" -Body ($Payload | ConvertTo-Json -Compress) -TimeoutSec 10 | Out-Null
  $report.inputsSent += [ordered]@{ at = [DateTimeOffset]::UtcNow.ToString("o"); payload = $Payload }
}

function Capture-Checkpoint([string]$FrameUrl, [string]$Name) {
  $path = Join-Path $OutputDir "$Name.jpg"
  Invoke-WebRequest -UseBasicParsing -Uri ($FrameUrl + "&t=" + [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()) -OutFile $path -TimeoutSec 15
  $hash = (Get-FileHash -Path $path -Algorithm SHA256).Hash.ToLowerInvariant()
  $report.checkpoints += [ordered]@{
    name = $Name
    at = [DateTimeOffset]::UtcNow.ToString("o")
    file = $path
    bytes = (Get-Item $path).Length
    sha256 = $hash
  }
}

function Collect-UnityLogs([DateTimeOffset]$Since) {
  $roots = @(
    (Join-Path $env:USERPROFILE "AppData\LocalLow\Against Gravity\Rec Room"),
    (Join-Path $env:USERPROFILE "AppData\LocalLow\Against Gravity Corp\Rec Room"),
    (Join-Path $env:USERPROFILE "AppData\LocalLow\Rec Room"),
    (Join-Path $env:LOCALAPPDATA "Rec Room"),
    $report.clientDir
  ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique
  $matches = @()
  foreach ($root in $roots) {
    $matches += Get-ChildItem -Path $root -Filter "Player.log" -File -Recurse -ErrorAction SilentlyContinue |
      Where-Object { $_.LastWriteTimeUtc -ge $Since.UtcDateTime.AddMinutes(-2) } |
      Sort-Object LastWriteTimeUtc -Descending |
      Select-Object -First 4
  }
  $copied = @()
  $index = 0
  foreach ($file in $matches | Select-Object -Unique) {
    $index++
    $dest = Join-Path $OutputDir ("Unity-Player-$index.log")
    Copy-Item $file.FullName $dest -Force
    $copied += $dest
  }
  return $copied
}

try {
  $layout = Require-Layout $ClientDir
  $report.clientDir = $layout.Root
  $report.executable = $layout.Exe
  $tools = Join-Path $PSScriptRoot "recroom-tools"
  $redirectTool = Join-Path $tools "redirect-client-urls.mjs"
  $proxyTool = Join-Path $tools "host-proxy.mjs"
  if (-not (Test-Path $redirectTool)) { throw "Missing $redirectTool. Run update-recroom-host.ps1 first." }
  if (-not (Test-Path $proxyTool)) { throw "Missing $proxyTool. Run update-recroom-host.ps1 first." }

  $env:FLUX_RECROOM_LOCAL_BASE = "http://127.0.0.1:81"
  $redirect = Run-NodeJson $redirectTool @("--root", $layout.Root)
  if (-not $redirect.ok -or [int]$redirect.preparedOccurrences -le 0) { throw "Client redirect could not be verified." }
  $report.redirectOccurrences = [int]$redirect.preparedOccurrences

  $proxyProcess = Start-Proxy $proxyTool $GatewayUrl $SessionToken
  $report.proxyReady = $true
  $env:FLUX_RECNET_URL = "http://127.0.0.1:81"
  $env:FLUX_RECNET = "http://127.0.0.1:81"
  $env:FLUX_RECROOM_BUILD = "recroom-2022-05-19"

  $gameProcess = Start-Process -FilePath $layout.Exe -WorkingDirectory $layout.Root -ArgumentList @(
    "-screen-fullscreen", "0", "-screen-width", "1280", "-screen-height", "720", "-force-d3d11"
  ) -PassThru
  $report.gamePid = $gameProcess.Id
  $env:FLUX_RECROOM_GAME_PID = [string]$gameProcess.Id
  Start-Sleep -Seconds 2
  if ($gameProcess.HasExited) { throw "Rec Room exited immediately with code $($gameProcess.ExitCode)." }

  $capture = Start-LocalCapture $gameProcess.Id
  $captureProcess = $capture.Process
  $report.streamReady = $true
  $report.streamUrl = $capture.Url
  $report.windowReady = $true
  $encoded = [Uri]::EscapeDataString($capture.Token)
  $frameUrl = "http://127.0.0.1:6081/frame.jpg?token=$encoded"
  $inputUrl = "http://127.0.0.1:6081/input?token=$encoded"

  Capture-Checkpoint $frameUrl "00-launch"
  Start-Sleep -Seconds 4
  Capture-Checkpoint $frameUrl "01-after-4s"
  Send-Input $inputUrl @{ type = "button"; button = "left"; down = $true; x = 0.5; y = 0.5 }
  Send-Input $inputUrl @{ type = "button"; button = "left"; down = $false }
  Send-Input $inputUrl @{ type = "key"; key = "w"; down = $true }
  Start-Sleep -Seconds 2
  Send-Input $inputUrl @{ type = "key"; key = "w"; down = $false }
  Send-Input $inputUrl @{ type = "move"; dx = 120; dy = -35 }
  Send-Input $inputUrl @{ type = "key"; key = "Space"; down = $true }
  Start-Sleep -Milliseconds 180
  Send-Input $inputUrl @{ type = "key"; key = "Space"; down = $false }
  Send-Input $inputUrl @{ type = "key"; key = "d"; down = $true }
  Start-Sleep -Seconds 1
  Send-Input $inputUrl @{ type = "key"; key = "d"; down = $false }
  Send-Input $inputUrl @{ type = "button"; button = "left"; down = $true }
  Start-Sleep -Milliseconds 120
  Send-Input $inputUrl @{ type = "button"; button = "left"; down = $false }
  Send-Input $inputUrl @{ type = "release" }
  Capture-Checkpoint $frameUrl "02-after-input"

  $remaining = [Math]::Max(0, $DurationSeconds - 10)
  if ($remaining -gt 0) { Start-Sleep -Seconds $remaining }
  $gameProcess.Refresh()
  if ($gameProcess.HasExited) {
    $report.processExited = $true
    $report.processExitCode = $gameProcess.ExitCode
  } else {
    Capture-Checkpoint $frameUrl "03-final"
  }

  $uniqueHashes = @($report.checkpoints | ForEach-Object { $_.sha256 } | Select-Object -Unique)
  $report.ok = (-not $report.processExited) -and $report.proxyReady -and $report.windowReady -and $report.streamReady -and ($report.checkpoints.Count -ge 3) -and ($uniqueHashes.Count -ge 2)
  if ($uniqueHashes.Count -lt 2) { $report.errors += "Captured frames never changed; the client may be frozen on a static screen." }
} catch {
  $report.errors += $_.Exception.Message
} finally {
  try { $report.unityLogs = @(Collect-UnityLogs $startedAt) } catch { $report.errors += ("Unity log collection failed: " + $_.Exception.Message) }
  if (-not $KeepRunning) {
    if ($captureProcess -and -not $captureProcess.HasExited) { Stop-Process -Id $captureProcess.Id -Force -ErrorAction SilentlyContinue }
    if ($gameProcess -and -not $gameProcess.HasExited) { Stop-Process -Id $gameProcess.Id -Force -ErrorAction SilentlyContinue }
    if ($proxyProcess -and -not $proxyProcess.HasExited) { Stop-Process -Id $proxyProcess.Id -Force -ErrorAction SilentlyContinue }
  }
  Remove-Item Env:FLUX_RECROOM_GAME_PID -ErrorAction SilentlyContinue
  $reportPath = Save-Report
  Write-Host "Rec Room playtest report: $reportPath" -ForegroundColor Cyan
  Write-Host ($report | ConvertTo-Json -Depth 10)
}

if (-not $report.ok) { exit 2 }
exit 0

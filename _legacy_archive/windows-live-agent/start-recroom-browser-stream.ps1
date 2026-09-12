param(
  [int]$Port = 6081,
  [switch]$LocalOnly
)

$ErrorActionPreference = "Stop"
$statePath = Join-Path $PSScriptRoot "recroom-stream-state.json"
$streamScript = Join-Path $PSScriptRoot "recroom-web-stream.py"
$stopScript = Join-Path $PSScriptRoot "stop-recroom-browser-stream.ps1"
$binDir = Join-Path $PSScriptRoot "bin"
$cloudflared = Join-Path $binDir "cloudflared.exe"
$stdoutLog = Join-Path $env:TEMP "flux-recroom-web-stream.out.log"
$stderrLog = Join-Path $env:TEMP "flux-recroom-web-stream.err.log"
$tunnelOut = Join-Path $env:TEMP "flux-recroom-cloudflared.out.log"
$tunnelErr = Join-Path $env:TEMP "flux-recroom-cloudflared.err.log"

if (-not $env:FLUX_RECROOM_GAME_PID) { throw "FLUX_RECROOM_GAME_PID is required." }
$gamePid = [int]$env:FLUX_RECROOM_GAME_PID
if ($gamePid -le 0) { throw "FLUX_RECROOM_GAME_PID is invalid." }
if (-not (Get-Process -Id $gamePid -ErrorAction SilentlyContinue)) { throw "Rec Room process $gamePid is not running." }
if (-not (Test-Path $streamScript)) { throw "Missing browser stream worker: $streamScript" }

if (Test-Path $stopScript) {
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript 2>$null | Out-Null
}

$python = $null
foreach ($candidate in @("python.exe", "python3.exe", "py.exe")) {
  $command = Get-Command $candidate -ErrorAction SilentlyContinue
  if ($command) { $python = $command.Source; break }
}
if (-not $python) { throw "Python 3 is required on the Windows Rec Room host." }

$pythonPrefix = @()
if ((Split-Path $python -Leaf).ToLowerInvariant() -eq "py.exe") { $pythonPrefix = @("-3") }

$pilCheck = Start-Process -FilePath $python -ArgumentList @($pythonPrefix + @("-c", '"from PIL import ImageGrab"')) -Wait -PassThru -NoNewWindow
if ($pilCheck.ExitCode -ne 0) {
  $install = Start-Process -FilePath $python -ArgumentList @($pythonPrefix + @("-m", "pip", "install", "--disable-pip-version-check", "Pillow>=10,<12")) -Wait -PassThru -NoNewWindow
  if ($install.ExitCode -ne 0) { throw "Could not install Pillow for the Rec Room browser stream." }
}

if (-not $LocalOnly) {
  New-Item -ItemType Directory -Path $binDir -Force | Out-Null
  if (-not (Test-Path $cloudflared)) {
    $url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
    $temp = "$cloudflared.download"
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $temp -TimeoutSec 120
    if (-not (Test-Path $temp) -or (Get-Item $temp).Length -lt 1MB) { throw "cloudflared download was incomplete." }
    Move-Item $temp $cloudflared -Force
  }
}

Remove-Item $stdoutLog,$stderrLog,$tunnelOut,$tunnelErr -Force -ErrorAction SilentlyContinue
$tokenBytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($tokenBytes)
$token = [Convert]::ToBase64String($tokenBytes).TrimEnd('=').Replace('+','-').Replace('/','_')

$streamArgs = @($pythonPrefix + @(
  "`"$streamScript`"",
  "--pid", [string]$gamePid,
  "--port", [string]$Port,
  "--token", $token,
  "--max-width", "1280",
  "--quality", "72"
))
$stream = Start-Process -FilePath $python -ArgumentList $streamArgs -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog

$healthy = $false
for ($i = 0; $i -lt 80; $i++) {
  if ($stream.HasExited) {
    $detail = if (Test-Path $stderrLog) { Get-Content $stderrLog -Raw } else { "browser stream exited" }
    throw "Rec Room browser stream failed: $detail"
  }
  try {
    $health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
    if ($health.ok) { $healthy = $true; break }
  } catch {}
  Start-Sleep -Milliseconds 250
}
if (-not $healthy) {
  Stop-Process -Id $stream.Id -Force -ErrorAction SilentlyContinue
  throw "Rec Room browser stream could not find a visible game window."
}

$tunnelPid = 0
$baseUrl = "http://127.0.0.1:$Port"
if (-not $LocalOnly) {
  $tunnel = Start-Process -FilePath $cloudflared -ArgumentList @("tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:$Port") -PassThru -WindowStyle Hidden -RedirectStandardOutput $tunnelOut -RedirectStandardError $tunnelErr
  $tunnelUrl = ""
  for ($i = 0; $i -lt 180; $i++) {
    if ($tunnel.HasExited) {
      $detail = ((Get-Content $tunnelErr -Raw -ErrorAction SilentlyContinue) + "`n" + (Get-Content $tunnelOut -Raw -ErrorAction SilentlyContinue)).Trim()
      throw "Cloudflare tunnel exited before becoming ready: $detail"
    }
    $logs = ((Get-Content $tunnelErr -Raw -ErrorAction SilentlyContinue) + "`n" + (Get-Content $tunnelOut -Raw -ErrorAction SilentlyContinue))
    $match = [regex]::Match($logs, 'https://[a-z0-9-]+\.trycloudflare\.com', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
    if ($match.Success) { $tunnelUrl = $match.Value; break }
    Start-Sleep -Milliseconds 250
  }
  if (-not $tunnelUrl) {
    Stop-Process -Id $tunnel.Id -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $stream.Id -Force -ErrorAction SilentlyContinue
    throw "Cloudflare Quick Tunnel did not return an HTTPS URL."
  }
  $tunnelPid = $tunnel.Id
  $baseUrl = $tunnelUrl.TrimEnd('/')
}

$state = [ordered]@{
  startedAt = [DateTimeOffset]::UtcNow.ToString("o")
  gamePid = $gamePid
  streamPid = $stream.Id
  tunnelPid = $tunnelPid
  localPort = $Port
  localOnly = [bool]$LocalOnly
  publicUrl = $(if ($LocalOnly) { "" } else { $baseUrl })
}
$state | ConvertTo-Json -Depth 4 | Set-Content $statePath -Encoding UTF8

Write-Output ($baseUrl + "/?token=" + [Uri]::EscapeDataString($token))

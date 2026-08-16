param(
  [int]$Port = 6081
)

$ErrorActionPreference = "Stop"
$statePath = Join-Path $PSScriptRoot "recroom-vm-stream-state.json"
$streamScript = Join-Path $PSScriptRoot "recroom-vm-web-stream.py"
$stdoutLog = Join-Path $env:TEMP "ripo-recroom-vm-stream.out.log"
$stderrLog = Join-Path $env:TEMP "ripo-recroom-vm-stream.err.log"

if (-not $env:FLUX_RECROOM_GAME_PID) { throw "FLUX_RECROOM_GAME_PID is required." }
$gamePid = [int]$env:FLUX_RECROOM_GAME_PID
if ($gamePid -le 0 -or -not (Get-Process -Id $gamePid -ErrorAction SilentlyContinue)) {
  throw "Rec Room process $gamePid is not running."
}
if (-not (Test-Path -LiteralPath $streamScript -PathType Leaf)) { throw "Missing $streamScript" }

$python = $null
foreach ($candidate in @("python.exe", "py.exe", "python3.exe")) {
  $command = Get-Command $candidate -ErrorAction SilentlyContinue
  if ($command) { $python = $command.Source; break }
}
if (-not $python) { throw "Python 3 is required inside the Windows VM." }
$prefix = @()
if ((Split-Path $python -Leaf).ToLowerInvariant() -eq "py.exe") { $prefix = @("-3") }

& $python @prefix -m pip install --disable-pip-version-check "Pillow>=10,<12" "numpy>=1.26,<3" "soundcard>=0.4,<1" | Out-Null

$tokenBytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($tokenBytes)
$token = [Convert]::ToBase64String($tokenBytes).TrimEnd('=').Replace('+','-').Replace('/','_')

Remove-Item $stdoutLog,$stderrLog -Force -ErrorAction SilentlyContinue
$args = @($prefix + @(
  "`"$streamScript`"",
  "--pid", [string]$gamePid,
  "--port", [string]$Port,
  "--token", $token,
  "--max-width", "1280",
  "--quality", "75"
))
$stream = Start-Process -FilePath $python -ArgumentList $args -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog

$healthy = $false
for ($i = 0; $i -lt 120; $i++) {
  if ($stream.HasExited) {
    $detail = Get-Content -LiteralPath $stderrLog -Raw -ErrorAction SilentlyContinue
    throw "RipoTeam VM stream exited early: $detail"
  }
  try {
    $health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
    if ($health.ok) { $healthy = $true; break }
  } catch {}
  Start-Sleep -Milliseconds 250
}
if (-not $healthy) {
  Stop-Process -Id $stream.Id -Force -ErrorAction SilentlyContinue
  throw "RipoTeam VM streamer never found the Rec Room game window."
}

@{
  startedAt = [DateTimeOffset]::UtcNow.ToString("o")
  gamePid = $gamePid
  streamPid = $stream.Id
  localPort = $Port
  audio = $true
} | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8

Write-Output ("http://127.0.0.1:$Port/?token=" + [Uri]::EscapeDataString($token))

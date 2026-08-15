param(
  [string]$Config = (Join-Path $PSScriptRoot "recroom-agent-config.json")
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Config)) {
  throw "Missing $Config. Copy recroom-agent-config.example.json to recroom-agent-config.json and edit it locally."
}

function Load-HostConfig {
  return Get-Content $Config -Raw | ConvertFrom-Json
}

function Stop-Worker($Process) {
  if ($Process -and -not $Process.HasExited) {
    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
  }
}

function Invoke-HostUpdater {
  $updateScript = Join-Path $PSScriptRoot "update-recroom-host.ps1"
  if (-not (Test-Path $updateScript)) { return $false }

  $process = Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$updateScript`"",
    "-Config", "`"$Config`""
  ) -Wait -PassThru -NoNewWindow

  if ($process.ExitCode -eq 10) { return $true }
  if ($process.ExitCode -ne 0) {
    Write-Host "Rec Room host updater failed with exit code $($process.ExitCode); keeping the current validated tools." -ForegroundColor Yellow
  }
  return $false
}

function Start-WorkerPair {
  $mainScript = Join-Path $PSScriptRoot "recroom-agent.ps1"
  $captureScript = Join-Path $PSScriptRoot "recroom-capture-agent.ps1"
  if (-not (Test-Path $mainScript)) { throw "Missing $mainScript" }
  if (-not (Test-Path $captureScript)) { throw "Missing $captureScript" }

  $common = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass"
  )
  $main = Start-Process -FilePath "powershell.exe" -ArgumentList @(
    $common[0], $common[1], $common[2],
    "-File", "`"$mainScript`"",
    "-Config", "`"$Config`""
  ) -PassThru
  $capture = Start-Process -FilePath "powershell.exe" -ArgumentList @(
    $common[0], $common[1], $common[2],
    "-File", "`"$captureScript`"",
    "-Config", "`"$Config`""
  ) -PassThru

  return [pscustomobject]@{ Main = $main; Capture = $capture }
}

function Test-RecRoomRunning {
  return [bool](Get-Process -Name "RecRoom", "Recroom_Release" -ErrorAction SilentlyContinue | Select-Object -First 1)
}

$cfg = Load-HostConfig
$autoUpdate = if ($null -eq $cfg.autoUpdate) { $true } else { [bool]$cfg.autoUpdate }
$intervalMinutes = if ($cfg.updateIntervalMinutes) { [Math]::Max(5, [int]$cfg.updateIntervalMinutes) } else { 15 }

if ($autoUpdate) {
  try { [void](Invoke-HostUpdater) } catch {
    Write-Host ("Initial Rec Room tool update check failed: " + $_.Exception.Message) -ForegroundColor Yellow
  }
}

$workers = Start-WorkerPair
$nextUpdate = [DateTimeOffset]::UtcNow.AddMinutes($intervalMinutes)
Write-Host "Flux Rec Room host supervisor online." -ForegroundColor Cyan
Write-Host "Auto-update: $autoUpdate · interval: $intervalMinutes minutes · active games are never interrupted."

try {
  while ($true) {
    if ($workers.Main.HasExited) {
      Write-Host "Rec Room host agent exited; restarting workers." -ForegroundColor Yellow
      Stop-Worker $workers.Capture
      Start-Sleep -Seconds 3
      $workers = Start-WorkerPair
    } elseif ($workers.Capture.HasExited) {
      Write-Host "Rec Room capture worker exited; restarting it with the host worker." -ForegroundColor Yellow
      Stop-Worker $workers.Main
      Start-Sleep -Seconds 2
      $workers = Start-WorkerPair
    }

    if ($autoUpdate -and [DateTimeOffset]::UtcNow -ge $nextUpdate) {
      if (Test-RecRoomRunning) {
        Write-Host "Update available check deferred because a Rec Room session is active." -ForegroundColor DarkGray
      } else {
        try {
          $changed = Invoke-HostUpdater
          if ($changed) {
            Write-Host "Applying updated Rec Room host workers..." -ForegroundColor Green
            Stop-Worker $workers.Main
            Stop-Worker $workers.Capture
            Start-Sleep -Milliseconds 600
            $workers = Start-WorkerPair
          }
        } catch {
          Write-Host ("Rec Room update check failed: " + $_.Exception.Message) -ForegroundColor Yellow
        }
      }
      $nextUpdate = [DateTimeOffset]::UtcNow.AddMinutes($intervalMinutes)
    }

    Start-Sleep -Seconds 3
  }
} finally {
  Stop-Worker $workers.Main
  Stop-Worker $workers.Capture
}

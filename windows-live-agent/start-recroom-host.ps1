param(
  [string]$Config = (Join-Path $PSScriptRoot "recroom-agent-config.json")
)

$ErrorActionPreference = "Stop"
$env:RECROOM_AGENT_DIR = $PSScriptRoot

function Refresh-Path {
  $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $user = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = @($machine, $user, $env:Path) -join ";"
}

function Ensure-WindowsRuntime([string]$CommandName, [string]$WingetId, [string]$Label) {
  if (Get-Command $CommandName -ErrorAction SilentlyContinue) { return }
  $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
  if (-not $winget) { throw "$Label is required. Install it, or install App Installer/winget so Flux can do it automatically." }
  Write-Host "Installing $Label for the Rec Room host..." -ForegroundColor DarkCyan
  $process = Start-Process -FilePath $winget.Source -ArgumentList @(
    "install", "--id", $WingetId, "-e", "--silent",
    "--accept-package-agreements", "--accept-source-agreements"
  ) -Wait -PassThru -NoNewWindow
  if ($process.ExitCode -ne 0) { throw "winget could not install $Label (exit $($process.ExitCode))." }
  Refresh-Path
  if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) { throw "$Label installed but is not visible in this shell yet. Reopen PowerShell once." }
}

if (-not (Test-Path $Config)) {
  $bootstrap = Join-Path $PSScriptRoot "bootstrap-recroom-host.ps1"
  if (-not (Test-Path $bootstrap)) { throw "Missing $Config and $bootstrap." }
  Write-Host "No Rec Room host config found; bootstrapping automatically..." -ForegroundColor Cyan
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bootstrap -Config $Config
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Config)) { throw "Rec Room host bootstrap did not create a valid config." }
}

Ensure-WindowsRuntime "node.exe" "OpenJS.NodeJS.LTS" "Node.js LTS"
$hasPython = (Get-Command py.exe -ErrorAction SilentlyContinue) -or (Get-Command python.exe -ErrorAction SilentlyContinue)
if (-not $hasPython) {
  Ensure-WindowsRuntime "python.exe" "Python.Python.3.12" "Python 3.12"
}

function Load-HostConfig {
  return Get-Content $Config -Raw | ConvertFrom-Json
}

function Set-ConfigProperty($cfg, [string]$Name, $Value) {
  if ($cfg.PSObject.Properties[$Name]) { $cfg.$Name = $Value }
  else { $cfg | Add-Member -NotePropertyName $Name -NotePropertyValue $Value }
}

function Save-HostConfig($cfg) {
  $cfg | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Config -Encoding UTF8
}

function Resolve-VerifiedRecRoomClient($cfg) {
  $finder = Join-Path $PSScriptRoot "identify-recroom-client.ps1"
  if (-not (Test-Path -LiteralPath $finder -PathType Leaf)) {
    throw "Missing strict Rec Room build finder: $finder. Run the host updater first."
  }

  $arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$finder`"",
    "-AsJson"
  )
  if ($cfg.clientDir) {
    $arguments += @("-Root", "`"$([string]$cfg.clientDir)`"")
  } else {
    $arguments += "-Scan"
  }

  $process = Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -Wait -PassThru -NoNewWindow `
    -RedirectStandardOutput (Join-Path $env:TEMP "flux-recroom-client-identify.json") `
    -RedirectStandardError (Join-Path $env:TEMP "flux-recroom-client-identify.err")
  if ($process.ExitCode -ne 0) {
    $errorText = Get-Content (Join-Path $env:TEMP "flux-recroom-client-identify.err") -Raw -ErrorAction SilentlyContinue
    throw "Rec Room client fingerprint scan failed (exit $($process.ExitCode)): $errorText"
  }

  $raw = Get-Content (Join-Path $env:TEMP "flux-recroom-client-identify.json") -Raw
  try { $scan = $raw | ConvertFrom-Json } catch { throw "Rec Room client finder returned invalid JSON: $raw" }

  $target = @($scan.clients | Where-Object { $_.kind -eq "target-2022" -and $_.playableBy2022Agent }) | Select-Object -First 1
  if ($target) {
    $resolved = [string]$target.root
    $env:FLUX_RECROOM_CLIENT_DIR = $resolved
    if ([string]$cfg.clientDir -ne $resolved) {
      Set-ConfigProperty $cfg "clientDir" $resolved
      Save-HostConfig $cfg
    }
    Write-Host "Verified Rec Room May 19 2022 client: $resolved" -ForegroundColor Green
    Write-Host "Identity: build $($target.buildId) / manifest $($target.manifestId) · confidence $($target.confidence)"
    return $target
  }

  $exact2023 = @($scan.clients | Where-Object { $_.kind -eq "fluxrec-2023" }) | Select-Object -First 1
  if ($exact2023) {
    throw "Found exact FluxRec March 7 2023 build 10679392 at '$($exact2023.root)', but this host mode targets May 19 2022 build 8751857. Refusing to patch the 2023 IL2CPP client as 2022."
  }

  $claimed2023 = @($scan.clients | Where-Object { $_.kind -eq "unverified-2023" }) | Select-Object -First 1
  if ($claimed2023) {
    throw "Found a folder claiming the March 2023 Rec Room build at '$($claimed2023.root)', but its pinned FluxRec hashes do not match. Refusing to launch it."
  }

  $unknown = @($scan.clients | Where-Object { $_.kind -eq "unknown" }) | Select-Object -First 1
  if ($unknown) {
    throw "Found an unknown Rec Room client at '$($unknown.root)' (exe SHA256 $($unknown.fingerprint.exeSha256)), but it is not verified as build 8751857. Rename/move only a known May 19 2022 depot into an exact 8751857/manifest-marked folder or set clientDir to that exact build folder."
  }

  throw "No complete verified May 19 2022 Rec Room client was found. The host will stay offline instead of advertising the wrong game build."
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
  # The updater may have replaced the finder or config, so reload before the
  # strict build-identification gate.
  $cfg = Load-HostConfig
}

$verifiedClient = Resolve-VerifiedRecRoomClient $cfg
$workers = Start-WorkerPair
$nextUpdate = [DateTimeOffset]::UtcNow.AddMinutes($intervalMinutes)
Write-Host "Flux Rec Room host supervisor online." -ForegroundColor Cyan
Write-Host "Client: $($verifiedClient.root)"
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
            $cfg = Load-HostConfig
            $verifiedClient = Resolve-VerifiedRecRoomClient $cfg
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
  $stopStream = Join-Path $PSScriptRoot "stop-recroom-browser-stream.ps1"
  if (Test-Path $stopStream) { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopStream 2>$null | Out-Null }
}

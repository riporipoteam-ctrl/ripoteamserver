param(
  [string]$Config = (Join-Path $PSScriptRoot "recroom-agent-config.json")
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Config)) {
  throw "Missing $Config. Copy recroom-agent-config.example.json to recroom-agent-config.json and edit it locally."
}

$cfg = Get-Content $Config -Raw | ConvertFrom-Json
if ($env:RECROOM_HOST_KEY) { $cfg.hostKey = $env:RECROOM_HOST_KEY }
if ($env:RECROOM_BROKER_URL) { $cfg.server = $env:RECROOM_BROKER_URL }
if (-not $cfg.server -or -not $cfg.hostId -or -not $cfg.hostKey) {
  throw "server, hostId and hostKey are required for Rec Room capture worker."
}

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class FluxWindowCaptureNative {
  [StructLayout(LayoutKind.Sequential)]
  public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
  [DllImport("user32.dll")]
  public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  [DllImport("user32.dll")]
  public static extern bool IsIconic(IntPtr hWnd);
}
"@

function Headers {
  return @{ "x-recroom-host-key" = [string]$cfg.hostKey }
}

function Api-Get([string]$Path) {
  return Invoke-RestMethod -Method Get -Uri ($cfg.server.TrimEnd('/') + $Path) -Headers (Headers) -TimeoutSec 20
}

function Api-PostJson([string]$Path, $Body) {
  return Invoke-RestMethod -Method Post -Uri ($cfg.server.TrimEnd('/') + $Path) -Headers (Headers) -ContentType "application/json" -Body ($Body | ConvertTo-Json -Depth 5) -TimeoutSec 20
}

function Find-RecRoomProcess {
  $names = @("RecRoom", "Recroom_Release")
  foreach ($name in $names) {
    $process = Get-Process -Name $name -ErrorAction SilentlyContinue |
      Where-Object { $_.MainWindowHandle -ne 0 } |
      Select-Object -First 1
    if ($process) { return $process }
  }
  return $null
}

function Capture-RecRoomWindow([string]$Destination) {
  $process = Find-RecRoomProcess
  if (-not $process) { throw "No visible Rec Room game window was found." }
  if ([FluxWindowCaptureNative]::IsIconic($process.MainWindowHandle)) {
    throw "Rec Room is minimized, so a clean game-window screenshot cannot be captured."
  }

  $rect = New-Object FluxWindowCaptureNative+RECT
  if (-not [FluxWindowCaptureNative]::GetWindowRect($process.MainWindowHandle, [ref]$rect)) {
    throw "Windows could not read the Rec Room window bounds."
  }

  $width = $rect.Right - $rect.Left
  $height = $rect.Bottom - $rect.Top
  if ($width -lt 320 -or $height -lt 240 -or $width -gt 7680 -or $height -gt 4320) {
    throw "Rec Room reported invalid capture bounds ${width}x${height}."
  }

  $bitmap = New-Object System.Drawing.Bitmap $width, $height
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  try {
    $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size, [System.Drawing.CopyPixelOperation]::SourceCopy)
    $bitmap.Save($Destination, [System.Drawing.Imaging.ImageFormat]::Png)
  } finally {
    $graphics.Dispose()
    $bitmap.Dispose()
  }

  $info = Get-Item $Destination
  if ($info.Length -le 0) { throw "Windows produced an empty screenshot." }
  if ($info.Length -gt 8MB) { throw "Screenshot is larger than the broker's 8 MB limit." }
  return $info
}

function Upload-Capture([string]$CaptureId, [string]$FilePath) {
  $uri = $cfg.server.TrimEnd('/') + "/api/recroom/hosts/$($cfg.hostId)/captures/$CaptureId"
  Invoke-WebRequest -UseBasicParsing -Method Put -Uri $uri -Headers (Headers) -ContentType "image/png" -InFile $FilePath -TimeoutSec 30 | Out-Null
}

function Fail-Capture([string]$CaptureId, [string]$Message) {
  try {
    [void](Api-PostJson "/api/recroom/hosts/$($cfg.hostId)/captures/$CaptureId/failed" @{ error = $Message })
  } catch {
    Write-Host ("Could not report screenshot failure: " + $_.Exception.Message) -ForegroundColor Yellow
  }
}

Write-Host "Flux Rec Room screenshot worker" -ForegroundColor Cyan
Write-Host "Host: $($cfg.hostId)"
Write-Host "Broker: $($cfg.server)"

$tempDir = Join-Path $env:TEMP "FluxRecRoomCaptures"
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null

while ($true) {
  try {
    $response = Api-Get "/api/recroom/hosts/$($cfg.hostId)/capture-jobs"
    if ($response.job -and $response.job.type -eq "capture-screenshot") {
      $captureId = [string]$response.job.captureId
      $sessionId = [string]$response.job.sessionId
      $filePath = Join-Path $tempDir ("$captureId.png")
      try {
        Write-Host "Capturing Rec Room for session $sessionId..." -ForegroundColor DarkCyan
        [void](Capture-RecRoomWindow $filePath)
        Upload-Capture $captureId $filePath
        Write-Host "Screenshot $captureId uploaded." -ForegroundColor Green
      } catch {
        $message = $_.Exception.Message
        Write-Host ("Screenshot failed: " + $message) -ForegroundColor Red
        Fail-Capture $captureId $message
      } finally {
        Remove-Item $filePath -Force -ErrorAction SilentlyContinue
      }
    }
    Start-Sleep -Milliseconds 700
  } catch {
    Write-Host ("Screenshot worker reconnecting: " + $_.Exception.Message) -ForegroundColor Yellow
    Start-Sleep -Seconds 4
  }
}

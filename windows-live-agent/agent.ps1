param(
  [string]$Server = "https://echoxr-ripoteam-cloud-pc.hf.space"
)

$ErrorActionPreference = "Stop"
$ConfigPath = Join-Path $PSScriptRoot "agent-config.json"
$AudioPath = Join-Path $env:TEMP "ripo-live-speech.wav"

function Load-Config {
  if (Test-Path $ConfigPath) {
    return Get-Content $ConfigPath -Raw | ConvertFrom-Json
  }
  return [pscustomobject]@{ server = $Server; agent_token = ""; agent_id = ""; name = $env:COMPUTERNAME }
}

function Save-Config($cfg) {
  $cfg | ConvertTo-Json | Set-Content -Encoding UTF8 $ConfigPath
}

function Api-Post([string]$Path, $Body, [string]$Token = "") {
  $headers = @{}
  if ($Token) { $headers["x-live-agent-token"] = $Token }
  return Invoke-RestMethod -Method Post -Uri ($script:cfg.server.TrimEnd('/') + $Path) -Headers $headers -ContentType "application/json" -Body ($Body | ConvertTo-Json -Depth 6) -TimeoutSec 20
}

function Send-LiveStudioHotkey([string]$Command) {
  $shell = New-Object -ComObject WScript.Shell
  [void]$shell.AppActivate("TikTok LIVE Studio")
  Start-Sleep -Milliseconds 250
  switch ($Command) {
    "start_live"  { $shell.SendKeys("^%{F9}"); return "Sent Start/Stop LIVE hotkey Ctrl+Alt+F9." }
    "stop_live"   { $shell.SendKeys("^%{F9}"); return "Sent Start/Stop LIVE hotkey Ctrl+Alt+F9." }
    "toggle_mic"  { $shell.SendKeys("^%m"); return "Sent microphone hotkey Ctrl+Alt+M." }
    "scene_next"  { $shell.SendKeys("^%{RIGHT}"); return "Sent next-scene hotkey Ctrl+Alt+Right." }
    "scene_prev"  { $shell.SendKeys("^%{LEFT}"); return "Sent previous-scene hotkey Ctrl+Alt+Left." }
    default { throw "Command '$Command' is not mapped to a LIVE Studio hotkey yet." }
  }
}

function Play-RipoSpeech {
  $headers = @{ "x-live-agent-token" = $script:cfg.agent_token }
  try {
    if (Test-Path $AudioPath) { Remove-Item $AudioPath -Force }
    $response = Invoke-WebRequest -Method Get -Uri ($script:cfg.server.TrimEnd('/') + "/api/tiktok/live-studio/agent/audio") -Headers $headers -OutFile $AudioPath -UseBasicParsing -TimeoutSec 12
    if ($response.StatusCode -eq 200 -and (Test-Path $AudioPath) -and (Get-Item $AudioPath).Length -gt 44) {
      $player = New-Object System.Media.SoundPlayer $AudioPath
      $player.PlaySync()
      Remove-Item $AudioPath -Force -ErrorAction SilentlyContinue
      return $true
    }
  } catch {
    Remove-Item $AudioPath -Force -ErrorAction SilentlyContinue
  }
  return $false
}

$script:cfg = Load-Config
if ($Server) { $script:cfg.server = $Server.TrimEnd('/') }

if (-not $script:cfg.agent_token) {
  Write-Host "Ripo TikTok LIVE Studio pairing" -ForegroundColor Cyan
  Write-Host "In the Ripo TikTok dashboard press Pair Windows LIVE Studio, then type the 6-digit code."
  $code = Read-Host "Pairing code"
  $result = Api-Post "/api/tiktok/live-studio/agent/register" @{ code = $code; name = $script:cfg.name }
  $script:cfg.agent_token = $result.agent_token
  $script:cfg.agent_id = $result.agent_id
  Save-Config $script:cfg
  Write-Host "Paired." -ForegroundColor Green
}

Write-Host "Ripo LIVE Studio bridge online." -ForegroundColor Green
Write-Host "Keep TikTok LIVE Studio open and signed in."
Write-Host "Configure these LIVE Studio hotkeys once:"
Write-Host "  Start/Stop streaming = Ctrl+Alt+F9"
Write-Host "  Mute/Unmute microphone = Ctrl+Alt+M"
Write-Host "  Next scene = Ctrl+Alt+Right"
Write-Host "  Previous scene = Ctrl+Alt+Left"
Write-Host "Ripo Bot speech will play through the Windows default audio output."
Write-Host "Make sure LIVE Studio captures that Windows audio output so viewers can hear the bot."

while ($true) {
  try {
    $proc = Get-Process | Where-Object { $_.ProcessName -match "TikTok|LIVEStudio|LiveStudio" } | Select-Object -First 1
    $poll = Api-Post "/api/tiktok/live-studio/agent/poll" @{ live_studio_running = [bool]$proc } $script:cfg.agent_token
    if ($poll.command) {
      $ok = $true
      $message = ""
      try {
        $message = Send-LiveStudioHotkey ([string]$poll.command.command)
      } catch {
        $ok = $false
        $message = $_.Exception.Message
      }
      [void](Api-Post "/api/tiktok/live-studio/agent/result" @{ command_id = $poll.command.id; ok = $ok; message = $message } $script:cfg.agent_token)
      Write-Host ((if ($ok) { "OK: " } else { "ERROR: " }) + $message)
    }
    [void](Play-RipoSpeech)
    Start-Sleep -Milliseconds 900
  } catch {
    Write-Host ("Bridge reconnecting: " + $_.Exception.Message) -ForegroundColor Yellow
    Start-Sleep -Seconds 5
  }
}

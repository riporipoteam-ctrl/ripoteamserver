param(
  [string]$AgentDir = "C:\RipoTeam\RecRoomHost"
)

$ErrorActionPreference = "Stop"
$logDir = "C:\RipoTeam\Logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logPath = Join-Path $logDir "recroom-vm-startup.log"
Start-Transcript -Path $logPath -Append | Out-Null

try {
  $configSource = $null
  foreach ($drive in Get-PSDrive -PSProvider FileSystem) {
    $candidate = Join-Path $drive.Root "recroom-vm-config.json"
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
      $configSource = $candidate
      break
    }
  }
  if (-not $configSource) {
    throw "RipoTeamServer session configuration ISO is not mounted."
  }

  New-Item -ItemType Directory -Path $AgentDir -Force | Out-Null
  $configPath = Join-Path $AgentDir "recroom-agent-config.json"
  Copy-Item -LiteralPath $configSource -Destination $configPath -Force
  $cfg = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json

  if ($cfg.agentDir -and [string]$cfg.agentDir -ne $AgentDir) {
    $AgentDir = [string]$cfg.agentDir
    New-Item -ItemType Directory -Path $AgentDir -Force | Out-Null
    $configPath = Join-Path $AgentDir "recroom-agent-config.json"
    Copy-Item -LiteralPath $configSource -Destination $configPath -Force
    $cfg = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
  }

  $repo = "https://raw.githubusercontent.com/riporipoteam-ctrl/ripoteamserver/main/windows-live-agent"
  $required = @(
    "recroom-agent.ps1",
    "identify-recroom-client.ps1",
    "start-recroom-vm-browser-stream.ps1",
    "stop-recroom-browser-stream.ps1",
    "recroom-vm-web-stream.py",
    "recroom-capture-agent.ps1"
  )
  foreach ($name in $required) {
    $target = Join-Path $AgentDir $name
    if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
      Invoke-WebRequest -UseBasicParsing -Uri "$repo/$name" -OutFile $target -TimeoutSec 60
    }
  }

  $python = $null
  foreach ($candidate in @("python.exe", "py.exe", "python3.exe")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) { $python = $cmd.Source; break }
  }
  if (-not $python) { throw "Python 3 is required in the Windows golden image." }

  $prefix = @()
  if ((Split-Path $python -Leaf).ToLowerInvariant() -eq "py.exe") { $prefix = @("-3") }
  & $python @prefix -m pip install --disable-pip-version-check "Pillow>=10,<12" "soundcard>=0.4,<1" "numpy>=1.26,<3" | Out-Null

  $env:RECROOM_AGENT_DIR = $AgentDir
  $env:RECROOM_BROKER_URL = [string]$cfg.server
  $env:RECROOM_HOST_KEY = [string]$cfg.hostKey
  $env:FLUX_RECROOM_CLIENT_DIR = [string]$cfg.clientDir

  # Force the server-owned VM path to use the local-only streamer. QEMU forwards
  # guest :6081 to a unique loopback port on RipoTeamServer; the broker rewrites
  # the local stream URL into the public per-VM proxy URL. This streamer carries
  # video, keyboard/mouse/touch input and Windows loopback audio.
  $streamCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%RECROOM_AGENT_DIR%\start-recroom-vm-browser-stream.ps1"'
  if ($cfg.PSObject.Properties["streamStartCommand"]) { $cfg.streamStartCommand = $streamCommand }
  else { $cfg | Add-Member -NotePropertyName streamStartCommand -NotePropertyValue $streamCommand }
  $cfg | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $configPath -Encoding UTF8

  $agent = Join-Path $AgentDir "recroom-agent.ps1"
  if (-not (Test-Path -LiteralPath $agent -PathType Leaf)) { throw "Rec Room VM agent is missing at $agent" }

  while ($true) {
    try {
      & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $agent -Config $configPath
    } catch {
      Write-Host "Rec Room VM agent crashed: $($_.Exception.Message)" -ForegroundColor Red
    }
    Start-Sleep -Seconds 3
  }
} finally {
  Stop-Transcript | Out-Null
}

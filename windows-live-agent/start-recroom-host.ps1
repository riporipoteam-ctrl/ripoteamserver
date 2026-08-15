param(
  [string]$Config = (Join-Path $PSScriptRoot "recroom-agent-config.json")
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Config)) {
  throw "Missing $Config. Copy recroom-agent-config.example.json to recroom-agent-config.json and edit it locally."
}

$mainScript = Join-Path $PSScriptRoot "recroom-agent.ps1"
$captureScript = Join-Path $PSScriptRoot "recroom-capture-agent.ps1"

if (-not (Test-Path $mainScript)) { throw "Missing $mainScript" }
if (-not (Test-Path $captureScript)) { throw "Missing $captureScript" }

$escapedConfig = $Config.Replace('"', '`"')
$capture = Start-Process -FilePath "powershell.exe" -ArgumentList @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-File", "`"$captureScript`"",
  "-Config", "`"$escapedConfig`""
) -PassThru

try {
  & $mainScript -Config $Config
} finally {
  if ($capture -and -not $capture.HasExited) {
    Stop-Process -Id $capture.Id -Force -ErrorAction SilentlyContinue
  }
}

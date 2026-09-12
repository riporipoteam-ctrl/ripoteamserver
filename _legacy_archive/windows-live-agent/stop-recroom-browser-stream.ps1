$ErrorActionPreference = "SilentlyContinue"
$statePath = Join-Path $PSScriptRoot "recroom-stream-state.json"
if (-not (Test-Path $statePath)) { exit 0 }

try {
  $state = Get-Content $statePath -Raw | ConvertFrom-Json
  foreach ($id in @($state.tunnelPid, $state.streamPid)) {
    if ($id) { Stop-Process -Id ([int]$id) -Force -ErrorAction SilentlyContinue }
  }
} finally {
  Remove-Item $statePath -Force -ErrorAction SilentlyContinue
}
exit 0

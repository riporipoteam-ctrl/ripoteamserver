param(
  [string]$Config = (Join-Path $PSScriptRoot "recroom-agent-config.json")
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Config)) {
  throw "Missing $Config. Run bootstrap-recroom-host.ps1 first."
}

$cfg = Get-Content $Config -Raw | ConvertFrom-Json
$enabled = if ($null -eq $cfg.autoUpdate) { $true } else { [bool]$cfg.autoUpdate }
if (-not $enabled) {
  Write-Host "Rec Room host auto-update is disabled."
  exit 0
}

$hostRepo = if ($cfg.updateRepository) { [string]$cfg.updateRepository } else { "riporipoteam-ctrl/ripoteamserver" }
$hostRef = if ($cfg.updateRef) { [string]$cfg.updateRef } else { "main" }
$toolsRepo = if ($cfg.toolsRepository) { [string]$cfg.toolsRepository } else { "riporipoteam-ctrl/recroomfluxgame" }
$toolsRef = if ($cfg.toolsRef) { [string]$cfg.toolsRef } else { "main" }

if ($hostRepo -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw "Invalid updateRepository." }
if ($toolsRepo -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw "Invalid toolsRepository." }
if ($hostRef -notmatch '^[A-Za-z0-9_./-]+$') { throw "Invalid updateRef." }
if ($toolsRef -notmatch '^[A-Za-z0-9_./-]+$') { throw "Invalid toolsRef." }

function Get-Hash([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return "" }
  $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
  try {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $bytes = $sha.ComputeHash($stream) } finally { $sha.Dispose() }
  } finally { $stream.Dispose() }
  return (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")
}

function Download-RepoSnapshot([string]$Repository, [string]$Reference, [string]$DestinationRoot) {
  $parts = $Repository.Split('/')
  if ($parts.Count -ne 2) { throw "Invalid GitHub repository: $Repository" }
  $owner = $parts[0]; $name = $parts[1]
  New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null
  $zip = Join-Path $DestinationRoot ($name + ".zip")
  $extract = Join-Path $DestinationRoot ($name + "-extract")
  $nonce = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
  $uri = "https://github.com/$owner/$name/archive/refs/heads/$Reference.zip?download=$nonce"
  Write-Host "Downloading coherent update snapshot $Repository@$Reference..." -ForegroundColor DarkCyan
  Invoke-WebRequest -UseBasicParsing -Headers @{ "Cache-Control" = "no-cache" } -Uri $uri -OutFile $zip -TimeoutSec 120
  if (-not (Test-Path $zip) -or (Get-Item $zip).Length -lt 256) { throw "GitHub snapshot download failed for $Repository@$Reference" }
  Expand-Archive -Path $zip -DestinationPath $extract -Force
  $root = Get-ChildItem -LiteralPath $extract -Directory | Select-Object -First 1
  if (-not $root) { throw "GitHub snapshot for $Repository@$Reference had no repository root." }
  return [pscustomobject]@{ Root=$root.FullName; Zip=$zip; Sha256=(Get-Hash $zip) }
}

function Test-PowerShellSyntax([string]$Path) {
  $tokens = $null
  $errors = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tokens, [ref]$errors)
  if ($errors.Count -gt 0) {
    $messages = ($errors | ForEach-Object { $_.Message }) -join "; "
    throw "Downloaded PowerShell failed syntax validation: $messages"
  }
}

function Test-NodeSyntax([string]$Path) {
  $node = Get-Command node -ErrorAction SilentlyContinue
  if (-not $node) { throw "Node.js 20+ is required to validate and run the Rec Room host proxy tools." }
  $process = Start-Process -FilePath $node.Source -ArgumentList @("--check", "`"$Path`"") -Wait -PassThru -NoNewWindow
  if ($process.ExitCode -ne 0) { throw "Downloaded Node tool failed syntax validation: $Path" }
}

$tempRoot = Join-Path $env:TEMP ("FluxRecRoomUpdate-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null
$changed = New-Object System.Collections.Generic.List[string]

try {
  $hostSnapshot = Download-RepoSnapshot $hostRepo $hostRef (Join-Path $tempRoot "host")
  $toolsSnapshot = Download-RepoSnapshot $toolsRepo $toolsRef (Join-Path $tempRoot "tools")
  $toolDir = Join-Path $PSScriptRoot "recroom-tools"
  New-Item -ItemType Directory -Path $toolDir -Force | Out-Null

  $targets = @(
    [pscustomobject]@{ Name="start-recroom-host.ps1"; Source=(Join-Path $hostSnapshot.Root "windows-live-agent\start-recroom-host.ps1"); Destination=(Join-Path $PSScriptRoot "start-recroom-host.ps1"); Kind="powershell" },
    [pscustomobject]@{ Name="identify-recroom-client.ps1"; Source=(Join-Path $hostSnapshot.Root "windows-live-agent\identify-recroom-client.ps1"); Destination=(Join-Path $PSScriptRoot "identify-recroom-client.ps1"); Kind="powershell" },
    [pscustomobject]@{ Name="update-recroom-host.ps1"; Source=(Join-Path $hostSnapshot.Root "windows-live-agent\update-recroom-host.ps1"); Destination=(Join-Path $PSScriptRoot "update-recroom-host.ps1"); Kind="powershell" },
    [pscustomobject]@{ Name="recroom-agent.ps1"; Source=(Join-Path $hostSnapshot.Root "windows-live-agent\recroom-agent.ps1"); Destination=(Join-Path $PSScriptRoot "recroom-agent.ps1"); Kind="powershell" },
    [pscustomobject]@{ Name="recroom-capture-agent.ps1"; Source=(Join-Path $hostSnapshot.Root "windows-live-agent\recroom-capture-agent.ps1"); Destination=(Join-Path $PSScriptRoot "recroom-capture-agent.ps1"); Kind="powershell" },
    [pscustomobject]@{ Name="playtest-recroom-client.ps1"; Source=(Join-Path $hostSnapshot.Root "windows-live-agent\playtest-recroom-client.ps1"); Destination=(Join-Path $PSScriptRoot "playtest-recroom-client.ps1"); Kind="powershell" },
    [pscustomobject]@{ Name="bootstrap-recroom-host.ps1"; Source=(Join-Path $hostSnapshot.Root "windows-live-agent\bootstrap-recroom-host.ps1"); Destination=(Join-Path $PSScriptRoot "bootstrap-recroom-host.ps1"); Kind="powershell" },
    [pscustomobject]@{ Name="download-recroom-client.ps1"; Source=(Join-Path $hostSnapshot.Root "windows-live-agent\download-recroom-client.ps1"); Destination=(Join-Path $PSScriptRoot "download-recroom-client.ps1"); Kind="powershell" },
    [pscustomobject]@{ Name="start-recroom-browser-stream.ps1"; Source=(Join-Path $hostSnapshot.Root "windows-live-agent\start-recroom-browser-stream.ps1"); Destination=(Join-Path $PSScriptRoot "start-recroom-browser-stream.ps1"); Kind="powershell" },
    [pscustomobject]@{ Name="stop-recroom-browser-stream.ps1"; Source=(Join-Path $hostSnapshot.Root "windows-live-agent\stop-recroom-browser-stream.ps1"); Destination=(Join-Path $PSScriptRoot "stop-recroom-browser-stream.ps1"); Kind="powershell" },
    [pscustomobject]@{ Name="recroom-web-stream.py"; Source=(Join-Path $hostSnapshot.Root "windows-live-agent\recroom-web-stream.py"); Destination=(Join-Path $PSScriptRoot "recroom-web-stream.py"); Kind="raw" },
    [pscustomobject]@{ Name="requirements.txt"; Source=(Join-Path $hostSnapshot.Root "windows-live-agent\requirements.txt"); Destination=(Join-Path $PSScriptRoot "requirements.txt"); Kind="raw" },
    [pscustomobject]@{ Name="host-proxy.mjs"; Source=(Join-Path $toolsSnapshot.Root "scripts\host-proxy.mjs"); Destination=(Join-Path $toolDir "host-proxy.mjs"); Kind="node" },
    [pscustomobject]@{ Name="redirect-client-urls.mjs"; Source=(Join-Path $toolsSnapshot.Root "scripts\redirect-client-urls.mjs"); Destination=(Join-Path $toolDir "redirect-client-urls.mjs"); Kind="node" },
    [pscustomobject]@{ Name="verify-client.mjs"; Source=(Join-Path $toolsSnapshot.Root "scripts\verify-client.mjs"); Destination=(Join-Path $toolDir "verify-client.mjs"); Kind="node" },
    [pscustomobject]@{ Name="scan-client-urls.mjs"; Source=(Join-Path $toolsSnapshot.Root "scripts\scan-client-urls.mjs"); Destination=(Join-Path $toolDir "scan-client-urls.mjs"); Kind="node" },
    [pscustomobject]@{ Name="patch-client-urls.mjs"; Source=(Join-Path $toolsSnapshot.Root "scripts\patch-client-urls.mjs"); Destination=(Join-Path $toolDir "patch-client-urls.mjs"); Kind="node" }
  )

  foreach ($target in $targets) {
    if (-not (Test-Path -LiteralPath $target.Source -PathType Leaf)) { throw "Update snapshot omitted $($target.Name)" }
    if ($target.Kind -eq "powershell") { Test-PowerShellSyntax $target.Source }
    if ($target.Kind -eq "node") { Test-NodeSyntax $target.Source }
    $downloadedHash = Get-Hash $target.Source
    $currentHash = Get-Hash $target.Destination
    if ($downloadedHash -eq $currentHash) { continue }

    $destinationDir = Split-Path -Parent $target.Destination
    New-Item -ItemType Directory -Path $destinationDir -Force | Out-Null
    $staged = "$($target.Destination).update-new"
    Copy-Item -LiteralPath $target.Source -Destination $staged -Force
    if (Test-Path -LiteralPath $target.Destination -PathType Leaf) {
      Copy-Item -LiteralPath $target.Destination -Destination "$($target.Destination).update-backup" -Force
    }
    Move-Item -LiteralPath $staged -Destination $target.Destination -Force
    $changed.Add($target.Name)
  }

  $state = [ordered]@{
    checkedAt = [DateTimeOffset]::UtcNow.ToString("o")
    hostRepository = $hostRepo
    hostRef = $hostRef
    hostSnapshotSha256 = $hostSnapshot.Sha256
    toolsRepository = $toolsRepo
    toolsRef = $toolsRef
    toolsSnapshotSha256 = $toolsSnapshot.Sha256
    changed = @($changed)
  }
  $state | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $PSScriptRoot "recroom-agent-state.json") -Encoding UTF8

  if ($changed.Count -gt 0) {
    Write-Host ("Updated Rec Room host tools: " + ($changed -join ", ")) -ForegroundColor Green
    exit 10
  }
  Write-Host "Rec Room host tools are current against coherent GitHub branch snapshots." -ForegroundColor Green
  exit 0
} finally {
  Remove-Item $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

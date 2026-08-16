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

function Resolve-GitHubCommit([string]$Repository, [string]$Reference) {
  $encodedRef = [Uri]::EscapeDataString($Reference)
  $uri = "https://api.github.com/repos/$Repository/commits/$encodedRef"
  $result = Invoke-RestMethod -UseBasicParsing -Headers @{
    "User-Agent" = "RipoTeam-RecRoom-Host"
    "Accept" = "application/vnd.github+json"
    "Cache-Control" = "no-cache"
  } -Uri $uri -TimeoutSec 30
  $sha = [string]$result.sha
  if ($sha -notmatch '^[0-9a-fA-F]{40}$') { throw "Could not resolve $Repository@$Reference to an immutable Git commit." }
  return $sha.ToLowerInvariant()
}

$hostSha = Resolve-GitHubCommit $hostRepo $hostRef
$toolsSha = Resolve-GitHubCommit $toolsRepo $toolsRef
$toolDir = Join-Path $PSScriptRoot "recroom-tools"
New-Item -ItemType Directory -Path $toolDir -Force | Out-Null

function Raw-Url([string]$Repo, [string]$Sha, [string]$Path) {
  return "https://raw.githubusercontent.com/$Repo/$Sha/$Path"
}

$targets = @(
  [pscustomobject]@{ Name="start-recroom-host.ps1"; Url=(Raw-Url $hostRepo $hostSha "windows-live-agent/start-recroom-host.ps1"); Destination=(Join-Path $PSScriptRoot "start-recroom-host.ps1"); Kind="powershell" },
  [pscustomobject]@{ Name="identify-recroom-client.ps1"; Url=(Raw-Url $hostRepo $hostSha "windows-live-agent/identify-recroom-client.ps1"); Destination=(Join-Path $PSScriptRoot "identify-recroom-client.ps1"); Kind="powershell" },
  [pscustomobject]@{ Name="update-recroom-host.ps1"; Url=(Raw-Url $hostRepo $hostSha "windows-live-agent/update-recroom-host.ps1"); Destination=(Join-Path $PSScriptRoot "update-recroom-host.ps1"); Kind="powershell" },
  [pscustomobject]@{ Name="recroom-agent.ps1"; Url=(Raw-Url $hostRepo $hostSha "windows-live-agent/recroom-agent.ps1"); Destination=(Join-Path $PSScriptRoot "recroom-agent.ps1"); Kind="powershell" },
  [pscustomobject]@{ Name="recroom-capture-agent.ps1"; Url=(Raw-Url $hostRepo $hostSha "windows-live-agent/recroom-capture-agent.ps1"); Destination=(Join-Path $PSScriptRoot "recroom-capture-agent.ps1"); Kind="powershell" },
  [pscustomobject]@{ Name="playtest-recroom-client.ps1"; Url=(Raw-Url $hostRepo $hostSha "windows-live-agent/playtest-recroom-client.ps1"); Destination=(Join-Path $PSScriptRoot "playtest-recroom-client.ps1"); Kind="powershell" },
  [pscustomobject]@{ Name="bootstrap-recroom-host.ps1"; Url=(Raw-Url $hostRepo $hostSha "windows-live-agent/bootstrap-recroom-host.ps1"); Destination=(Join-Path $PSScriptRoot "bootstrap-recroom-host.ps1"); Kind="powershell" },
  [pscustomobject]@{ Name="download-recroom-client.ps1"; Url=(Raw-Url $hostRepo $hostSha "windows-live-agent/download-recroom-client.ps1"); Destination=(Join-Path $PSScriptRoot "download-recroom-client.ps1"); Kind="powershell" },
  [pscustomobject]@{ Name="start-recroom-browser-stream.ps1"; Url=(Raw-Url $hostRepo $hostSha "windows-live-agent/start-recroom-browser-stream.ps1"); Destination=(Join-Path $PSScriptRoot "start-recroom-browser-stream.ps1"); Kind="powershell" },
  [pscustomobject]@{ Name="stop-recroom-browser-stream.ps1"; Url=(Raw-Url $hostRepo $hostSha "windows-live-agent/stop-recroom-browser-stream.ps1"); Destination=(Join-Path $PSScriptRoot "stop-recroom-browser-stream.ps1"); Kind="powershell" },
  [pscustomobject]@{ Name="recroom-web-stream.py"; Url=(Raw-Url $hostRepo $hostSha "windows-live-agent/recroom-web-stream.py"); Destination=(Join-Path $PSScriptRoot "recroom-web-stream.py"); Kind="raw" },
  [pscustomobject]@{ Name="requirements.txt"; Url=(Raw-Url $hostRepo $hostSha "windows-live-agent/requirements.txt"); Destination=(Join-Path $PSScriptRoot "requirements.txt"); Kind="raw" },
  [pscustomobject]@{ Name="host-proxy.mjs"; Url=(Raw-Url $toolsRepo $toolsSha "scripts/host-proxy.mjs"); Destination=(Join-Path $toolDir "host-proxy.mjs"); Kind="node" },
  [pscustomobject]@{ Name="redirect-client-urls.mjs"; Url=(Raw-Url $toolsRepo $toolsSha "scripts/redirect-client-urls.mjs"); Destination=(Join-Path $toolDir "redirect-client-urls.mjs"); Kind="node" },
  [pscustomobject]@{ Name="verify-client.mjs"; Url=(Raw-Url $toolsRepo $toolsSha "scripts/verify-client.mjs"); Destination=(Join-Path $toolDir "verify-client.mjs"); Kind="node" },
  [pscustomobject]@{ Name="scan-client-urls.mjs"; Url=(Raw-Url $toolsRepo $toolsSha "scripts/scan-client-urls.mjs"); Destination=(Join-Path $toolDir "scan-client-urls.mjs"); Kind="node" },
  [pscustomobject]@{ Name="patch-client-urls.mjs"; Url=(Raw-Url $toolsRepo $toolsSha "scripts/patch-client-urls.mjs"); Destination=(Join-Path $toolDir "patch-client-urls.mjs"); Kind="node" }
)

function Get-Hash([string]$Path) {
  if (-not (Test-Path $Path)) { return "" }
  $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
  try {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $bytes = $sha.ComputeHash($stream) } finally { $sha.Dispose() }
  } finally { $stream.Dispose() }
  return (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")
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

$changed = New-Object System.Collections.Generic.List[string]
$tempRoot = Join-Path $env:TEMP ("FluxRecRoomUpdate-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null

try {
  foreach ($target in $targets) {
    $temp = Join-Path $tempRoot $target.Name
    Write-Host "Checking $($target.Name)..." -ForegroundColor DarkCyan
    Invoke-WebRequest -UseBasicParsing -Uri $target.Url -OutFile $temp -TimeoutSec 30
    $downloadedHash = Get-Hash $temp
    if (-not $downloadedHash) { throw "Downloaded update is empty: $($target.Name)" }
    if ($target.Kind -eq "powershell") { Test-PowerShellSyntax $temp }
    if ($target.Kind -eq "node") { Test-NodeSyntax $temp }
    $currentHash = Get-Hash $target.Destination
    if ($currentHash -eq $downloadedHash) { continue }
    $destinationDir = Split-Path -Parent $target.Destination
    New-Item -ItemType Directory -Path $destinationDir -Force | Out-Null
    $staged = "$($target.Destination).update-new"
    Copy-Item $temp $staged -Force
    if (Test-Path $target.Destination) { Copy-Item $target.Destination "$($target.Destination).update-backup" -Force }
    Move-Item $staged $target.Destination -Force
    $changed.Add($target.Name)
  }

  $state = [ordered]@{
    checkedAt = [DateTimeOffset]::UtcNow.ToString("o")
    hostRepository = $hostRepo
    hostRef = $hostRef
    hostCommit = $hostSha
    toolsRepository = $toolsRepo
    toolsRef = $toolsRef
    toolsCommit = $toolsSha
    changed = @($changed)
  }
  $state | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $PSScriptRoot "recroom-agent-state.json") -Encoding UTF8
  if ($changed.Count -gt 0) {
    Write-Host ("Updated Rec Room host tools: " + ($changed -join ", ")) -ForegroundColor Green
    exit 10
  }
  Write-Host "Rec Room host tools are current at $hostSha / $toolsSha." -ForegroundColor Green
  exit 0
} finally {
  Remove-Item $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
}

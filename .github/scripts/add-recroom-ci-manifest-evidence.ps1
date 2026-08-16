param(
  [Parameter(Mandatory = $true)]
  [string]$ClientDir
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $ClientDir -PathType Container)) {
  throw "CI client directory does not exist: $ClientDir"
}

$configDir = Join-Path $ClientDir ".DepotDownloader"
New-Item -ItemType Directory -Force -Path $configDir | Out-Null
$manifest = Join-Path $configDir "471711_6337851004861751095.manifest"
[IO.File]::WriteAllBytes(
  $manifest,
  [Text.Encoding]::ASCII.GetBytes("CI ONLY - depot 471711 manifest 6337851004861751095 fixture")
)

$stream = [IO.File]::Open($manifest, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
try {
  $sha1 = [Security.Cryptography.SHA1]::Create()
  try { $hash = $sha1.ComputeHash($stream) } finally { $sha1.Dispose() }
} finally { $stream.Dispose() }
[IO.File]::WriteAllBytes("$manifest.sha", $hash)

Write-Host "RECROOM_CI_MANIFEST_EVIDENCE_CREATED=true"
Write-Host "Manifest fixture: $manifest"

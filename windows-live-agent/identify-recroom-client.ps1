[CmdletBinding()]
param(
  [string]$Root = "",
  [switch]$Scan,
  [switch]$AsJson
)

$ErrorActionPreference = "Stop"

$Target2022 = [ordered]@{
  label = "May 19 2022 / Under Construction A"
  buildId = "8751857"
  manifestId = "6337851004861751095"
  runtimeId = "recroom-2022-05-19"
  depotId = "471711"
}

$FluxRec2023 = [ordered]@{
  label = "March 7 2023 / Shape Up!"
  buildId = "10679392"
  manifestId = "7859140924515540835"
  runtimeId = "recroom-2023-03-07"
  depotId = "471711"
  exeSha256 = "EA53A04EE3E35C8239266D737D44EF4323563C1B862D3F24C5A111D50A547BB1"
  gameAssemblySha256 = "DA7649561A940FE1EC3DEF4EBE85AF11C7518AA3D0FE923CF04D168AA3F84ECF"
  metadataSha256 = "588953ABFD91DD45F798F26CABDA5DD62572933207886CAE44FCA9A7828AA617"
}

function Find-Layout([string]$Candidate) {
  if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate -PathType Container)) { return $null }
  $resolved = (Resolve-Path -LiteralPath $Candidate).Path
  $exe = @("RecRoom.exe", "Recroom_Release.exe") |
    ForEach-Object { Join-Path $resolved $_ } |
    Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
    Select-Object -First 1
  if (-not $exe) { return $null }

  $assembly = Join-Path $resolved "GameAssembly.dll"
  if (-not (Test-Path -LiteralPath $assembly -PathType Leaf)) { return $null }

  $data = @("RecRoom_Data", "Recroom_Release_Data") |
    ForEach-Object { Join-Path $resolved $_ } |
    Where-Object { Test-Path -LiteralPath $_ -PathType Container } |
    Select-Object -First 1
  if (-not $data) { return $null }

  $metadata = Join-Path $data "il2cpp_data\Metadata\global-metadata.dat"
  if (-not (Test-Path -LiteralPath $metadata -PathType Leaf)) { return $null }

  return [pscustomobject]@{
    root = $resolved
    exe = $exe
    gameAssembly = $assembly
    data = $data
    metadata = $metadata
  }
}

function Hash-File([string]$Path, [string]$Algorithm = "SHA256") {
  $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
  try {
    if ($Algorithm -eq "SHA1") {
      $hasher = [Security.Cryptography.SHA1]::Create()
    } elseif ($Algorithm -eq "SHA256") {
      $hasher = [Security.Cryptography.SHA256]::Create()
    } else {
      throw "Unsupported hash algorithm: $Algorithm"
    }
    try {
      $bytes = $hasher.ComputeHash($stream)
      return (($bytes | ForEach-Object { $_.ToString("X2") }) -join "")
    } finally {
      $hasher.Dispose()
    }
  } finally {
    $stream.Dispose()
  }
}

function Bytes-ToHex([byte[]]$Bytes) {
  if (-not $Bytes) { return "" }
  return (($Bytes | ForEach-Object { $_.ToString("X2") }) -join "")
}

function Get-DepotManifestEvidence([string]$ClientRoot, [string]$DepotId, [string]$ManifestId) {
  $roots = New-Object System.Collections.Generic.List[string]
  $cursor = Get-Item -LiteralPath $ClientRoot
  for ($i = 0; $i -lt 4 -and $cursor; $i++) {
    $roots.Add($cursor.FullName)
    $cursor = $cursor.Parent
  }

  foreach ($base in $roots | Select-Object -Unique) {
    $configDir = Join-Path $base ".DepotDownloader"
    if (-not (Test-Path -LiteralPath $configDir -PathType Container)) { continue }
    foreach ($extension in @("manifest", "bin")) {
      $manifestPath = Join-Path $configDir ("{0}_{1}.{2}" -f $DepotId, $ManifestId, $extension)
      if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { continue }
      $shaPath = "$manifestPath.sha"
      $shaPresent = Test-Path -LiteralPath $shaPath -PathType Leaf
      $shaValid = $false
      $actualSha1 = Hash-File $manifestPath "SHA1"
      $storedSha1 = ""
      if ($shaPresent) {
        try {
          $storedSha1 = Bytes-ToHex ([IO.File]::ReadAllBytes($shaPath))
          $shaValid = ($storedSha1 -eq $actualSha1)
        } catch { $shaValid = $false }
      }
      return [pscustomobject]@{
        found = $true
        path = $manifestPath
        shaPath = if ($shaPresent) { $shaPath } else { "" }
        shaPresent = [bool]$shaPresent
        shaValid = [bool]$shaValid
        actualSha1 = $actualSha1
        storedSha1 = $storedSha1
      }
    }
  }

  return [pscustomobject]@{
    found = $false
    path = ""
    shaPath = ""
    shaPresent = $false
    shaValid = $false
    actualSha1 = ""
    storedSha1 = ""
  }
}

function Identify-Layout($Layout) {
  if (-not $Layout) { return $null }

  $exeItem = Get-Item -LiteralPath $Layout.exe
  $assemblyItem = Get-Item -LiteralPath $Layout.gameAssembly
  $metadataItem = Get-Item -LiteralPath $Layout.metadata
  $exeHash = Hash-File $Layout.exe
  $assemblyHash = Hash-File $Layout.gameAssembly
  $metadataHash = Hash-File $Layout.metadata
  $pathText = [string]$Layout.root

  $exact2023 = (
    $exeHash -eq $FluxRec2023.exeSha256 -and
    $assemblyHash -eq $FluxRec2023.gameAssemblySha256 -and
    $metadataHash -eq $FluxRec2023.metadataSha256
  )

  $evidence2022 = Get-DepotManifestEvidence $Layout.root $Target2022.depotId $Target2022.manifestId
  $evidence2023 = Get-DepotManifestEvidence $Layout.root $FluxRec2023.depotId $FluxRec2023.manifestId

  $pathClaims2022 = (
    $pathText -match [regex]::Escape($Target2022.buildId) -or
    $pathText -match [regex]::Escape($Target2022.manifestId) -or
    $pathText -match '(?i)May[ _.-]*19[ _.-]*2022'
  )
  $pathClaims2023 = (
    $pathText -match [regex]::Escape($FluxRec2023.buildId) -or
    $pathText -match [regex]::Escape($FluxRec2023.manifestId) -or
    $pathText -match '(?i)Mar(ch)?[ _.-]*7[ _.-]*2023'
  )

  $kind = "unknown"
  $runtimeId = ""
  $buildId = ""
  $manifestId = ""
  $confidence = "none"
  $playableBy2022Agent = $false
  $reason = "Client layout is complete, but this binary is not a recognized build."

  if ($evidence2022.found -and $evidence2022.shaValid) {
    $kind = "target-2022"
    $runtimeId = $Target2022.runtimeId
    $buildId = $Target2022.buildId
    $manifestId = $Target2022.manifestId
    $confidence = "verified-steam-manifest-cache"
    $playableBy2022Agent = $true
    $reason = "Exact depot 471711 / manifest 6337851004861751095 cache exists and its DepotDownloader SHA-1 sidecar validates."
  } elseif ($exact2023) {
    $kind = "fluxrec-2023"
    $runtimeId = $FluxRec2023.runtimeId
    $buildId = $FluxRec2023.buildId
    $manifestId = $FluxRec2023.manifestId
    $confidence = "exact-three-file-sha256"
    $reason = "RecRoom.exe + GameAssembly.dll + global-metadata.dat exactly match the pinned March 7 2023 FluxRec client hashes."
  } elseif ($evidence2022.found) {
    $kind = "unverified-2022"
    $runtimeId = $Target2022.runtimeId
    $buildId = $Target2022.buildId
    $manifestId = $Target2022.manifestId
    $confidence = "manifest-cache-integrity-failed"
    $reason = "The May 2022 manifest cache is present, but its DepotDownloader .sha sidecar is missing or does not validate. Host launch is blocked."
  } elseif ($evidence2023.found -and $evidence2023.shaValid) {
    $kind = "unverified-2023"
    $runtimeId = $FluxRec2023.runtimeId
    $buildId = $FluxRec2023.buildId
    $manifestId = $FluxRec2023.manifestId
    $confidence = "steam-manifest-cache-but-hash-mismatch"
    $reason = "The March 2023 manifest cache is valid, but one or more pinned client hashes differ. Host launch is blocked."
  } elseif ($pathClaims2022) {
    $kind = "unverified-2022"
    $runtimeId = $Target2022.runtimeId
    $buildId = $Target2022.buildId
    $manifestId = $Target2022.manifestId
    $confidence = "path-claim-only"
    $reason = "Folder name claims May 19 2022, but no verified DepotDownloader manifest cache exists. Folder names are not trusted."
  } elseif ($pathClaims2023) {
    $kind = "unverified-2023"
    $runtimeId = $FluxRec2023.runtimeId
    $buildId = $FluxRec2023.buildId
    $manifestId = $FluxRec2023.manifestId
    $confidence = "path-claim-only"
    $reason = "Folder name claims March 7 2023, but the exact three-file hashes do not match the pinned FluxRec client."
  }

  $version = $null
  try { $version = $exeItem.VersionInfo } catch {}

  return [pscustomobject]@{
    ok = $true
    root = $Layout.root
    exe = $Layout.exe
    kind = $kind
    runtimeId = $runtimeId
    buildId = $buildId
    manifestId = $manifestId
    confidence = $confidence
    playableBy2022Agent = $playableBy2022Agent
    reason = $reason
    manifestEvidence = [pscustomobject]@{
      target2022 = $evidence2022
      fluxrec2023 = $evidence2023
    }
    fingerprint = [pscustomobject]@{
      exeSha256 = $exeHash
      gameAssemblySha256 = $assemblyHash
      metadataSha256 = $metadataHash
      exeBytes = [int64]$exeItem.Length
      gameAssemblyBytes = [int64]$assemblyItem.Length
      metadataBytes = [int64]$metadataItem.Length
      fileVersion = if ($version) { [string]$version.FileVersion } else { "" }
      productVersion = if ($version) { [string]$version.ProductVersion } else { "" }
    }
  }
}

function Candidate-Roots {
  $seen = @{}
  $items = New-Object System.Collections.Generic.List[string]

  function Add-Candidate([string]$Path) {
    if (-not $Path) { return }
    try { $full = [IO.Path]::GetFullPath($Path) } catch { return }
    if ($seen.ContainsKey($full)) { return }
    $seen[$full] = $true
    $items.Add($full)
  }

  if ($Root) { Add-Candidate $Root }
  if ($env:FLUX_RECROOM_CLIENT_DIR) { Add-Candidate $env:FLUX_RECROOM_CLIENT_DIR }

  if ($env:LOCALAPPDATA) { Add-Candidate (Join-Path $env:LOCALAPPDATA "FluxRecRoom\May 19 2022") }
  Add-Candidate "C:\Games\FluxRecRoom\May 19 2022"

  $searchRoots = @()
  if ($env:USERPROFILE) {
    $searchRoots += (Join-Path $env:USERPROFILE "Downloads")
    $searchRoots += (Join-Path $env:USERPROFILE "Desktop")
    $searchRoots += (Join-Path $env:USERPROFILE "Downloads\DepotDownloader-windows-x64\depots\471711")
    $searchRoots += (Join-Path $env:USERPROFILE "Downloads\DepotDownloader\depots\471711")
  }
  foreach ($drive in @("C:\", "D:\", "E:\")) {
    if (-not (Test-Path -LiteralPath $drive -PathType Container)) { continue }
    $searchRoots += (Join-Path $drive "DepotDownloader-windows-x64\depots\471711")
    $searchRoots += (Join-Path $drive "DepotDownloader\depots\471711")
    $searchRoots += (Join-Path $drive "Games\FluxRecRoom")
  }

  foreach ($searchRoot in $searchRoots | Select-Object -Unique) {
    if (-not (Test-Path -LiteralPath $searchRoot -PathType Container)) { continue }
    Get-ChildItem -LiteralPath $searchRoot -File -Recurse -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -in @("RecRoom.exe", "Recroom_Release.exe") } |
      Select-Object -First 100 |
      ForEach-Object { Add-Candidate $_.Directory.FullName }
  }
  return $items
}

$results = New-Object System.Collections.Generic.List[object]
if ($Scan -or -not $Root) {
  foreach ($candidate in Candidate-Roots) {
    $layout = Find-Layout $candidate
    if (-not $layout) { continue }
    $identity = Identify-Layout $layout
    if ($identity) { $results.Add($identity) }
  }
} else {
  $layout = Find-Layout $Root
  if ($layout) { $results.Add((Identify-Layout $layout)) }
}

$rank = @{ 'target-2022' = 0; 'fluxrec-2023' = 1; 'unverified-2022' = 7; 'unverified-2023' = 8; 'unknown' = 9 }
$ordered = @($results | Sort-Object @{ Expression = { if ($rank.ContainsKey($_.kind)) { $rank[$_.kind] } else { 99 } } }, root)
$output = [pscustomobject]@{
  ok = $true
  found = $ordered.Count
  preferred = if ($ordered.Count) { $ordered[0] } else { $null }
  clients = $ordered
}

if ($AsJson) {
  Write-Output ($output | ConvertTo-Json -Depth 12 -Compress)
  exit 0
}

if (-not $ordered.Count) {
  Write-Host "No complete Rec Room IL2CPP client layouts were found." -ForegroundColor Yellow
} else {
  foreach ($item in $ordered) {
    $color = if ($item.kind -eq 'target-2022') { 'Green' } elseif ($item.kind -eq 'fluxrec-2023') { 'Cyan' } else { 'Yellow' }
    Write-Host "[$($item.kind)] $($item.root)" -ForegroundColor $color
    Write-Host "  confidence: $($item.confidence)"
    Write-Host "  exe sha256: $($item.fingerprint.exeSha256)"
    Write-Host "  $($item.reason)"
  }
}

Write-Output $output

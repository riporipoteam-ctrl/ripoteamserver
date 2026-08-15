param(
  [string]$SteamUsername = "",
  [string]$Destination = (Join-Path $env:LOCALAPPDATA "FluxRecRoom\May 19 2022")
)

$ErrorActionPreference = "Stop"
$AppId = "471710"
$DepotId = "471711"
$ManifestId = "6337851004861751095"

function Test-ClientLayout([string]$Root) {
  if (-not $Root -or -not (Test-Path $Root)) { return $false }
  $exe = @("RecRoom.exe", "Recroom_Release.exe") | ForEach-Object { Join-Path $Root $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $exe) { return $false }
  if (-not (Test-Path (Join-Path $Root "GameAssembly.dll"))) { return $false }
  $data = @("RecRoom_Data", "Recroom_Release_Data") | ForEach-Object { Join-Path $Root $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $data) { return $false }
  return Test-Path (Join-Path $data "il2cpp_data\Metadata\global-metadata.dat")
}

if (Test-ClientLayout $Destination) {
  Write-Host "May 19 2022 Rec Room client is already present at $Destination" -ForegroundColor Green
  Write-Output $Destination
  exit 0
}

$depotDownloader = Get-Command DepotDownloader.exe -ErrorAction SilentlyContinue
if (-not $depotDownloader) { $depotDownloader = Get-Command DepotDownloader -ErrorAction SilentlyContinue }
if (-not $depotDownloader) {
  $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
  if (-not $winget) { throw "DepotDownloader is not installed and winget is unavailable." }
  Write-Host "Installing SteamRE DepotDownloader..." -ForegroundColor DarkCyan
  $install = Start-Process -FilePath $winget.Source -ArgumentList @(
    "install", "--exact", "--id", "SteamRE.DepotDownloader", "--silent",
    "--accept-package-agreements", "--accept-source-agreements"
  ) -Wait -PassThru -NoNewWindow
  if ($install.ExitCode -ne 0) { throw "winget could not install DepotDownloader (exit $($install.ExitCode))." }
  $env:Path = @(
    [Environment]::GetEnvironmentVariable("Path", "Machine"),
    [Environment]::GetEnvironmentVariable("Path", "User"),
    $env:Path
  ) -join ";"
  $depotDownloader = Get-Command DepotDownloader.exe -ErrorAction SilentlyContinue
  if (-not $depotDownloader) { $depotDownloader = Get-Command DepotDownloader -ErrorAction SilentlyContinue }
}
if (-not $depotDownloader) { throw "DepotDownloader installed but its executable could not be found in PATH." }

if (-not $SteamUsername) {
  $SteamUsername = Read-Host "Steam username for an account that owns/has Rec Room in its library"
}
if (-not $SteamUsername) { throw "Steam username is required for the licensed depot download attempt." }

New-Item -ItemType Directory -Path $Destination -Force | Out-Null
Write-Host "Steam will now authenticate locally. Scan/approve the QR prompt in your Steam mobile app." -ForegroundColor Cyan
Write-Host "No Steam password is stored in Flux or sent to RipoTeamServer." -ForegroundColor DarkGray

$arguments = @(
  "-app", $AppId,
  "-depot", $DepotId,
  "-manifest", $ManifestId,
  "-username", $SteamUsername,
  "-qr",
  "-remember-password",
  "-os", "windows",
  "-dir", $Destination,
  "-validate"
)

& $depotDownloader.Source @arguments
if ($LASTEXITCODE -ne 0) {
  throw "Steam/DepotDownloader did not provide the May 19 2022 depot. The Steam account may lack the Rec Room license, or Valve may have blocked this old manifest for authenticated accounts too."
}

if (-not (Test-ClientLayout $Destination)) {
  $nestedExe = Get-ChildItem -Path $Destination -File -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @("RecRoom.exe", "Recroom_Release.exe") } | Select-Object -First 1
  if ($nestedExe -and (Test-ClientLayout $nestedExe.Directory.FullName)) {
    $Destination = $nestedExe.Directory.FullName
  }
}

if (-not (Test-ClientLayout $Destination)) {
  throw "Depot download completed, but the expected Rec Room IL2CPP client layout was not found."
}

Write-Host "May 19 2022 Rec Room client downloaded and verified at $Destination" -ForegroundColor Green
Write-Output $Destination

$dest = "$PSScriptRoot\..\tools\mariadb"
if (!(Test-Path $dest)) { New-Item -ItemType Directory -Path $dest -Force | Out-Null }

$downloadUrl = "https://archive.mariadb.org/mariadb-11.4.3/winx64-packages/mariadb-11.4.3-winx64.zip"
$tempZip = "$env:TEMP\mariadb.zip"
Write-Host "Downloading MariaDB 11.4.3..."
Invoke-WebRequest -Uri $downloadUrl -OutFile $tempZip

Write-Host "Extracting MariaDB..."
$tempExtract = "$env:TEMP\mariadb_extract"
& "C:\Program Files\7-Zip\7z.exe" x $tempZip -o"$tempExtract" -y | Out-Null
$innerFolder = (Get-ChildItem $tempExtract | Where-Object { $_.PSIsContainer } | Select-Object -First 1).FullName
Copy-Item -Path "$innerFolder\*" -Destination $dest -Recurse -Force
Remove-Item $tempZip -Force -ErrorAction SilentlyContinue
Remove-Item $tempExtract -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "Initializing MariaDB data directory..."
& "$dest\bin\mariadb-install-db.exe" --datadir="$dest\data"

Write-Host "MariaDB setup complete!"

$ErrorActionPreference = "Continue"
$resRoot = "c:\Users\ripot\OneDrive\Desktop\flux rp\server-data\resources"

function Clone-Resource($url, $dest) {
    if (!(Test-Path -LiteralPath $dest)) {
        Write-Host "Cloning $url into $dest..."
        git clone --depth 1 $url "$dest"
        if (Test-Path -LiteralPath "$dest\.git") {
            Remove-Item -LiteralPath "$dest\.git" -Recurse -Force -ErrorAction SilentlyContinue
        }
    } else {
        Write-Host "Already exists: $dest"
    }
}

# 1. cfx-server-data
$cfxDest = "$resRoot\[cfx-default]"
if (!(Test-Path -LiteralPath $cfxDest)) {
    Write-Host "Setting up cfx-server-data..."
    $tempCfx = "$env:TEMP\cfx-server-data"
    git clone --depth 1 https://github.com/citizenfx/cfx-server-data.git $tempCfx
    New-Item -ItemType Directory -Path $cfxDest -Force | Out-Null
    Copy-Item -LiteralPath "$tempCfx\resources\*" -Destination $cfxDest -Recurse -Force
    Remove-Item -LiteralPath "$cfxDest\[gameplay]\chat" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $tempCfx -Recurse -Force -ErrorAction SilentlyContinue
}

# 2. oxmysql release
$oxDest = "$resRoot\[standalone]\oxmysql"
if (!(Test-Path -LiteralPath $oxDest)) {
    Write-Host "Downloading oxmysql..."
    New-Item -ItemType Directory -Path "$resRoot\[standalone]" -Force | Out-Null
    $oxZip = "$env:TEMP\oxmysql.zip"
    Invoke-WebRequest -Uri "https://github.com/overextended/oxmysql/releases/download/v2.14.1/oxmysql.zip" -OutFile $oxZip
    & "C:\Program Files\7-Zip\7z.exe" x $oxZip -o"$resRoot\[standalone]" -y | Out-Null
    Remove-Item $oxZip -Force -ErrorAction SilentlyContinue
}

# 3. menuv release
$menuvDest = "$resRoot\[standalone]\menuv"
if (!(Test-Path -LiteralPath $menuvDest)) {
    Write-Host "Downloading menuv..."
    $menuvZip = "$env:TEMP\menuv.zip"
    Invoke-WebRequest -Uri "https://github.com/ThymonA/menuv/releases/download/v1.4.1/menuv_v1.4.1.zip" -OutFile $menuvZip
    New-Item -ItemType Directory -Path $menuvDest -Force | Out-Null
    & "C:\Program Files\7-Zip\7z.exe" x $menuvZip -o"$menuvDest" -y | Out-Null
    Remove-Item $menuvZip -Force -ErrorAction SilentlyContinue
}

# 4. Standalone github repos
$standaloneRepos = @(
    @{ name = "bob74_ipl"; url = "https://github.com/qbcore-fivem/bob74_ipl.git" },
    @{ name = "safecracker"; url = "https://github.com/qbcore-fivem/safecracker.git" },
    @{ name = "progressbar"; url = "https://github.com/qbcore-fivem/progressbar.git" },
    @{ name = "interact-sound"; url = "https://github.com/qbcore-fivem/interact-sound.git" },
    @{ name = "connectqueue"; url = "https://github.com/qbcore-fivem/connectqueue.git" },
    @{ name = "PolyZone"; url = "https://github.com/qbcore-fivem/PolyZone.git" }
)
foreach ($repo in $standaloneRepos) {
    Clone-Resource $repo.url "$resRoot\[standalone]\$($repo.name)"
}

# 5. Voice
New-Item -ItemType Directory -Path "$resRoot\[voice]" -Force | Out-Null
Clone-Resource "https://github.com/AkeIke/pma-voice.git" "$resRoot\[voice]\pma-voice"
Clone-Resource "https://github.com/qbcore-fivem/qb-radio.git" "$resRoot\[voice]\qb-radio"

# 6. Default Maps
New-Item -ItemType Directory -Path "$resRoot\[defaultmaps]" -Force | Out-Null
Clone-Resource "https://github.com/qbcore-fivem/hospital_map.git" "$resRoot\[defaultmaps]\hospital_map"
Clone-Resource "https://github.com/qbcore-fivem/dealer_map.git" "$resRoot\[defaultmaps]\dealer_map"
Clone-Resource "https://github.com/qbcore-fivem/prison_map.git" "$resRoot\[defaultmaps]\prison_map"

# 7. QBCore Framework Resources
$qbRepos = @(
    "qb-core", "qb-apartments", "qb-spawn", "qb-houses", "qb-multicharacter",
    "qb-weathersync", "qb-clothing", "qb-phone", "qb-garages", "qb-inventory",
    "qb-radialmenu", "qb-ambulancejob", "qb-policejob", "qb-weapons", "qb-vehiclekeys",
    "qb-drugs", "qb-busjob", "qb-garbagejob", "qb-hotdogjob", "qb-doorlock",
    "qb-shops", "qb-bankrobbery", "qb-banking", "qb-cityhall", "qb-crypto",
    "qb-diving", "qb-houserobbery", "qb-jewelery", "qb-lapraces", "qb-newsjob",
    "qb-vehiclesales", "qb-pawnshop", "qb-recyclejob", "qb-scoreboard", "qb-scrapyard",
    "qb-storerobbery", "qb-streetraces", "qb-taxijob", "qb-towjob", "qb-truckerjob",
    "qb-weed", "qb-vineyard", "qb-truckrobbery", "qb-mechanicjob", "qb-adminmenu",
    "qb-prison", "qb-hud", "qb-target", "qb-menu", "qb-input", "qb-management",
    "qb-fuel", "qb-vehicleshop", "qb-interior", "qb-minigames", "qb-crafting",
    "qb-smallresources"
)
foreach ($qb in $qbRepos) {
    Clone-Resource "https://github.com/qbcore-fivem/$qb.git" "$resRoot\[qb]\$qb"
}

Write-Host "All resources setup complete!"

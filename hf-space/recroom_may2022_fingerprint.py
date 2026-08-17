from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

BUILD_ID = "8751857"
MANIFEST_ID = "6337851004861751095"
DEPOT_ID = "471711"
BUILD_DATE = "2022-05-19T06-50-09Z"
UPDATE_NAME = 'the "Under Construction" edition A'
TOTAL_BYTES = 6790009298
FILE_COUNT = 3693

CRITICAL_FILES: dict[str, dict[str, Any]] = {
    "RecRoom.exe": {
        "path": "RecRoom.exe",
        "size": 650752,
        "sha256": "153860af085119fa5c45d97f109f341fb8dde577dc34207e1bb94c1fcc83d16f",
    },
    "Recroom_Release.exe": {
        "path": "Recroom_Release.exe",
        "size": 1420104,
        "sha256": "f6673b066d8d36da10f7ae21f8f1b021bbe6c7018d2a6bc39bd866df067453d7",
    },
    "GameAssembly.dll": {
        "path": "GameAssembly.dll",
        "size": 132531712,
        "sha256": "2ba4571be2791602386bf02581805e021568ccfeacce6cd265450974b7ea8f27",
    },
    "UnityPlayer.dll": {
        "path": "UnityPlayer.dll",
        "size": 26030984,
        "sha256": "697b57d82f97326c5410a819a1abfe4a8c13fdcbeea7808ae1254ddbdeb4e744",
    },
    "global-metadata.dat": {
        "path": "RecRoom_Data/il2cpp_data/Metadata/global-metadata.dat",
        "size": 32123860,
        "sha256": "d77f3728ba1f3765941f90de036f6b283301c156db3d7e31932e56c9b06a9b0c",
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(4 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_exact_client(root: Path) -> dict[str, Any]:
    root = root.resolve()
    results: dict[str, Any] = {}
    errors: list[str] = []

    for label, expected in CRITICAL_FILES.items():
        path = root.joinpath(*str(expected["path"]).split("/"))
        item = {
            "path": str(path),
            "exists": path.is_file(),
            "expectedSize": int(expected["size"]),
            "expectedSha256": str(expected["sha256"]),
        }
        if not path.is_file():
            errors.append(f"missing {expected['path']}")
            results[label] = item
            continue
        size = path.stat().st_size
        item["size"] = size
        if size != int(expected["size"]):
            errors.append(f"{expected['path']} size {size} != {expected['size']}")
            results[label] = item
            continue
        actual = _sha256(path)
        item["sha256"] = actual
        if actual != expected["sha256"]:
            errors.append(f"{expected['path']} SHA-256 mismatch")
        results[label] = item

    return {
        "ok": not errors,
        "buildId": BUILD_ID,
        "manifestId": MANIFEST_ID,
        "depotId": DEPOT_ID,
        "buildDate": BUILD_DATE,
        "updateName": UPDATE_NAME,
        "fileCount": FILE_COUNT,
        "totalBytes": TOTAL_BYTES,
        "criticalFiles": results,
        "errors": errors,
    }

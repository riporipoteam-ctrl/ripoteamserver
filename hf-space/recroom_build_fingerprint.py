from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any


_FINGERPRINT_PATH = Path(__file__).with_name("recroom-may-2022-fingerprint.json")
FINGERPRINT: dict[str, Any] = json.loads(_FINGERPRINT_PATH.read_text("utf-8"))
_HASH_CACHE: dict[str, tuple[int, int, str]] = {}
_HASH_LOCK = threading.RLock()


def _sha256(path: Path) -> str:
    stat = path.stat()
    key = str(path.resolve())
    signature = (int(stat.st_size), int(stat.st_mtime_ns))
    with _HASH_LOCK:
        cached = _HASH_CACHE.get(key)
        if cached and cached[:2] == signature:
            return cached[2]
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    with _HASH_LOCK:
        _HASH_CACHE[key] = (signature[0], signature[1], value)
    return value


def verify_client_root(root: Path) -> dict[str, Any]:
    root = root.expanduser()
    checked: dict[str, Any] = {}
    mismatches: list[str] = []
    critical = FINGERPRINT.get("criticalFiles") or {}

    if not root.is_dir():
        return {
            "ok": False,
            "buildId": FINGERPRINT["buildId"],
            "manifestId": FINGERPRINT["manifestId"],
            "root": str(root),
            "mismatches": ["client directory is missing"],
            "files": checked,
        }

    for label, expected in critical.items():
        relative = str(expected["path"])
        path = root.joinpath(*relative.split("/"))
        entry: dict[str, Any] = {
            "path": relative,
            "expectedSize": int(expected["size"]),
            "expectedSha256": str(expected["sha256"]).lower(),
            "exists": path.is_file(),
        }
        if not path.is_file():
            mismatches.append(f"{label}: missing {relative}")
            checked[label] = entry
            continue
        stat = path.stat()
        entry["size"] = int(stat.st_size)
        if stat.st_size != int(expected["size"]):
            mismatches.append(f"{label}: size {stat.st_size} != {expected['size']}")
            checked[label] = entry
            continue
        actual = _sha256(path)
        entry["sha256"] = actual
        entry["match"] = actual == str(expected["sha256"]).lower()
        if not entry["match"]:
            mismatches.append(f"{label}: SHA-256 mismatch")
        checked[label] = entry

    # The old DepotDownloader marker is useful diagnostics but is no longer
    # trusted as build identity. Exact immutable game-file hashes are stronger.
    depot = str(FINGERPRINT["depotId"])
    manifest_id = str(FINGERPRINT["manifestId"])
    manifest_names = [
        root / ".DepotDownloader" / f"{depot}_{manifest_id}.manifest",
        root / "DepotDownloader" / f"{depot}_{manifest_id}.manifest",
    ]
    manifest = next((path for path in manifest_names if path.is_file()), None)

    return {
        "ok": not mismatches,
        "buildId": str(FINGERPRINT["buildId"]),
        "manifestId": manifest_id,
        "depotId": depot,
        "buildDate": str(FINGERPRINT["buildDate"]),
        "fileCount": int(FINGERPRINT["fileCount"]),
        "totalBytes": int(FINGERPRINT["totalBytes"]),
        "fingerprintSha256": str(FINGERPRINT["fingerprintSha256"]),
        "root": str(root),
        "manifestPresent": bool(manifest),
        "manifestPath": str(manifest) if manifest else "",
        "mismatches": mismatches,
        "files": checked,
    }

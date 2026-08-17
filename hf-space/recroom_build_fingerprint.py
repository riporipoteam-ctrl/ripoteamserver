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
            "rootPresent": False,
            "manifestPresent": False,
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

    # The DepotDownloader file is diagnostics only. Exact immutable game-file
    # hashes identify the build even when an authorized archive/copy omitted
    # that downloader-specific folder.
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
        "rootPresent": True,
        "manifestPresent": bool(manifest),
        "manifestPath": str(manifest) if manifest else "",
        "mismatches": mismatches,
        "files": checked,
    }


def guard_wine_pool(pool: Any) -> Any:
    """Make exact build hashes the Wine pool's client-readiness authority."""
    if getattr(pool, "_ripo_exact_build_guard", False):
        return pool

    original_capability = pool.capability

    def guarded_capability() -> dict[str, Any]:
        verification = verify_client_root(Path(pool.client_dir))
        # The legacy pool can stop requiring the downloader marker only after
        # the stronger exact binary fingerprint succeeds.
        if verification.get("ok") and hasattr(pool, "strict_manifest"):
            pool.strict_manifest = False

        base = dict(original_capability())
        checks = dict(base.get("checks") or {})
        checks["fingerprint"] = bool(verification.get("ok"))
        checks["manifest"] = bool(verification.get("manifestPresent"))
        base["checks"] = checks
        base["exactBuild"] = bool(verification.get("ok"))
        base["targetBuild"] = str(FINGERPRINT["buildId"])
        base["targetManifest"] = str(FINGERPRINT["manifestId"])
        base["targetFingerprint"] = str(FINGERPRINT["fingerprintSha256"])
        base["clientFingerprint"] = {
            "ok": bool(verification.get("ok")),
            "buildId": verification.get("buildId"),
            "manifestId": verification.get("manifestId"),
            "manifestPresent": verification.get("manifestPresent"),
            "mismatches": verification.get("mismatches") or [],
        }

        if verification.get("rootPresent") and not verification.get("ok"):
            base["supported"] = False
            base["readyForGame"] = False
            mismatch = "; ".join(str(item) for item in (verification.get("mismatches") or [])[:5])
            base["reason"] = (
                f"server client is not exact Rec Room build {FINGERPRINT['buildId']}: "
                + (mismatch or "critical binary fingerprint mismatch")
            )
            base["warning"] = "RipoTeamServer refuses to launch a mismatched Rec Room build."
        return base

    pool.capability = guarded_capability
    pool._ripo_exact_build_guard = True
    return pool

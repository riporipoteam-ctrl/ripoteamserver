from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any


_FINGERPRINT_PATH = Path(__file__).with_name("recroom-may-2022-fingerprint.json")
FINGERPRINT: dict[str, Any] = json.loads(_FINGERPRINT_PATH.read_text("utf-8"))
_ATTESTATION_NAME = ".ripo-build-attestation.json"
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


def _bootstrap_mode() -> bool:
    return bool(FINGERPRINT.get("bootstrapFingerprintFromTrustedArchive"))


def trusted_archive_url() -> str:
    return str(FINGERPRINT.get("trustedArchiveUrl") or "").strip()


def _discover_critical(root: Path) -> dict[str, dict[str, Any]]:
    critical: dict[str, dict[str, Any]] = {}
    candidates: list[tuple[str, list[str], bool]] = [
        ("RecRoom.exe", ["RecRoom.exe"], True),
        ("Recroom_Release.exe", ["Recroom_Release.exe"], True),
        ("GameAssembly.dll", ["GameAssembly.dll"], False),
        ("UnityPlayer.dll", ["UnityPlayer.dll"], False),
        (
            "global-metadata.dat",
            [
                "RecRoom_Data/il2cpp_data/Metadata/global-metadata.dat",
                "Recroom_Release_Data/il2cpp_data/Metadata/global-metadata.dat",
            ],
            False,
        ),
    ]
    executable_found = False
    for label, relatives, optional in candidates:
        selected: Path | None = None
        relative = ""
        for candidate in relatives:
            path = root.joinpath(*candidate.split("/"))
            if path.is_file():
                selected = path
                relative = candidate
                break
        if selected is None:
            if not optional:
                raise RuntimeError(f"missing required Rec Room file: {relatives[0]}")
            continue
        if label in {"RecRoom.exe", "Recroom_Release.exe"}:
            executable_found = True
        critical[label] = {
            "path": relative,
            "size": int(selected.stat().st_size),
            "sha256": _sha256(selected),
        }
    if not executable_found:
        raise RuntimeError("no Rec Room executable was found in the target archive")
    return critical


def _canonical_fingerprint(critical: dict[str, dict[str, Any]]) -> str:
    order = ["RecRoom.exe", "Recroom_Release.exe", "GameAssembly.dll", "UnityPlayer.dll", "global-metadata.dat"]
    rows: list[str] = []
    for label in order:
        item = critical.get(label)
        if not item:
            continue
        rows.append(f"{item['path']}\0{int(item['size'])}\0{str(item['sha256']).lower()}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def attest_client_root(root: Path, source_url: str) -> dict[str, Any]:
    root = root.expanduser()
    expected_source = trusted_archive_url().rstrip("/")
    if not _bootstrap_mode() or not expected_source:
        return verify_client_root(root)
    if source_url.strip().rstrip("/") != expected_source:
        raise RuntimeError("Bootstrap attestation is restricted to the pinned Aug 25 2021 archive URL.")
    if not root.is_dir():
        raise RuntimeError("Cannot attest a missing Rec Room client directory.")

    critical = _discover_critical(root)
    fingerprint = _canonical_fingerprint(critical)
    payload = {
        "schema": 1,
        "buildId": str(FINGERPRINT["buildId"]),
        "manifestId": str(FINGERPRINT["manifestId"]),
        "depotId": str(FINGERPRINT["depotId"]),
        "buildDate": str(FINGERPRINT["buildDate"]),
        "sourceArchive": expected_source,
        "fileCount": len(critical),
        "totalBytes": sum(int(item["size"]) for item in critical.values()),
        "fingerprintSha256": fingerprint,
        "criticalFiles": critical,
    }
    path = root / _ATTESTATION_NAME
    temp = path.with_name(path.name + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)
    return verify_client_root(root)


def _load_attestation(root: Path) -> dict[str, Any] | None:
    path = root / _ATTESTATION_NAME
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _verification_base(root: Path, *, root_present: bool) -> dict[str, Any]:
    return {
        "ok": False,
        "buildId": str(FINGERPRINT["buildId"]),
        "manifestId": str(FINGERPRINT["manifestId"]),
        "depotId": str(FINGERPRINT["depotId"]),
        "buildDate": str(FINGERPRINT["buildDate"]),
        "root": str(root),
        "rootPresent": root_present,
        "manifestPresent": False,
        "manifestPath": "",
        "mismatches": [],
        "files": {},
        "fingerprintSha256": "",
        "fileCount": 0,
        "totalBytes": 0,
        "attestedFromTrustedArchive": False,
    }


def _manifest(root: Path) -> Path | None:
    depot = str(FINGERPRINT["depotId"])
    manifest_id = str(FINGERPRINT["manifestId"])
    candidates = [
        root / ".DepotDownloader" / f"{depot}_{manifest_id}.manifest",
        root / "DepotDownloader" / f"{depot}_{manifest_id}.manifest",
    ]
    return next((path for path in candidates if path.is_file()), None)


def _verify_expected(root: Path, expected: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    checked: dict[str, Any] = {}
    mismatches: list[str] = []
    critical = expected.get("criticalFiles") if isinstance(expected.get("criticalFiles"), dict) else {}
    for label, raw in critical.items():
        if not isinstance(raw, dict):
            mismatches.append(f"{label}: invalid fingerprint entry")
            continue
        relative = str(raw.get("path") or "")
        expected_size = int(raw.get("size") or 0)
        expected_sha = str(raw.get("sha256") or "").lower()
        path = root.joinpath(*relative.split("/")) if relative else root / "__missing__"
        entry: dict[str, Any] = {
            "path": relative,
            "expectedSize": expected_size,
            "expectedSha256": expected_sha,
            "exists": path.is_file(),
        }
        if not relative or not path.is_file():
            mismatches.append(f"{label}: missing {relative or 'path'}")
            checked[label] = entry
            continue
        size = int(path.stat().st_size)
        entry["size"] = size
        if size != expected_size:
            mismatches.append(f"{label}: size {size} != {expected_size}")
            checked[label] = entry
            continue
        actual = _sha256(path)
        entry["sha256"] = actual
        entry["match"] = bool(expected_sha) and actual == expected_sha
        if not entry["match"]:
            mismatches.append(f"{label}: SHA-256 mismatch")
        checked[label] = entry

    manifest = _manifest(root)
    result.update(
        {
            "ok": bool(critical) and not mismatches,
            "manifestPresent": bool(manifest),
            "manifestPath": str(manifest) if manifest else "",
            "mismatches": mismatches,
            "files": checked,
            "fingerprintSha256": str(expected.get("fingerprintSha256") or ""),
            "fileCount": int(expected.get("fileCount") or len(critical)),
            "totalBytes": int(expected.get("totalBytes") or 0),
        }
    )
    return result


def verify_client_root(root: Path) -> dict[str, Any]:
    root = root.expanduser()
    result = _verification_base(root, root_present=root.is_dir())
    if not root.is_dir():
        result["mismatches"] = ["client directory is missing"]
        return result

    if _bootstrap_mode():
        attestation = _load_attestation(root)
        if not attestation:
            result["mismatches"] = ["trusted Aug 25 2021 build attestation is missing"]
            return result
        identity_ok = (
            str(attestation.get("buildId") or "") == str(FINGERPRINT["buildId"])
            and str(attestation.get("manifestId") or "") == str(FINGERPRINT["manifestId"])
            and str(attestation.get("sourceArchive") or "").rstrip("/") == trusted_archive_url().rstrip("/")
        )
        if not identity_ok:
            result["mismatches"] = ["trusted archive attestation identity mismatch"]
            return result
        result["attestedFromTrustedArchive"] = True
        return _verify_expected(root, attestation, result)

    return _verify_expected(root, FINGERPRINT, result)


def guard_wine_pool(pool: Any) -> Any:
    if getattr(pool, "_ripo_exact_build_guard", False):
        return pool

    original_capability = pool.capability

    def guarded_capability() -> dict[str, Any]:
        verification = verify_client_root(Path(pool.client_dir))
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
        base["targetFingerprint"] = str(verification.get("fingerprintSha256") or FINGERPRINT.get("fingerprintSha256") or "")
        base["clientFingerprint"] = {
            "ok": bool(verification.get("ok")),
            "buildId": verification.get("buildId"),
            "manifestId": verification.get("manifestId"),
            "fingerprintSha256": verification.get("fingerprintSha256"),
            "attestedFromTrustedArchive": bool(verification.get("attestedFromTrustedArchive")),
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

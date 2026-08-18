from __future__ import annotations

from typing import Any

from fastapi import HTTPException

import recroom_build_fingerprint as build_fingerprint
import recroom_client_installer as client_installer


_PATCH_REVISION = "aug25-2021-trusted-archive-attestation-v1"
_ORIGINAL_FIND_CLIENT_ROOT = client_installer._find_client_root
_ORIGINAL_START = client_installer.RecRoomClientInstaller.start


def _bootstrap_enabled() -> bool:
    return bool(build_fingerprint.FINGERPRINT.get("bootstrapFingerprintFromTrustedArchive"))


def _trusted_url() -> str:
    return build_fingerprint.trusted_archive_url().rstrip("/")


def _find_client_root_attested(extracted: Any) -> tuple[Any, dict[str, Any]]:
    if not _bootstrap_enabled():
        return _ORIGINAL_FIND_CLIENT_ROOT(extracted)

    trusted = _trusted_url()
    if not trusted:
        raise RuntimeError("Aug 25 2021 trusted archive URL is not configured.")

    failures: list[str] = []
    for root in client_installer._candidate_roots(extracted):
        try:
            result = build_fingerprint.attest_client_root(root, trusted)
        except Exception as exc:
            failures.append(f"{root}: {type(exc).__name__}: {exc}")
            continue
        if result.get("ok"):
            return root, result
        detail = "; ".join(str(item) for item in (result.get("mismatches") or [])[:6])
        failures.append(f"{root}: {detail or 'trusted archive attestation failed'}")

    suffix = " | ".join(failures[:4])
    raise RuntimeError(
        "Pinned Aug 25 2021 archive did not produce a valid exact Rec Room client attestation."
        + (f" Checked: {suffix}" if suffix else " No Rec Room executable was found.")
    )


def _start_trusted_archive(self: Any, source_url: str, expected_sha256: str = "") -> dict[str, Any]:
    if _bootstrap_enabled():
        trusted = _trusted_url()
        supplied = str(source_url or "").strip().rstrip("/")
        if not trusted or supplied != trusted:
            raise HTTPException(
                status_code=400,
                detail="Initial Aug 25 2021 install is restricted to the pinned RecAgain archive URL.",
            )
    return _ORIGINAL_START(self, source_url, expected_sha256)


client_installer._find_client_root = _find_client_root_attested
client_installer.RecRoomClientInstaller.start = _start_trusted_archive
print(f"Rec Room trusted archive attestation installer hook loaded: {_PATCH_REVISION}")

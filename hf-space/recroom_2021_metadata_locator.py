from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import recroom_https_recnet_fix as transport
import recroom_nameserver_fix as nameserver_fix
from recroom_wine_pool import RecRoomWinePool


_PATCH_REVISION = "aug25-2021-metadata-locator-v1"
_ORIGINAL_PATCH = transport._patch_client_trusted


def _metadata_relative(root: Path) -> Path:
    attestation = root / ".ripo-build-attestation.json"
    try:
        payload = json.loads(attestation.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    critical = payload.get("criticalFiles") if isinstance(payload, dict) else None
    metadata = critical.get("global-metadata.dat") if isinstance(critical, dict) else None
    if isinstance(metadata, dict):
        relative = str(metadata.get("path") or "").strip()
        if relative:
            candidate = Path(relative)
            if root.joinpath(*candidate.parts).is_file():
                return candidate

    configured = Path(str(getattr(transport, "_EXACT_METADATA_RELATIVE", "") or ""))
    candidates = [
        configured,
        Path("RecRoom_Data/il2cpp_data/Metadata/global-metadata.dat"),
        Path("Recroom_Release_Data/il2cpp_data/Metadata/global-metadata.dat"),
    ]
    seen: set[str] = set()
    for relative in candidates:
        key = relative.as_posix()
        if not key or key == "." or key in seen:
            continue
        seen.add(key)
        if root.joinpath(*relative.parts).is_file():
            return relative
    raise RuntimeError("Aug 25 2021 IL2CPP global-metadata.dat could not be located in the attested client.")


def _patch_with_metadata_locator(self: RecRoomWinePool, root: Path, local_base: str) -> int:
    transport._EXACT_METADATA_RELATIVE = _metadata_relative(root)
    return int(_ORIGINAL_PATCH(self, root, local_base) or 0)


transport._patch_client_trusted = _patch_with_metadata_locator
nameserver_fix._patch_client = _patch_with_metadata_locator
RecRoomWinePool._patch_client = _patch_with_metadata_locator  # type: ignore[method-assign]
print(f"Rec Room Aug 2021 metadata locator loaded: {_PATCH_REVISION}")

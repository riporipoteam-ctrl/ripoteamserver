from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import recroom_broker
import recroom_vm_bridge
import recroom_vm_pool
import recroom_wine_pool
import recroom_client_installer
import recroom_public


_PATCH_REVISION = "aug25-2021-broker-alias-v2"
_CANONICAL_BUILD = "recroom-2021-08-25"
_CANONICAL_BUILD_NUMBER = "7225744"
_CANONICAL_MANIFEST = "7611535694620830622"
_CANONICAL_DEPOT = "471711"
_ALIASES = {
    _CANONICAL_BUILD: _CANONICAL_BUILD,
    # Keep existing Flux clients working while their UI label is migrated.
    "recroom-2022-05-19": _CANONICAL_BUILD,
}
_ORIGINAL_ALLOCATE = recroom_broker.RecRoomBroker.allocate

# Keep every server-side allocator on the same Aug 25 2021 contract. Several
# modules imported TARGET_BUILD_ID by value before this compatibility module
# was loaded, so patch their module globals explicitly rather than relying on
# the Python import alias alone.
recroom_broker.TARGET_BUILD_ID = _CANONICAL_BUILD
recroom_vm_bridge.TARGET_BUILD_ID = _CANONICAL_BUILD
recroom_vm_pool.TARGET_BUILD_ID = _CANONICAL_BUILD
recroom_wine_pool.TARGET_BUILD_ID = _CANONICAL_BUILD
recroom_wine_pool.TARGET_MANIFEST = _CANONICAL_MANIFEST
recroom_wine_pool.TARGET_DEPOT = _CANONICAL_DEPOT
recroom_client_installer.TARGET_MANIFEST = _CANONICAL_MANIFEST
recroom_client_installer.TARGET_DEPOT = _CANONICAL_DEPOT
recroom_public.TARGET_BUILD_ID = _CANONICAL_BUILD

# The managed Wine runtime installs the trusted Aug 25 2021 archive into a
# dedicated path so a stale May 2022 directory can never be selected by a
# default environment.
_data_root = Path(os.environ.get("RIPO_DATA_DIR", str(Path.home() / ".ripo-cloud-pc")))
os.environ.setdefault("RECROOM_WINE_CLIENT_DIR", str(_data_root / "recroom-client-2021"))
os.environ.setdefault("RECROOM_WINE_CLIENT_LABEL", "Aug 25 2021 / Build 7225744")


def _allocate_2021(self: Any, identity_payload: dict[str, Any], build_id: str):
    requested = str(build_id or _CANONICAL_BUILD)
    canonical = _ALIASES.get(requested, requested)
    return _ORIGINAL_ALLOCATE(self, identity_payload, canonical)


recroom_broker.RecRoomBroker.allocate = _allocate_2021
print(
    "Rec Room 2021 broker alias loaded: "
    f"{_PATCH_REVISION} · build {_CANONICAL_BUILD_NUMBER} · manifest {_CANONICAL_MANIFEST}"
)

from __future__ import annotations

from typing import Any

import recroom_broker


_PATCH_REVISION = "aug25-2021-broker-alias-v1"
_CANONICAL_BUILD = "recroom-2021-08-25"
_ALIASES = {
    _CANONICAL_BUILD: _CANONICAL_BUILD,
    # Keep existing Flux clients working while their UI label is migrated.
    "recroom-2022-05-19": _CANONICAL_BUILD,
}
_ORIGINAL_ALLOCATE = recroom_broker.RecRoomBroker.allocate

recroom_broker.TARGET_BUILD_ID = _CANONICAL_BUILD


def _allocate_2021(self: Any, identity_payload: dict[str, Any], build_id: str):
    requested = str(build_id or _CANONICAL_BUILD)
    canonical = _ALIASES.get(requested, requested)
    return _ORIGINAL_ALLOCATE(self, identity_payload, canonical)


recroom_broker.RecRoomBroker.allocate = _allocate_2021
print(f"Rec Room 2021 broker alias loaded: {_PATCH_REVISION}")

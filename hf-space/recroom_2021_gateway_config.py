from __future__ import annotations

import recroom_gateway
import recroom_wine_pool
import recroom_vm_bridge
import recroom_vm_pool


_CONFIG_REVISION = "aug25-2021-gateway-v3"
_CANONICAL_BUILD_ID = "7225744"
_CANONICAL_RUNTIME_ID = "recroom-2021-08-25"
_CANONICAL_MANIFEST_ID = "7611535694620830622"
_CANONICAL_DEPOT_ID = "471711"

# Align every imported runtime module with the actual Aug 25 2021 client.
# Some modules imported TARGET_BUILD_ID by value, so changing only
# recroom_broker.TARGET_BUILD_ID is not sufficient.
recroom_gateway.TARGET_BUILD_DATE = "2021-08-25"
recroom_gateway.TARGET_BUILD_ID = _CANONICAL_BUILD_ID
recroom_gateway.TARGET_MANIFEST_ID = _CANONICAL_MANIFEST_ID
recroom_gateway.PHOTON_APP_VERSION = "20210827_prod"

recroom_wine_pool.TARGET_BUILD_ID = _CANONICAL_RUNTIME_ID
recroom_wine_pool.TARGET_MANIFEST = _CANONICAL_MANIFEST_ID
recroom_wine_pool.TARGET_DEPOT = _CANONICAL_DEPOT_ID

recroom_vm_bridge.TARGET_BUILD_ID = _CANONICAL_RUNTIME_ID
recroom_vm_pool.TARGET_BUILD_ID = _CANONICAL_RUNTIME_ID

print(f"Rec Room 2021 gateway/Wine/VM identity loaded: {_CONFIG_REVISION}")

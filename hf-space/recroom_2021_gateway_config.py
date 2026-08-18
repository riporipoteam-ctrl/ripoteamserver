from __future__ import annotations

import recroom_gateway
import recroom_wine_pool


_CONFIG_REVISION = "aug25-2021-gateway-v2"

# Match the archived Bento Box A client family while retaining RipoTeam's own
# Photon application id and region overrides. The 2021 revival client family
# identifies itself with the late-August production app version.
recroom_gateway.TARGET_BUILD_DATE = "2021-08-25"
recroom_gateway.TARGET_BUILD_ID = "7225744"
recroom_gateway.TARGET_MANIFEST_ID = "7611535694620830622"
recroom_gateway.PHOTON_APP_VERSION = "20210827_prod"

# RecRoomWinePool resolves these module globals when it checks capabilities and
# manifests, so update them before the broker constructs the pool.
recroom_wine_pool.TARGET_BUILD_ID = "recroom-2021-08-25"
recroom_wine_pool.TARGET_MANIFEST = "7611535694620830622"
recroom_wine_pool.TARGET_DEPOT = "471711"

print(f"Rec Room 2021 gateway/Wine identity loaded: {_CONFIG_REVISION}")

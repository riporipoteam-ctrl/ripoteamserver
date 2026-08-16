from __future__ import annotations

import os

from fastapi.middleware.cors import CORSMiddleware

from app_server import DATA_DIR, DISPLAY, SERVER_TIKTOK_CONNECT, TIKTOK_AI, app, authorize
from recroom_broker import install_recroom_broker_routes
from recroom_capture import install_recroom_capture_routes
from recroom_gateway import RecRoomGateway, install_recroom_gateway_routes
from recroom_public import install_recroom_public_routes
from recroom_vm_bridge import attach_recroom_vm_pool
from server_live_broadcaster import ServerLiveBroadcaster, install_server_live_routes

_DEFAULT_CORS_ORIGINS = (
    "https://riporipoteam-ctrl.github.io,"
    "http://localhost:3000,"
    "http://127.0.0.1:3000"
)
RECROOM_CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("RECROOM_CORS_ORIGINS", _DEFAULT_CORS_ORIGINS).split(",")
    if origin.strip()
]

# The live compatibility gateway is hosted by this same Space. Reuse the
# existing ADMIN_TOKEN as the private host/broker key when dedicated Rec Room
# keys have not been configured, so the deployment does not require another HF
# secret just to start. Disposable Windows VMs receive this key only through
# their per-session read-only configuration ISO.
_SPACE_URL = os.environ.get("RECROOM_PUBLIC_BASE_URL", "https://echoxr-ripoteam-cloud-pc.hf.space").rstrip("/")
os.environ.setdefault("RECROOM_GATEWAY_URL", _SPACE_URL)
_admin_token = os.environ.get("ADMIN_TOKEN", "").strip()
if _admin_token:
    os.environ.setdefault("RECROOM_BROKER_KEY", _admin_token)
    os.environ.setdefault("RECROOM_HOST_KEY", _admin_token)

# Flux is published as a static GitHub Pages app, so its browser must be able to
# call the authenticated Rec Room control plane directly. Authentication remains
# token-based; no cross-origin cookies are used or accepted here.
app.add_middleware(
    CORSMiddleware,
    allow_origins=RECROOM_CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

SERVER_LIVE_BROADCASTER = ServerLiveBroadcaster(
    TIKTOK_AI,
    SERVER_TIKTOK_CONNECT,
    DATA_DIR / "tiktok-server-live",
    authorize,
    DISPLAY,
)
install_server_live_routes(app, SERVER_LIVE_BROADCASTER)

# Firebase-token exchange, profile/save state, May-2022 compatibility routes and
# Photon config live on the same public service the Windows guest uses.
RECROOM_GATEWAY = RecRoomGateway(DATA_DIR / "recroom-gateway")
install_recroom_gateway_routes(app, RECROOM_GATEWAY)

RECROOM_BROKER = install_recroom_broker_routes(app, DATA_DIR / "recroom-broker")
RECROOM_VM_POOL = attach_recroom_vm_pool(app, RECROOM_BROKER, DATA_DIR)
RECROOM_CAPTURE = install_recroom_capture_routes(app, RECROOM_BROKER, DATA_DIR / "recroom-captures")
install_recroom_public_routes(app, RECROOM_BROKER, RECROOM_CAPTURE)

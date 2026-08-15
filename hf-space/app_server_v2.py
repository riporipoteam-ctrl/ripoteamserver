from __future__ import annotations

import os

from fastapi.middleware.cors import CORSMiddleware

from app_server import DATA_DIR, DISPLAY, SERVER_TIKTOK_CONNECT, TIKTOK_AI, app, authorize
from recroom_broker import install_recroom_broker_routes
from recroom_capture import install_recroom_capture_routes
from recroom_public import install_recroom_public_routes
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

RECROOM_BROKER = install_recroom_broker_routes(app, DATA_DIR / "recroom-broker")
RECROOM_CAPTURE = install_recroom_capture_routes(app, RECROOM_BROKER, DATA_DIR / "recroom-captures")
install_recroom_public_routes(app, RECROOM_BROKER, RECROOM_CAPTURE)

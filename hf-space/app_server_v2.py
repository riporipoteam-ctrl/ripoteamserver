from __future__ import annotations

import os

from fastapi.middleware.cors import CORSMiddleware

from app_server import DATA_DIR, DISPLAY, SERVER_TIKTOK_CONNECT, TIKTOK_AI, app, authorize
from recroom_autoload import install_into_live_app
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

# Mount the current Rec Room runtime synchronously. sitecustomize also starts a
# guarded autoload thread for the normal Gradio environment, but Docker startup
# must not depend on a race between Uvicorn imports and that background thread.
_RECROOM = install_into_live_app(app, DATA_DIR)
RIPO_RECROOM_GATEWAY = _RECROOM.get("gateway")
RIPO_RECROOM_BROKER = _RECROOM.get("broker")
RIPO_RECROOM_VM_POOL = _RECROOM.get("vmPool")
RIPO_RECROOM_WINE_POOL = _RECROOM.get("winePool")
RIPO_RECROOM_CLIENT_INSTALLER = _RECROOM.get("clientInstaller")
RIPO_RECROOM_CAPTURE = _RECROOM.get("capture")

from __future__ import annotations

import os

from fastapi.middleware.cors import CORSMiddleware

from app_server import DATA_DIR, DISPLAY, SERVER_TIKTOK_CONNECT, TIKTOK_AI, app, authorize
from recroom_broker import install_recroom_broker_routes
from recroom_build_fingerprint import guard_wine_pool
from recroom_capture import install_recroom_capture_routes
from recroom_client_installer import install_recroom_client_installer_routes
from recroom_gateway import RecRoomGateway, install_recroom_gateway_routes
from recroom_launch_wait_fix import install_launch_wait_fix
from recroom_public import install_recroom_public_routes
import recroom_wine_prefix_fix  # noqa: F401
import recroom_wine_runtime_fix  # noqa: F401
import recroom_nameserver_fix  # noqa: F401
import recroom_black_viewport_fix  # noqa: F401
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

_SPACE_URL = os.environ.get("RECROOM_PUBLIC_BASE_URL", "https://echoxr-ripoteam-cloud-pc.hf.space").rstrip("/")
os.environ.setdefault("RECROOM_GATEWAY_URL", _SPACE_URL)
_admin_token = os.environ.get("ADMIN_TOKEN", "").strip()
if _admin_token:
    os.environ.setdefault("RECROOM_BROKER_KEY", _admin_token)
    os.environ.setdefault("RECROOM_HOST_KEY", _admin_token)

os.environ.setdefault(
    "RECROOM_WINE_CLIENT_ARCHIVE_URL",
    "https://archive.recagain.site/download/2022-05-19T06-50-09Z",
)
os.environ.setdefault("RECROOM_STARTING_TTL_SECONDS", "900")
os.environ.setdefault("RECROOM_CLIENT_WAIT_SECONDS", "720")

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

RECROOM_GATEWAY = RecRoomGateway(DATA_DIR / "recroom-gateway")
install_recroom_gateway_routes(app, RECROOM_GATEWAY)

RECROOM_BROKER = install_recroom_broker_routes(app, DATA_DIR / "recroom-broker")
RECROOM_VM_POOL = attach_recroom_vm_pool(app, RECROOM_BROKER, DATA_DIR)
RECROOM_WINE_POOL = getattr(RECROOM_BROKER, "wine_pool", None)
RECROOM_CLIENT_INSTALLER = None
if RECROOM_WINE_POOL is not None:
    guard_wine_pool(RECROOM_WINE_POOL)
    RECROOM_CLIENT_INSTALLER = install_recroom_client_installer_routes(
        app,
        RECROOM_BROKER,
        RECROOM_WINE_POOL,
        DATA_DIR / "recroom-client-installer",
    )
    install_launch_wait_fix(RECROOM_BROKER, RECROOM_WINE_POOL, RECROOM_CLIENT_INSTALLER)
RECROOM_CAPTURE = install_recroom_capture_routes(app, RECROOM_BROKER, DATA_DIR / "recroom-captures")
install_recroom_public_routes(app, RECROOM_BROKER, RECROOM_CAPTURE)

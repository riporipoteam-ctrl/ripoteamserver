from __future__ import annotations

from app_server import DATA_DIR, DISPLAY, SERVER_TIKTOK_CONNECT, TIKTOK_AI, app, authorize
from recroom_broker import install_recroom_broker_routes
from server_live_broadcaster import ServerLiveBroadcaster, install_server_live_routes

SERVER_LIVE_BROADCASTER = ServerLiveBroadcaster(
    TIKTOK_AI,
    SERVER_TIKTOK_CONNECT,
    DATA_DIR / "tiktok-server-live",
    authorize,
    DISPLAY,
)
install_server_live_routes(app, SERVER_LIVE_BROADCASTER)

RECROOM_BROKER = install_recroom_broker_routes(app, DATA_DIR / "recroom-broker")

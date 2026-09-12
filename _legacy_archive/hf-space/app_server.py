from __future__ import annotations

from app import DATA_DIR, DISPLAY, TIKTOK_AI, app, authorize
from live_studio_bridge import LiveStudioBridge, install_live_studio_routes
from server_tiktok_connect import ServerTikTokConnect, install_server_tiktok_connect_routes

LIVE_STUDIO_BRIDGE = LiveStudioBridge(DATA_DIR / "live-studio-bridge", TIKTOK_AI, authorize)
install_live_studio_routes(app, LIVE_STUDIO_BRIDGE)

SERVER_TIKTOK_CONNECT = ServerTikTokConnect(TIKTOK_AI, DATA_DIR / "tiktok-server-browser", DISPLAY)
install_server_tiktok_connect_routes(app, SERVER_TIKTOK_CONNECT)

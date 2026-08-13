from __future__ import annotations

from app import DATA_DIR, TIKTOK_AI, app, authorize
from live_studio_bridge import LiveStudioBridge, install_live_studio_routes

LIVE_STUDIO_BRIDGE = LiveStudioBridge(DATA_DIR / "live-studio-bridge", TIKTOK_AI, authorize)
install_live_studio_routes(app, LIVE_STUDIO_BRIDGE)

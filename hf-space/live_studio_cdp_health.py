from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from live_studio_cdp import _targets


def install_live_studio_cdp_health_route(app: Any) -> None:
    @app.get("/api/tiktok/live-studio-linux/ui-capabilities")
    async def live_studio_ui_capabilities() -> JSONResponse:
        targets = _targets()
        return JSONResponse(
            {
                "ok": True,
                "cdp_ready": bool(targets),
                "target_count": len(targets),
                "localhost_only": True,
            }
        )

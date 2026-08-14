from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def install_live_studio_cdp_health_route(app: Any, bridge: Any) -> None:
    @app.get("/api/tiktok/live-studio-linux/ui-capabilities")
    async def live_studio_ui_capabilities() -> JSONResponse:
        try:
            data = bridge.status()
        except Exception as exc:
            data = {
                "ok": True,
                "control": "visible-window-ocr",
                "visible_ui_ready": False,
                "ocr_ready": False,
                "go_live_available": False,
                "login_required": False,
                "safe_action_labels": [],
                "visible_ui_error": str(exc)[:500],
            }
        # Public endpoint returns only coarse capability flags. Never return OCR
        # text, cookies, account data, screen pixels, or private app target URLs.
        return JSONResponse(
            {
                "ok": True,
                "control": str(data.get("control") or "visible-window-ocr"),
                "cdp_ready": False,
                "target_count": 0,
                "localhost_only": True,
                "visible_ui_ready": bool(data.get("visible_ui_ready")),
                "ocr_ready": bool(data.get("ocr_ready")),
                "go_live_available": bool(data.get("go_live_available")),
                "login_required": bool(data.get("login_required")),
                "confirm_available": bool(data.get("confirm_available")),
                "continue_available": bool(data.get("continue_available")),
                "guest_controls_visible": bool(data.get("guest_controls_visible")),
                "microphone_controls_visible": bool(data.get("microphone_controls_visible")),
                "safe_action_labels": list(data.get("safe_action_labels") or [])[:8],
                "visible_ui_error": str(data.get("visible_ui_error") or "")[:300],
            }
        )

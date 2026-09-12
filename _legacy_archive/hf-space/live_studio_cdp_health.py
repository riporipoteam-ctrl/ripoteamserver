from __future__ import annotations

import re
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
                "cdp_ready": False,
                "target_count": 0,
                "actions": [],
                "visible_ui_ready": False,
                "ocr_ready": False,
                "go_live_available": False,
                "login_required": False,
                "visible_ui_error": str(exc)[:500],
            }

        actions = [re.sub(r"\s+", " ", str(x)).strip()[:120] for x in (data.get("actions") or [])]
        go_live = any(re.fullmatch(r"(?i)\s*(go\s*live|start\s*live|start\s*stream(?:ing)?)\s*", x or "") for x in actions)
        login = any(re.search(r"(?i)\b(log\s*in|sign\s*in)\b", x or "") for x in actions)
        confirm = any(re.fullmatch(r"(?i)\s*(confirm|go\s*live|start\s*live)\s*", x or "") for x in actions)
        cont = any(re.fullmatch(r"(?i)\s*(continue|next|allow|authorize)\s*", x or "") for x in actions)

        return JSONResponse(
            {
                "ok": True,
                "control": "localhost-electron-cdp" if data.get("cdp_ready") else "visible-window-ocr",
                "cdp_ready": bool(data.get("cdp_ready")),
                "target_count": int(data.get("target_count") or 0),
                "localhost_only": True,
                "visible_ui_ready": bool(data.get("visible_ui_ready")),
                "ocr_ready": bool(data.get("ocr_ready", True)),
                "go_live_available": bool(go_live or data.get("go_live_available")),
                "login_required": bool(login or data.get("login_required")),
                "confirm_available": bool(confirm or data.get("confirm_available")),
                "continue_available": bool(cont or data.get("continue_available")),
                "guest_controls_visible": bool(data.get("guest_controls_visible")),
                "microphone_controls_visible": bool(data.get("microphone_controls_visible")),
                "safe_action_labels": [
                    label for label in actions[:12]
                    if re.search(r"(?i)go\s*live|start\s*(live|stream)|log\s*in|sign\s*in|confirm|continue|next", label)
                ][:8],
                "visible_ui_error": str(data.get("visible_ui_error") or "")[:300],
            }
        )

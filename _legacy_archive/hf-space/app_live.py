from __future__ import annotations

from pathlib import Path

from fastapi import Header
from fastapi.responses import FileResponse, Response

from app import DATA_DIR, TIKTOK_AI, app, authorize
from live_studio_bridge import LiveStudioBridge, install_live_studio_routes

LIVE_STUDIO_BRIDGE = LiveStudioBridge(DATA_DIR / "live-studio-bridge", TIKTOK_AI, authorize)
install_live_studio_routes(app, LIVE_STUDIO_BRIDGE)


@app.get("/api/tiktok/live-studio/agent/audio")
async def live_studio_agent_audio(x_live_agent_token: str | None = Header(default=None)) -> Response:
    LIVE_STUDIO_BRIDGE._agent(x_live_agent_token)
    row = TIKTOK_AI.pop_audio()
    if not row:
        return Response(status_code=204)
    path = Path(str(row.get("path") or ""))
    if not path.exists():
        return Response(status_code=204)
    return FileResponse(path, media_type="audio/wav", filename=f"ripo-{row.get('id','speech')}.wav")

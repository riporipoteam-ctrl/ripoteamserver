from __future__ import annotations

from typing import Any

from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse

from tiktok_profile_backup import _stop_firefox, export_profile, restore_profile


def install_tiktok_profile_persistence_routes(app: Any, connector: Any) -> None:
    @app.post("/api/tiktok/server-connect/profile/restore")
    async def restore_server_tiktok_profile(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(400, "Expected JSON body.") from exc
        blob = str((payload or {}).get("blob") or "")
        return JSONResponse(restore_profile(connector, blob))

    @app.post("/api/tiktok/server-connect/profile/export")
    async def export_server_tiktok_profile(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        if not connector.ai.session_valid(x_admin_token):
            raise HTTPException(401, "Reconnect TikTok before exporting the server login backup.")
        # Graceful shutdown flushes Firefox cookies.sqlite before the encrypted snapshot.
        _stop_firefox(connector)
        blob, plain_bytes = export_profile(connector)
        return JSONResponse(
            {
                "ok": True,
                "blob": blob,
                "backup_bytes": plain_bytes,
                "persistence": "encrypted-dashboard-backup",
                "message": "TikTok server login encrypted for restart-safe restoration.",
            }
        )

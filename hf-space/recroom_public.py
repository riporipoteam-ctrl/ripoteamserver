from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Body, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse


TARGET_BUILD_ID = "recroom-2022-05-19"


def install_recroom_public_routes(app: Any, broker: Any, capture: Any) -> None:
    """Install browser-facing Rec Room routes for static Flux deployments.

    These routes deliberately do not accept the private Flux broker key. Creating
    a game session requires a Firebase ID token that the broker verifies through
    the Rec Room gateway. Once allocated, session/capture operations require the
    opaque, high-entropy per-session access token returned only to that player.

    Host registration, job polling, stream-ready callbacks and screenshot upload
    remain on the private host-key routes in recroom_broker.py/recroom_capture.py.
    """

    @app.get("/api/recroom-public/status")
    async def recroom_public_status() -> JSONResponse:
        status = broker.status()
        return JSONResponse(
            {
                "ok": True,
                "targetBuild": status.get("targetBuild", TARGET_BUILD_ID),
                "configured": bool(status.get("configured")),
                "onlineHosts": int(status.get("onlineHosts") or 0),
                "sessions": int(status.get("sessions") or 0),
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/recroom-public/sessions")
    async def create_public_recroom_session(
        payload: dict[str, Any] = Body(default_factory=dict),
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        identity = await asyncio.to_thread(broker.verify_flux_user, authorization)
        session, access_token = broker.allocate(
            identity,
            str(payload.get("buildId") or TARGET_BUILD_ID),
        )
        return JSONResponse(
            broker.public_session(session, access_token),
            status_code=202,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/recroom-public/sessions/{session_id}")
    async def get_public_recroom_session(
        session_id: str,
        access_token: str = Query(alias="accessToken"),
    ) -> JSONResponse:
        session = broker.session_for_access(session_id, access_token)
        return JSONResponse(
            broker.public_session(session),
            headers={"Cache-Control": "no-store"},
        )

    @app.delete("/api/recroom-public/sessions/{session_id}")
    async def delete_public_recroom_session(
        session_id: str,
        access_token: str = Query(alias="accessToken"),
    ) -> JSONResponse:
        # Validate ownership before releasing the allocation.
        broker.session_for_access(session_id, access_token)
        broker.release(session_id)
        return JSONResponse(
            {"ok": True, "sessionId": session_id, "state": "released"},
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/recroom-public/sessions/{session_id}/captures")
    async def create_public_capture(
        session_id: str,
        access_token: str = Query(alias="accessToken"),
    ) -> JSONResponse:
        record = capture.request_capture(session_id, access_token)
        return JSONResponse(
            capture.public(record),
            status_code=202,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/recroom-public/sessions/{session_id}/captures/{capture_id}")
    async def public_capture_status(
        session_id: str,
        capture_id: str,
        access_token: str = Query(alias="accessToken"),
    ) -> JSONResponse:
        record = capture.for_access(session_id, capture_id, access_token)
        return JSONResponse(capture.public(record), headers={"Cache-Control": "no-store"})

    @app.get("/api/recroom-public/sessions/{session_id}/captures/{capture_id}/image")
    async def public_capture_image(
        session_id: str,
        capture_id: str,
        access_token: str = Query(alias="accessToken"),
    ) -> FileResponse:
        record = capture.for_access(session_id, capture_id, access_token)
        if record.state != "ready" or not record.image_path or not record.image_path.exists():
            raise HTTPException(status_code=409, detail="Screenshot is not ready yet.")
        return FileResponse(
            record.image_path,
            media_type=record.content_type,
            filename=f"flux-recroom-{capture_id}{record.image_path.suffix}",
            headers={"Cache-Control": "private, no-store"},
        )

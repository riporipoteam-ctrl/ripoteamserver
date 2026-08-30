from __future__ import annotations

import asyncio
import secrets
import threading
import time
from typing import Any

from fastapi import Body, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse


TARGET_BUILD_ID = "recroom-2021-08-25"
PAIRING_TTL_SECONDS = 10 * 60


def install_recroom_public_routes(app: Any, broker: Any, capture: Any) -> None:
    """Install browser-facing Rec Room routes for static Flux deployments.

    Creating a game session requires a Firebase ID token that the broker verifies
    through the Rec Room gateway. Public sessions use the RipoTeamServer VM pool
    when RECROOM_VM_ONLY is enabled; legacy host pairing remains available for
    admin diagnostics but is not required by the Flux player.
    """

    pairing_lock = threading.RLock()
    pairing_codes: dict[str, float] = {}

    def prune_pairing_codes() -> None:
        now = time.time()
        for code, expires_at in list(pairing_codes.items()):
            if expires_at <= now:
                pairing_codes.pop(code, None)

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
                "mode": status.get("mode", "remote"),
                "vmReadyForGame": bool(status.get("vmReadyForGame")),
                "runtimeReadyForGame": bool(status.get("runtimeReadyForGame") or status.get("vmReadyForGame")),
                "serverRuntime": status.get("serverRuntime"),
                "wineRuntime": status.get("wineRuntime"),
                "kvmRuntime": status.get("kvmRuntime"),
                "vmRuntime": status.get("vmRuntime"),
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/recroom-public/host-pairing")
    async def create_host_pairing(
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        identity = await asyncio.to_thread(broker.verify_flux_user, authorization)
        account = identity.get("account") if isinstance(identity, dict) else None
        if not isinstance(account, dict) or not bool(account.get("isAdmin")):
            raise HTTPException(status_code=403, detail="Flux administrator access is required to pair a Windows host.")
        if not broker.host_key:
            raise HTTPException(status_code=503, detail="Rec Room host authentication is not configured.")

        code = secrets.token_urlsafe(15)
        expires_at = time.time() + PAIRING_TTL_SECONDS
        with pairing_lock:
            prune_pairing_codes()
            pairing_codes[code] = expires_at
        broker._audit("host.pairing.create", uid=identity.get("uid"), expires_at=expires_at)
        return JSONResponse(
            {
                "ok": True,
                "pairingCode": code,
                "expiresAtMs": int(expires_at * 1000),
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/recroom-public/host-pairing/claim")
    async def claim_host_pairing(
        payload: dict[str, Any] = Body(default_factory=dict),
    ) -> JSONResponse:
        code = str(payload.get("pairingCode") or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="pairingCode is required.")
        with pairing_lock:
            prune_pairing_codes()
            expires_at = pairing_codes.pop(code, None)
        if not expires_at or expires_at <= time.time():
            raise HTTPException(status_code=401, detail="Pairing code is invalid, expired, or already used.")
        if not broker.host_key:
            raise HTTPException(status_code=503, detail="Rec Room host authentication is not configured.")
        broker._audit("host.pairing.claim")
        return JSONResponse(
            {
                "ok": True,
                "hostKey": broker.host_key,
                "server": str(getattr(broker, "gateway_url", "") or "").rstrip("/"),
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

    def release_for_player(session_id: str, access_token: str) -> JSONResponse:
        broker.session_for_access(session_id, access_token)
        broker.release(session_id)
        return JSONResponse(
            {"ok": True, "sessionId": session_id, "state": "released"},
            headers={"Cache-Control": "no-store"},
        )

    @app.delete("/api/recroom-public/sessions/{session_id}")
    async def delete_public_recroom_session(
        session_id: str,
        access_token: str = Query(alias="accessToken"),
    ) -> JSONResponse:
        return release_for_player(session_id, access_token)

    @app.post("/api/recroom-public/sessions/{session_id}/release")
    async def post_public_recroom_session_release(
        session_id: str,
        access_token: str = Query(alias="accessToken"),
    ) -> JSONResponse:
        return release_for_player(session_id, access_token)

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

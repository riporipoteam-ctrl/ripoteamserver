from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import Body, Header, HTTPException, Query
from fastapi.responses import JSONResponse


TARGET_BUILD_ID = "recroom-2022-05-19"
HOST_STALE_SECONDS = int(os.environ.get("RECROOM_HOST_STALE_SECONDS", "35"))
SESSION_TTL_SECONDS = int(os.environ.get("RECROOM_SESSION_TTL_SECONDS", "7200"))
STARTING_TTL_SECONDS = int(os.environ.get("RECROOM_STARTING_TTL_SECONDS", "300"))


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_equal(left: str | None, right: str) -> bool:
    return bool(left and right and secrets.compare_digest(left, right))


@dataclass
class HostRecord:
    host_id: str
    name: str
    builds: set[str]
    capacity: int = 1
    last_heartbeat: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
    jobs: list[dict[str, Any]] = field(default_factory=list)
    active_sessions: set[str] = field(default_factory=set)

    def online(self) -> bool:
        return time.time() - self.last_heartbeat <= HOST_STALE_SECONDS

    def has_capacity(self) -> bool:
        return self.online() and len(self.active_sessions) < max(1, self.capacity)


@dataclass
class SessionRecord:
    session_id: str
    access_token_hash: str
    uid: str
    account: dict[str, Any]
    host_id: str
    build_id: str
    created_at: float
    expires_at: float
    state: str = "starting"
    stream_url: str | None = None
    error: str | None = None
    host_details: dict[str, Any] = field(default_factory=dict)


class RecRoomBroker:
    """Small control-plane broker; the native Windows game never runs here."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.hosts: dict[str, HostRecord] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self.broker_key = os.environ.get("RECROOM_BROKER_KEY", "")
        self.host_key = os.environ.get("RECROOM_HOST_KEY", "")
        self.gateway_url = os.environ.get("RECROOM_GATEWAY_URL", "").rstrip("/")
        self.audit_path = self.data_dir / "audit.jsonl"

    def _audit(self, event: str, **fields: Any) -> None:
        safe = {
            "ts": time.time(),
            "event": event,
            **{key: value for key, value in fields.items() if key not in {"token", "access_token", "firebase_token", "recnet_session_token"}},
        }
        try:
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(safe, separators=(",", ":")) + "\n")
        except OSError:
            pass

    def authorize_flux(self, key: str | None) -> None:
        if not self.broker_key:
            raise HTTPException(status_code=503, detail="RECROOM_BROKER_KEY is not configured.")
        if not _safe_equal(key, self.broker_key):
            raise HTTPException(status_code=401, detail="Invalid Flux broker key.")

    def authorize_host(self, key: str | None) -> None:
        if not self.host_key:
            raise HTTPException(status_code=503, detail="RECROOM_HOST_KEY is not configured.")
        if not _safe_equal(key, self.host_key):
            raise HTTPException(status_code=401, detail="Invalid Rec Room host key.")

    def verify_flux_user(self, authorization: str | None) -> dict[str, Any]:
        if not self.gateway_url:
            raise HTTPException(status_code=503, detail="RECROOM_GATEWAY_URL is not configured.")
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Flux Firebase token required.")
        firebase_token = authorization[7:].strip()
        if not firebase_token:
            raise HTTPException(status_code=401, detail="Flux Firebase token required.")

        body = json.dumps({"idToken": firebase_token}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.gateway_url}/flux/auth/firebase",
            data=body,
            method="POST",
            headers={"content-type": "application/json", "user-agent": "RipoTeam-RecRoom-Broker/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in {400, 401, 403}:
                raise HTTPException(status_code=401, detail="Flux identity could not be verified.") from exc
            raise HTTPException(status_code=503, detail=f"Rec Room gateway returned HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=503, detail="Rec Room identity gateway is unavailable.") from exc

        if not payload.get("ok") or not payload.get("sessionToken") or not isinstance(payload.get("account"), dict):
            raise HTTPException(status_code=502, detail="Rec Room gateway returned an invalid identity response.")
        return payload

    def cleanup(self) -> None:
        now = time.time()
        with self.lock:
            expired: list[str] = []
            for session_id, session in self.sessions.items():
                deadline = session.expires_at
                if session.state == "starting":
                    deadline = min(deadline, session.created_at + STARTING_TTL_SECONDS)
                if now >= deadline:
                    expired.append(session_id)
            for session_id in expired:
                self._release_locked(session_id, "expired")

    def register_host(self, payload: dict[str, Any]) -> HostRecord:
        host_id = str(payload.get("hostId") or "").strip()
        if not host_id or len(host_id) > 96:
            raise HTTPException(status_code=400, detail="hostId is required.")
        builds = {str(item) for item in payload.get("builds", []) if str(item)}
        if not builds:
            builds = {TARGET_BUILD_ID}
        capacity = max(1, min(8, int(payload.get("capacity") or 1)))
        name = str(payload.get("name") or host_id)[:96]
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}

        with self.lock:
            existing = self.hosts.get(host_id)
            if existing:
                existing.name = name
                existing.builds = builds
                existing.capacity = capacity
                existing.last_heartbeat = time.time()
                existing.metadata = metadata
                host = existing
            else:
                host = HostRecord(
                    host_id=host_id,
                    name=name,
                    builds=builds,
                    capacity=capacity,
                    metadata=metadata,
                )
                self.hosts[host_id] = host
        self._audit("host.register", host_id=host_id, builds=sorted(builds), capacity=capacity)
        return host

    def heartbeat(self, host_id: str, payload: dict[str, Any]) -> HostRecord:
        with self.lock:
            host = self.hosts.get(host_id)
            if not host:
                raise HTTPException(status_code=404, detail="Host is not registered.")
            host.last_heartbeat = time.time()
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                host.metadata.update(metadata)
            return host

    def allocate(self, identity_payload: dict[str, Any], build_id: str) -> tuple[SessionRecord, str]:
        self.cleanup()
        if build_id != TARGET_BUILD_ID:
            raise HTTPException(status_code=400, detail=f"Unsupported build {build_id!r}.")

        account = identity_payload["account"]
        recnet_session_token = str(identity_payload["sessionToken"])
        uid = str(account.get("uid") or identity_payload.get("uid") or "")
        # The gateway's account response currently uses numeric accountId and may
        # not expose Firebase uid. Keep a stable private broker owner key derived
        # from the verified identity response when uid is absent.
        if not uid:
            uid = f"account:{account.get('accountId', 'unknown')}"

        with self.lock:
            candidates = [
                host
                for host in self.hosts.values()
                if build_id in host.builds and host.has_capacity()
            ]
            candidates.sort(key=lambda host: (len(host.active_sessions), -host.last_heartbeat, host.host_id))
            if not candidates:
                raise HTTPException(status_code=503, detail="No online Windows Rec Room host has free capacity.")

            host = candidates[0]
            session_id = str(uuid.uuid4())
            access_token = secrets.token_urlsafe(32)
            now = time.time()
            session = SessionRecord(
                session_id=session_id,
                access_token_hash=_hash_token(access_token),
                uid=uid,
                account=account,
                host_id=host.host_id,
                build_id=build_id,
                created_at=now,
                expires_at=now + SESSION_TTL_SECONDS,
            )
            self.sessions[session_id] = session
            host.active_sessions.add(session_id)
            host.jobs.append(
                {
                    "type": "start-session",
                    "sessionId": session_id,
                    "buildId": build_id,
                    "gatewayUrl": self.gateway_url,
                    "recnetSessionToken": recnet_session_token,
                    "account": {
                        "accountId": account.get("accountId"),
                        "username": account.get("username"),
                        "displayName": account.get("displayName"),
                        "isAdmin": bool(account.get("isAdmin")),
                    },
                }
            )

        self._audit("session.allocate", session_id=session_id, host_id=host.host_id, build_id=build_id)
        return session, access_token

    def session_for_access(self, session_id: str, access_token: str) -> SessionRecord:
        self.cleanup()
        with self.lock:
            session = self.sessions.get(session_id)
            if not session:
                raise HTTPException(status_code=404, detail="Rec Room session not found.")
            if not _safe_equal(_hash_token(access_token), session.access_token_hash):
                raise HTTPException(status_code=401, detail="Invalid session access token.")
            return session

    def next_job(self, host_id: str) -> dict[str, Any] | None:
        self.cleanup()
        with self.lock:
            host = self.hosts.get(host_id)
            if not host:
                raise HTTPException(status_code=404, detail="Host is not registered.")
            host.last_heartbeat = time.time()
            if not host.jobs:
                return None
            return host.jobs.pop(0)

    def mark_ready(self, host_id: str, session_id: str, payload: dict[str, Any]) -> SessionRecord:
        stream_url = str(payload.get("streamUrl") or "").strip()
        allow_http = os.environ.get("RECROOM_ALLOW_HTTP_STREAMS", "0") == "1"
        valid = stream_url.startswith("https://") or (allow_http and stream_url.startswith("http://"))
        if not valid:
            raise HTTPException(status_code=400, detail="streamUrl must be HTTPS.")

        with self.lock:
            session = self.sessions.get(session_id)
            if not session or session.host_id != host_id:
                raise HTTPException(status_code=404, detail="Assigned session not found for this host.")
            session.stream_url = stream_url
            session.state = "ready"
            session.host_details = {
                "processId": payload.get("processId"),
                "resolution": payload.get("resolution"),
                "streamer": payload.get("streamer"),
            }
            self.hosts[host_id].last_heartbeat = time.time()
        self._audit("session.ready", session_id=session_id, host_id=host_id)
        return session

    def mark_failed(self, host_id: str, session_id: str, error: str) -> None:
        with self.lock:
            session = self.sessions.get(session_id)
            if not session or session.host_id != host_id:
                raise HTTPException(status_code=404, detail="Assigned session not found for this host.")
            session.state = "failed"
            session.error = error[:500] or "Windows host failed to start Rec Room."
            host = self.hosts.get(host_id)
            if host:
                host.active_sessions.discard(session_id)
        self._audit("session.failed", session_id=session_id, host_id=host_id)

    def release(self, session_id: str) -> None:
        with self.lock:
            self._release_locked(session_id, "released")

    def _release_locked(self, session_id: str, reason: str) -> None:
        session = self.sessions.get(session_id)
        if not session:
            return
        host = self.hosts.get(session.host_id)
        if host:
            host.active_sessions.discard(session_id)
            host.jobs.append({"type": "stop-session", "sessionId": session_id, "reason": reason})
        session.state = reason
        self.sessions.pop(session_id, None)
        self._audit("session.release", session_id=session_id, host_id=session.host_id, reason=reason)

    def public_session(self, session: SessionRecord, access_token: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": session.state not in {"failed"},
            "mode": "remote",
            "sessionId": session.session_id,
            "state": session.state,
            "streamUrl": session.stream_url,
            "hostId": session.host_id,
            "buildId": session.build_id,
            "expiresAtMs": int(session.expires_at * 1000),
            "error": session.error,
        }
        if access_token is not None:
            payload["sessionAccessToken"] = access_token
        return payload

    def status(self) -> dict[str, Any]:
        self.cleanup()
        with self.lock:
            hosts = [
                {
                    "hostId": host.host_id,
                    "name": host.name,
                    "online": host.online(),
                    "builds": sorted(host.builds),
                    "capacity": host.capacity,
                    "activeSessions": len(host.active_sessions),
                    "lastHeartbeatAgeSeconds": round(time.time() - host.last_heartbeat, 1),
                }
                for host in self.hosts.values()
            ]
            return {
                "ok": True,
                "targetBuild": TARGET_BUILD_ID,
                "configured": bool(self.broker_key and self.host_key and self.gateway_url),
                "onlineHosts": sum(1 for host in self.hosts.values() if host.online()),
                "sessions": len(self.sessions),
                "hosts": hosts,
            }


def install_recroom_broker_routes(app: Any, data_dir: Path) -> RecRoomBroker:
    broker = RecRoomBroker(data_dir)

    @app.get("/api/recroom/status")
    async def recroom_status(x_flux_broker_key: str | None = Header(default=None)) -> JSONResponse:
        broker.authorize_flux(x_flux_broker_key)
        return JSONResponse(broker.status())

    @app.post("/api/recroom/sessions")
    async def create_recroom_session(
        payload: dict[str, Any] = Body(default_factory=dict),
        authorization: str | None = Header(default=None),
        x_flux_broker_key: str | None = Header(default=None),
    ) -> JSONResponse:
        broker.authorize_flux(x_flux_broker_key)
        identity = await __import__("asyncio").to_thread(broker.verify_flux_user, authorization)
        session, access_token = broker.allocate(identity, str(payload.get("buildId") or TARGET_BUILD_ID))
        return JSONResponse(broker.public_session(session, access_token), status_code=202)

    @app.get("/api/recroom/sessions/{session_id}")
    async def get_recroom_session(
        session_id: str,
        access_token: str = Query(alias="accessToken"),
        x_flux_broker_key: str | None = Header(default=None),
    ) -> JSONResponse:
        broker.authorize_flux(x_flux_broker_key)
        session = broker.session_for_access(session_id, access_token)
        return JSONResponse(broker.public_session(session))

    @app.delete("/api/recroom/sessions/{session_id}")
    async def delete_recroom_session(
        session_id: str,
        access_token: str = Query(alias="accessToken"),
        x_flux_broker_key: str | None = Header(default=None),
    ) -> JSONResponse:
        broker.authorize_flux(x_flux_broker_key)
        broker.session_for_access(session_id, access_token)
        broker.release(session_id)
        return JSONResponse({"ok": True, "sessionId": session_id, "state": "released"})

    @app.post("/api/recroom/hosts/register")
    async def register_recroom_host(
        payload: dict[str, Any] = Body(default_factory=dict),
        x_recroom_host_key: str | None = Header(default=None),
    ) -> JSONResponse:
        broker.authorize_host(x_recroom_host_key)
        host = broker.register_host(payload)
        return JSONResponse({"ok": True, "hostId": host.host_id, "heartbeatSeconds": 10})

    @app.post("/api/recroom/hosts/{host_id}/heartbeat")
    async def recroom_host_heartbeat(
        host_id: str,
        payload: dict[str, Any] = Body(default_factory=dict),
        x_recroom_host_key: str | None = Header(default=None),
    ) -> JSONResponse:
        broker.authorize_host(x_recroom_host_key)
        host = broker.heartbeat(host_id, payload)
        return JSONResponse({"ok": True, "hostId": host.host_id, "activeSessions": len(host.active_sessions)})

    @app.get("/api/recroom/hosts/{host_id}/jobs")
    async def recroom_host_job(
        host_id: str,
        x_recroom_host_key: str | None = Header(default=None),
    ) -> JSONResponse:
        broker.authorize_host(x_recroom_host_key)
        job = broker.next_job(host_id)
        return JSONResponse({"ok": True, "job": job})

    @app.post("/api/recroom/hosts/{host_id}/sessions/{session_id}/ready")
    async def recroom_host_ready(
        host_id: str,
        session_id: str,
        payload: dict[str, Any] = Body(default_factory=dict),
        x_recroom_host_key: str | None = Header(default=None),
    ) -> JSONResponse:
        broker.authorize_host(x_recroom_host_key)
        session = broker.mark_ready(host_id, session_id, payload)
        return JSONResponse({"ok": True, "sessionId": session.session_id, "state": session.state})

    @app.post("/api/recroom/hosts/{host_id}/sessions/{session_id}/failed")
    async def recroom_host_failed(
        host_id: str,
        session_id: str,
        payload: dict[str, Any] = Body(default_factory=dict),
        x_recroom_host_key: str | None = Header(default=None),
    ) -> JSONResponse:
        broker.authorize_host(x_recroom_host_key)
        broker.mark_failed(host_id, session_id, str(payload.get("error") or "Host failed to start session"))
        return JSONResponse({"ok": True, "sessionId": session_id, "state": "failed"})

    return broker

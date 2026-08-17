from __future__ import annotations

import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse


CAPTURE_TTL_SECONDS = int(os.environ.get("RECROOM_CAPTURE_TTL_SECONDS", "900"))
CAPTURE_MAX_BYTES = int(os.environ.get("RECROOM_CAPTURE_MAX_BYTES", str(8 * 1024 * 1024)))


@dataclass
class CaptureRecord:
    capture_id: str
    session_id: str
    host_id: str
    created_at: float
    state: str = "queued"
    content_type: str = "image/png"
    image_path: Path | None = None
    error: str | None = None


class RecRoomCaptureService:
    """Screenshot service layered over the active Rec Room runtime.

    External Windows hosts still consume capture jobs through the host queue.
    Server-owned Wine sandboxes capture directly from their authenticated local
    browser-stream worker so there is no fake host-agent queue in production.
    """

    def __init__(self, broker: Any, data_dir: Path):
        self.broker = broker
        self.data_dir = data_dir
        self.images_dir = data_dir / "images"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.records: dict[str, CaptureRecord] = {}
        self.host_queues: dict[str, list[str]] = {}

    def cleanup(self) -> None:
        cutoff = time.time() - CAPTURE_TTL_SECONDS
        with self.lock:
            stale = [capture_id for capture_id, record in self.records.items() if record.created_at < cutoff]
            for capture_id in stale:
                record = self.records.pop(capture_id, None)
                if record and record.image_path:
                    try:
                        record.image_path.unlink(missing_ok=True)
                    except OSError:
                        pass
            valid = set(self.records)
            for host_id, queue in list(self.host_queues.items()):
                self.host_queues[host_id] = [capture_id for capture_id in queue if capture_id in valid]

    def _capture_wine_frame(self, record: CaptureRecord) -> None:
        try:
            pool = getattr(self.broker, "wine_pool", None)
            if pool is None:
                raise RuntimeError("The Wine runtime is not attached to RipoTeamServer.")

            with pool.lock:
                instance = pool.instances.get(record.host_id)
                if not instance or instance.destroying:
                    raise RuntimeError("The Rec Room Wine sandbox is no longer running.")
                port = int(instance.stream_port)
                token = str(instance.stream_token)

            with self.lock:
                current = self.records.get(record.capture_id)
                if not current:
                    return
                current.state = "capturing"

            frame_url = (
                f"http://127.0.0.1:{port}/frame.jpg?token="
                + urllib.parse.quote(token, safe="")
            )
            request = urllib.request.Request(
                frame_url,
                headers={"Cache-Control": "no-store", "User-Agent": "RipoTeamServer-Capture/1.0"},
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                content_type = response.headers.get("content-type", "image/jpeg")
                body = response.read(CAPTURE_MAX_BYTES + 1)
            self.store(record.host_id, record.capture_id, content_type, body)
        except Exception as exc:
            try:
                self.fail(record.host_id, record.capture_id, str(exc))
            except Exception:
                pass

    def request_capture(self, session_id: str, access_token: str) -> CaptureRecord:
        self.cleanup()
        session = self.broker.session_for_access(session_id, access_token)
        if session.state != "ready":
            raise HTTPException(status_code=409, detail="The Rec Room session must be ready before taking a screenshot.")

        provider = ""
        with self.broker.lock:
            host = self.broker.hosts.get(session.host_id)
            if not host or not host.online():
                raise HTTPException(status_code=503, detail="The assigned game host is offline.")
            provider = str((host.metadata or {}).get("provider") or "")

        capture_id = str(uuid.uuid4())
        record = CaptureRecord(
            capture_id=capture_id,
            session_id=session_id,
            host_id=session.host_id,
            created_at=time.time(),
        )
        with self.lock:
            self.records[capture_id] = record
            # Real remote Windows hosts pull jobs. The server-owned Wine runtime
            # has its streamer in this process, so capture it directly instead.
            if provider != "wine":
                self.host_queues.setdefault(session.host_id, []).append(capture_id)
        self.broker._audit("capture.queued", capture_id=capture_id, session_id=session_id, host_id=session.host_id)

        if provider == "wine":
            threading.Thread(
                target=self._capture_wine_frame,
                args=(record,),
                name=f"recroom-wine-capture-{capture_id[:8]}",
                daemon=True,
            ).start()
        return record

    def for_access(self, session_id: str, capture_id: str, access_token: str) -> CaptureRecord:
        self.cleanup()
        self.broker.session_for_access(session_id, access_token)
        with self.lock:
            record = self.records.get(capture_id)
            if not record or record.session_id != session_id:
                raise HTTPException(status_code=404, detail="Screenshot capture not found.")
            return record

    def next_for_host(self, host_id: str) -> dict[str, Any] | None:
        self.cleanup()
        with self.broker.lock:
            host = self.broker.hosts.get(host_id)
            if not host:
                raise HTTPException(status_code=404, detail="Host is not registered.")
            host.last_heartbeat = time.time()

        with self.lock:
            queue = self.host_queues.setdefault(host_id, [])
            while queue:
                capture_id = queue.pop(0)
                record = self.records.get(capture_id)
                if record and record.state == "queued":
                    record.state = "capturing"
                    return {
                        "type": "capture-screenshot",
                        "captureId": record.capture_id,
                        "sessionId": record.session_id,
                    }
        return None

    def store(self, host_id: str, capture_id: str, content_type: str, body: bytes) -> CaptureRecord:
        if not body:
            raise HTTPException(status_code=400, detail="Screenshot body is empty.")
        if len(body) > CAPTURE_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Screenshot exceeds the configured size limit.")
        normalized = content_type.split(";", 1)[0].strip().lower()
        if normalized not in {"image/png", "image/jpeg"}:
            raise HTTPException(status_code=415, detail="Screenshot must be PNG or JPEG.")

        with self.lock:
            record = self.records.get(capture_id)
            if not record or record.host_id != host_id:
                raise HTTPException(status_code=404, detail="Capture request is not assigned to this host.")
            extension = ".jpg" if normalized == "image/jpeg" else ".png"
            destination = self.images_dir / f"{capture_id}{extension}"
            destination.write_bytes(body)
            record.image_path = destination
            record.content_type = normalized
            record.state = "ready"
            record.error = None
        self.broker._audit("capture.ready", capture_id=capture_id, session_id=record.session_id, host_id=host_id, bytes=len(body))
        return record

    def fail(self, host_id: str, capture_id: str, error: str) -> CaptureRecord:
        with self.lock:
            record = self.records.get(capture_id)
            if not record or record.host_id != host_id:
                raise HTTPException(status_code=404, detail="Capture request is not assigned to this host.")
            record.state = "failed"
            record.error = (error or "Game host could not capture the game.")[:500]
        self.broker._audit("capture.failed", capture_id=capture_id, session_id=record.session_id, host_id=host_id)
        return record

    @staticmethod
    def public(record: CaptureRecord) -> dict[str, Any]:
        return {
            "ok": record.state != "failed",
            "captureId": record.capture_id,
            "sessionId": record.session_id,
            "state": record.state,
            "ready": record.state == "ready" and bool(record.image_path),
            "contentType": record.content_type if record.state == "ready" else None,
            "error": record.error,
        }


def install_recroom_capture_routes(app: Any, broker: Any, data_dir: Path) -> RecRoomCaptureService:
    service = RecRoomCaptureService(broker, data_dir)

    @app.post("/api/recroom/sessions/{session_id}/captures")
    async def create_capture(
        session_id: str,
        access_token: str = Query(alias="accessToken"),
        x_flux_broker_key: str | None = Header(default=None),
    ) -> JSONResponse:
        broker.authorize_flux(x_flux_broker_key)
        record = service.request_capture(session_id, access_token)
        return JSONResponse(service.public(record), status_code=202)

    @app.get("/api/recroom/sessions/{session_id}/captures/{capture_id}")
    async def capture_status(
        session_id: str,
        capture_id: str,
        access_token: str = Query(alias="accessToken"),
        x_flux_broker_key: str | None = Header(default=None),
    ) -> JSONResponse:
        broker.authorize_flux(x_flux_broker_key)
        record = service.for_access(session_id, capture_id, access_token)
        return JSONResponse(service.public(record))

    @app.get("/api/recroom/sessions/{session_id}/captures/{capture_id}/image")
    async def capture_image(
        session_id: str,
        capture_id: str,
        access_token: str = Query(alias="accessToken"),
        x_flux_broker_key: str | None = Header(default=None),
    ) -> FileResponse:
        broker.authorize_flux(x_flux_broker_key)
        record = service.for_access(session_id, capture_id, access_token)
        if record.state != "ready" or not record.image_path or not record.image_path.exists():
            raise HTTPException(status_code=409, detail="Screenshot is not ready yet.")
        return FileResponse(
            record.image_path,
            media_type=record.content_type,
            filename=f"flux-recroom-{capture_id}{record.image_path.suffix}",
            headers={"Cache-Control": "private, no-store"},
        )

    @app.get("/api/recroom/hosts/{host_id}/capture-jobs")
    async def host_capture_job(
        host_id: str,
        x_recroom_host_key: str | None = Header(default=None),
    ) -> JSONResponse:
        broker.authorize_host(x_recroom_host_key)
        return JSONResponse({"ok": True, "job": service.next_for_host(host_id)})

    @app.put("/api/recroom/hosts/{host_id}/captures/{capture_id}")
    async def host_capture_upload(
        host_id: str,
        capture_id: str,
        request: Request,
        x_recroom_host_key: str | None = Header(default=None),
    ) -> JSONResponse:
        broker.authorize_host(x_recroom_host_key)
        body = await request.body()
        record = service.store(host_id, capture_id, request.headers.get("content-type", ""), body)
        return JSONResponse(service.public(record))

    @app.post("/api/recroom/hosts/{host_id}/captures/{capture_id}/failed")
    async def host_capture_failed(
        host_id: str,
        capture_id: str,
        request: Request,
        x_recroom_host_key: str | None = Header(default=None),
    ) -> JSONResponse:
        broker.authorize_host(x_recroom_host_key)
        payload = await request.json()
        record = service.fail(host_id, capture_id, str(payload.get("error") or "Capture failed"))
        return JSONResponse(service.public(record))

    return service

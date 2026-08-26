from __future__ import annotations

import json
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import Body, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from server_live_broadcaster import ServerLiveBroadcaster

CHUNK_SIZE = 8 * 1024 * 1024
MAX_CHUNK_SIZE = 16 * 1024 * 1024


class PreRecordedLiveEngine(ServerLiveBroadcaster):
    """Prerecorded-video layer on top of the existing TikTok LIVE broadcaster."""

    def __init__(self, ai: Any, connector: Any, data_dir: Path, authorize: Any, display: str) -> None:
        super().__init__(ai, connector, data_dir / "engine", authorize, display)
        self.root = data_dir
        self.uploads_dir = data_dir / "uploads"
        self.library_file = data_dir / "library.json"
        self.jobs_file = data_dir / "jobs.json"
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, dict[str, Any]] = {}
        self.library_rows: dict[str, dict[str, Any]] = {}
        self.current_media_id = ""
        self.media_path = ""
        self.loop_video = True
        self.scheduled_job_id = ""
        self.state_lock = threading.RLock()
        self.scheduler_stop = threading.Event()
        self._load()
        threading.Thread(target=self._scheduler_loop, daemon=True, name="ripo-prerecorded-scheduler").start()

    def _load(self) -> None:
        for path, attr in ((self.library_file, "library_rows"), (self.jobs_file, "jobs")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    setattr(self, attr, value)
            except Exception:
                pass

    def _save(self) -> None:
        self.library_file.write_text(json.dumps(self.library_rows, indent=2), encoding="utf-8")
        self.jobs_file.write_text(json.dumps(self.jobs, indent=2), encoding="utf-8")

    @staticmethod
    def _safe_name(name: str) -> str:
        raw = Path(name or "video.mp4").name
        cleaned = "".join(c for c in raw if c.isalnum() or c in " ._-()[]")
        return (cleaned.strip() or "video.mp4")[:180]

    def _probe(self, path: Path) -> dict[str, Any]:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return {}
        try:
            p = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration,size", "-show_streams", "-of", "json", str(path)],
                capture_output=True, timeout=90, check=True,
            )
            data = json.loads(p.stdout.decode("utf-8", errors="replace"))
            fmt = data.get("format") or {}
            return {
                "duration": float(fmt.get("duration") or 0),
                "size": int(fmt.get("size") or path.stat().st_size),
                "streams": [
                    {"codec_type": s.get("codec_type"), "codec_name": s.get("codec_name"),
                     "width": s.get("width"), "height": s.get("height"), "r_frame_rate": s.get("r_frame_rate")}
                    for s in data.get("streams", [])
                ],
            }
        except Exception as exc:
            return {"probe_error": str(exc)[:300]}

    def library(self) -> list[dict[str, Any]]:
        out = []
        for media_id, row in self.library_rows.items():
            path = Path(str(row.get("path") or ""))
            if not row.get("complete") or not path.exists():
                continue
            out.append({"id": media_id, "name": row.get("name"), "size": int(row.get("size") or path.stat().st_size),
                        "created_at": row.get("created_at"), "duration": row.get("duration"), "probe": row.get("probe") or {}})
        out.sort(key=lambda r: float(r.get("created_at") or 0), reverse=True)
        return out

    def status(self) -> dict[str, Any]:
        base = super().status()
        scheduled = self.jobs.get(self.scheduled_job_id)
        current = self.library_rows.get(self.current_media_id)
        return {**base, "mode": "prerecorded", "ffmpeg_available": bool(shutil.which("ffmpeg")),
                "library_count": len(self.library()),
                "current_media": ({"id": self.current_media_id, "name": current.get("name")} if current else None),
                "scheduled_job": scheduled}

    def init_upload(self, name: str, size: int) -> dict[str, Any]:
        if size < 1:
            raise HTTPException(400, "Video size must be greater than zero.")
        media_id = secrets.token_urlsafe(12)
        safe = self._safe_name(name)
        part = self.uploads_dir / f".{media_id}.part"
        final = self.uploads_dir / f"{media_id}-{safe}"
        part.touch()
        self.library_rows[media_id] = {"id": media_id, "name": safe, "size": int(size), "uploaded": 0,
                                       "part": str(part), "path": str(final), "complete": False, "created_at": time.time()}
        self._save()
        return {"ok": True, "media_id": media_id, "chunk_size": CHUNK_SIZE, "max_chunk_size": MAX_CHUNK_SIZE}

    def upload_chunk(self, media_id: str, offset: int, upload: UploadFile) -> dict[str, Any]:
        row = self.library_rows.get(media_id)
        if not row:
            raise HTTPException(404, "Upload not found.")
        expected = int(row.get("uploaded") or 0)
        if offset != expected:
            raise HTTPException(409, f"Wrong offset; expected {expected}.")
        part = Path(str(row["part"]))
        written = 0
        with part.open("ab") as out:
            while True:
                data = upload.file.read(1024 * 1024)
                if not data:
                    break
                written += len(data)
                if written > MAX_CHUNK_SIZE:
                    raise HTTPException(413, "Chunk exceeds 16 MB.")
                out.write(data)
        row["uploaded"] = expected + written
        if row["uploaded"] > int(row["size"]):
            raise HTTPException(413, "Upload exceeds declared size.")
        if row["uploaded"] == int(row["size"]):
            final = Path(str(row["path"]))
            part.replace(final)
            row["complete"] = True
            row["probe"] = self._probe(final)
            row["duration"] = (row["probe"] or {}).get("duration")
            row.pop("part", None)
        self._save()
        return {"ok": True, "media_id": media_id, "uploaded": row["uploaded"], "size": row["size"], "complete": row["complete"], "probe": row.get("probe") or {}}

    def delete_media(self, media_id: str) -> dict[str, Any]:
        row = self.library_rows.pop(media_id, None)
        if not row:
            raise HTTPException(404, "Video not found.")
        for key in ("path", "part"):
            try:
                Path(str(row.get(key) or "")).unlink(missing_ok=True)
            except Exception:
                pass
        self._save()
        return {"ok": True, "media_id": media_id}

    def start_media(self, media_id: str, loop: bool = True) -> dict[str, Any]:
        row = self.library_rows.get(media_id)
        if not row or not row.get("complete"):
            raise HTTPException(409, "Video upload is not complete.")
        path = Path(str(row.get("path") or ""))
        if not path.exists():
            raise HTTPException(404, "Video file is missing from the server.")
        if not shutil.which("ffmpeg"):
            raise HTTPException(503, "FFmpeg is unavailable on the server.")
        self.current_media_id = media_id
        self.media_path = str(path)
        self.loop_video = bool(loop)
        return super().start()

    def _start_ffmpeg(self, server: str, key: str) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("FFmpeg is missing from the Ripo server.")
        media = Path(self.media_path)
        if not media.exists():
            raise RuntimeError("Selected video is missing.")
        target = server.rstrip("/") + "/" + key.lstrip("/")
        log = (self.data_dir / "prerecorded-ffmpeg.log").open("ab", buffering=0)
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-re"]
        if self.loop_video:
            cmd += ["-stream_loop", "-1"]
        cmd += ["-i", str(media),
                "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1",
                "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                "-pix_fmt", "yuv420p", "-g", "60", "-b:v", "2400k", "-maxrate", "2800k", "-bufsize", "4800k",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", "-f", "flv", target]
        self.ffmpeg = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        time.sleep(2)
        if self.ffmpeg.poll() is not None:
            raise RuntimeError("FFmpeg exited while starting the prerecorded broadcast.")

    def schedule(self, media_id: str, timestamp: float, loop: bool = True) -> dict[str, Any]:
        if media_id not in self.library_rows or not self.library_rows[media_id].get("complete"):
            raise HTTPException(409, "Video upload is not complete.")
        if timestamp <= time.time():
            raise HTTPException(400, "Schedule time must be in the future.")
        job_id = secrets.token_urlsafe(10)
        self.jobs[job_id] = {"id": job_id, "media_id": media_id, "scheduled_for": float(timestamp), "loop": bool(loop), "state": "scheduled", "created_at": time.time()}
        self.scheduled_job_id = job_id
        self._save()
        return {"ok": True, "job": self.jobs[job_id]}

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found.")
        job["state"] = "cancelled"
        if self.scheduled_job_id == job_id:
            self.scheduled_job_id = ""
        self._save()
        return {"ok": True, "job": job}

    def _scheduler_loop(self) -> None:
        while not self.scheduler_stop.wait(1):
            for job in list(self.jobs.values()):
                if job.get("state") != "scheduled" or float(job.get("scheduled_for") or 0) > time.time():
                    continue
                job["state"] = "starting"
                self._save()
                try:
                    self.start_media(str(job["media_id"]), bool(job.get("loop", True)))
                    job["state"] = "live"
                    job["started_at"] = time.time()
                except Exception as exc:
                    job["state"] = "error"
                    job["error"] = str(exc)[:500]
                self._save()

    def stop(self) -> dict[str, Any]:
        result = super().stop()
        self.current_media_id = ""
        self._save()
        return result


def _auth(engine: PreRecordedLiveEngine, token: str | None) -> None:
    engine._control_auth(token)


def install_prerecorded_live_routes(app: Any, engine: PreRecordedLiveEngine) -> None:
    @app.get("/api/tiktok/prerecorded/status")
    async def status() -> JSONResponse:
        return JSONResponse(engine.status())

    @app.get("/api/tiktok/prerecorded/library")
    async def library() -> JSONResponse:
        return JSONResponse({"ok": True, "videos": engine.library()})

    @app.post("/api/tiktok/prerecorded/upload/init")
    async def init_upload(payload: dict[str, Any] = Body(default_factory=dict), x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        _auth(engine, x_admin_token)
        return JSONResponse(engine.init_upload(str(payload.get("name") or "video.mp4"), int(payload.get("size") or 0)))

    @app.post("/api/tiktok/prerecorded/upload/chunk")
    async def upload_chunk(media_id: str = Query(...), offset: int = Query(ge=0), file: UploadFile = File(...), x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        _auth(engine, x_admin_token)
        return JSONResponse(engine.upload_chunk(media_id, offset, file))

    @app.delete("/api/tiktok/prerecorded/library/{media_id}")
    async def delete_media(media_id: str, x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        _auth(engine, x_admin_token)
        return JSONResponse(engine.delete_media(media_id))

    @app.post("/api/tiktok/prerecorded/start")
    async def start(payload: dict[str, Any] = Body(default_factory=dict), x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        _auth(engine, x_admin_token)
        return JSONResponse(engine.start_media(str(payload.get("media_id") or ""), bool(payload.get("loop", True))))

    @app.post("/api/tiktok/prerecorded/stop")
    async def stop(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        _auth(engine, x_admin_token)
        return JSONResponse(engine.stop())

    @app.post("/api/tiktok/prerecorded/schedule")
    async def schedule(payload: dict[str, Any] = Body(default_factory=dict), x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        _auth(engine, x_admin_token)
        return JSONResponse(engine.schedule(str(payload.get("media_id") or ""), float(payload.get("scheduled_for") or 0), bool(payload.get("loop", True))))

    @app.post("/api/tiktok/prerecorded/schedule/{job_id}/cancel")
    async def cancel(job_id: str, x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        _auth(engine, x_admin_token)
        return JSONResponse(engine.cancel_job(job_id))

    @app.get("/api/tiktok/prerecorded/jobs")
    async def jobs(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        _auth(engine, x_admin_token)
        return JSONResponse({"ok": True, "jobs": list(engine.jobs.values())})

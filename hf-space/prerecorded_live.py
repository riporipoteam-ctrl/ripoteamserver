from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import Body, Header, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from server_live_broadcaster import ServerLiveBroadcaster


MAX_CHUNK_BYTES = 16 * 1024 * 1024
DEFAULT_CHUNK_BYTES = 8 * 1024 * 1024


class PreRecordedLiveBroadcaster(ServerLiveBroadcaster):
    """Use the existing TikTok browser/RTMP capture pipeline for prerecorded video."""

    def __init__(self, ai: Any, connector: Any, data_dir: Path, authorize: Any, display: str) -> None:
        super().__init__(ai, connector, data_dir / "engine", authorize, display)
        self.root_dir = data_dir
        self.upload_dir = data_dir / "uploads"
        self.library_file = data_dir / "library.json"
        self.jobs_file = data_dir / "jobs.json"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.media_path = ""
        self.loop_video = True
        self.current_job_id = ""
        self.scheduled_job_id = ""
        self.jobs: dict[str, dict[str, Any]] = {}
        self.uploads: dict[str, dict[str, Any]] = {}
        self.lock2 = threading.RLock()
        self.scheduler_stop = threading.Event()
        self.scheduler = threading.Thread(target=self._scheduler_loop, daemon=True, name="ripo-prerecorded-scheduler")
        self._load_state()
        self.scheduler.start()

    def _load_state(self) -> None:
        for path, target in ((self.library_file, "uploads"), (self.jobs_file, "jobs")):
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(saved, dict):
                    setattr(self, target, saved)
            except Exception:
                pass

    def _save_state(self) -> None:
        self.library_file.write_text(json.dumps(self.uploads, indent=2), encoding="utf-8")
        self.jobs_file.write_text(json.dumps(self.jobs, indent=2), encoding="utf-8")

    def _clean_name(self, name: str) -> str:
        cleaned = "".join(ch for ch in Path(name or "video.mp4").name if ch.isalnum() or ch in " ._-()[]").strip()
        return cleaned[:180] or "video.mp4"

    def library(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for media_id, row in self.uploads.items():
            path = Path(str(row.get("path") or ""))
            if not path.exists():
                continue
            rows.append({
                "id": media_id,
                "name": row.get("name") or path.name,
                "size": int(row.get("size") or path.stat().st_size),
                "created_at": row.get("created_at"),
                "path_ready": True,
                "duration": row.get("duration"),
                "probe": row.get("probe") or {},
            })
        rows.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        return rows

    def status(self) -> dict[str, Any]:
        base = super().status()
        current = self.uploads.get(self.current_job_id)
        scheduled = self.jobs.get(self.scheduled_job_id)
        return {
            **base,
            "mode": "prerecorded",
            "current_media": current and {
                "id": self.current_job_id,
                "name": current.get("name"),
                "size": current.get("size"),
            },
            "scheduled_job": scheduled and {
                "id": self.scheduled_job_id,
                "media_id": scheduled.get("media_id"),
                "media_name": self.uploads.get(str(scheduled.get("media_id")), {}).get("name"),
                "scheduled_for": scheduled.get("scheduled_for"),
                "loop": scheduled.get("loop", True),
                "state": scheduled.get("state"),
            },
            "library_count": len(self.library()),
            "ffmpeg_available": bool(shutil.which("ffmpeg")),
        }

    def _probe(self, path: Path) -> dict[str, Any]:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return {}
        try:
            completed = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration,size", "-show_streams", "-of", "json", str(path)],
                capture_output=True,
                timeout=90,
                check=True,
            )
            data = json.loads(completed.stdout.decode("utf-8", errors="replace"))
            duration = float((data.get("format") or {}).get("duration") or 0)
            return {
                "duration": duration,
                "size": int((data.get("format") or {}).get("size") or path.stat().st_size),
                "streams": [
                    {
                        "codec_type": row.get("codec_type"),
                        "codec_name": row.get("codec_name"),
                        "width": row.get("width"),
                        "height": row.get("height"),
                        "r_frame_rate": row.get("r_frame_rate"),
                    }
                    for row in data.get("streams", [])
                ],
            }
        except Exception as exc:
            return {"probe_error": str(exc)[:300]}

    def init_upload(self, name: str, size: int | None = None) -> dict[str, Any]:
        media_id = secrets.token_urlsafe(12)
        safe_name = self._clean_name(name)
        temp_path = self.upload_dir / f".{media_id}.part"
        self.uploads[media_id] = {
            "id": media_id,
            "name": safe_name,
            "size": int(size or 0),
            "uploaded": 0,
            "path": str(self.upload_dir / f"{media_id}-{safe_name}"),
            "temp_path": str(temp_path),
            "created_at": time.time(),
            "complete": False,
        }
        temp_path.touch(exist_ok=True)
        self._save_state()
        return {"ok": True, "media_id": media_id, "chunk_size": DEFAULT_CHUNK_BYTES, "max_chunk_size": MAX_CHUNK_BYTES}

    def upload_chunk(self, media_id: str, offset: int, upload: UploadFile) -> dict[str, Any]:
        row = self.uploads.get(media_id)
        if not row:
            raise HTTPException(404, "Upload not found.")
        if row.get("complete"):
            return {"ok": True, "media_id": media_id, "uploaded": row.get("size", 0), "complete": True}
        offset = int(offset)
        expected = int(row.get("uploaded") or 0)
        if offset != expected:
            raise HTTPException(409, f"Wrong upload offset. Expected {expected}.")

        temp_path = Path(str(row["temp_path"]))
        written = 0
        with temp_path.open("ab") as output:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_CHUNK_BYTES:
                    raise HTTPException(413, f"Chunk is larger than {MAX_CHUNK_BYTES} bytes.")
                output.write(chunk)
        row["uploaded"] = expected + written
        declared = int(row.get("size") or 0)
        if declared and row["uploaded"] > declared:
            raise HTTPException(413, "Uploaded data exceeds the declared file size.")
        if declared and row["uploaded"] == declared:
            final_path = Path(str(row["path"]))
            temp_path.replace(final_path)
            row["complete"] = True
            row["size"] = final_path.stat().st_size
            row["probe"] = self._probe(final_path)
            row["duration"] = row["probe"].get("duration")
        self._save_state()
        return {
            "ok": True,
            "media_id": media_id,
            "uploaded": row["uploaded"],
            "size": row.get("size"),
            "complete": bool(row.get("complete")),
            "probe": row.get("probe") or {},
        }

    def delete_media(self, media_id: str) -> dict[str, Any]:
        row = self.uploads.pop(media_id, None)
        if not row:
            raise HTTPException(404, "Video not found.")
        for key in ("path", "temp_path"):
            try:
                Path(str(row.get(key) or "")).unlink(missing_ok=True)
            except Exception:
                pass
        self._save_state()
        return {"ok": True, "media_id": media_id}

    def start_prerecorded(self, media_id: str, loop: bool = True) -> dict[str, Any]:
        row = self.uploads.get(media_id)
        if not row or not row.get("complete"):
            raise HTTPException(409, "The selected video is not fully uploaded yet.")
        path = Path(str(row.get("path") or ""))
        if not path.exists():
            raise HTTPException(404, "Video file is missing on the server.")
        if not self.status().get("ffmpeg_available"):
            raise HTTPException(503, "FFmpeg is not installed on the Ripo server.")
        self.media_path = str(path)
        self.loop_video = bool(loop)
        self.current_job_id = media_id
        return super().start()

    def _start_ffmpeg(self, server: str, key: str) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("FFmpeg is missing from the Ripo server.")
        media = Path(self.media_path)
        if not media.exists():
            raise RuntimeError("Selected prerecorded video is missing.")
        target = server.rstrip("/") + "/" + key.lstrip("/")
        log = (self.data_dir / "prerecorded-ffmpeg.log").open("ab", buffering=0)
        command = [
            ffmpeg,
            "-hide_banner", "-loglevel", "warning",
            "-re",
        ]
        if self.loop_video:
            command += ["-stream_loop", "-1"]
        command += [
            "-i", str(media),
            "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1",
            "-r", "30",
            "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
            "-pix_fmt", "yuv420p", "-g", "60",
            "-b:v", "2400k", "-maxrate", "2800k", "-bufsize", "4800k",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            "-f", "flv", target,
        ]
        self.ffmpeg = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        time.sleep(2)
        if self.ffmpeg.poll() is not None:
            raise RuntimeError("FFmpeg exited while starting the prerecorded TikTok broadcast. Check LIVE access and the FFmpeg log.")

    def schedule(self, media_id: str, scheduled_for: float, loop: bool = True) -> dict[str, Any]:
        row = self.uploads.get(media_id)
        if not row or not row.get("complete"):
            raise HTTPException(409, "The selected video is not fully uploaded yet.")
        when = float(scheduled_for)
        if when <= time.time():
            raise HTTPException(400, "Schedule time must be in the future.")
        with self.lock2:
            job_id = secrets.token_urlsafe(10)
            self.jobs[job_id] = {
                "id": job_id,
                "media_id": media_id,
                "scheduled_for": when,
                "loop": bool(loop),
                "state": "scheduled",
                "created_at": time.time(),
            }
            self.scheduled_job_id = job_id
            self._save_state()
        return {"ok": True, "job": self.jobs[job_id]}

    def cancel_schedule(self, job_id: str) -> dict[str, Any]:
        with self.lock2:
            job = self.jobs.get(job_id)
            if not job:
                raise HTTPException(404, "Scheduled job not found.")
            job["state"] = "cancelled"
            if self.scheduled_job_id == job_id:
                self.scheduled_job_id = ""
            self._save_state()
        return {"ok": True, "job": job}

    def _scheduler_loop(self) -> None:
        while not self.scheduler_stop.wait(1.0):
            try:
                with self.lock2:
                    candidates = [
                        row for row in self.jobs.values()
                        if row.get("state") == "scheduled" and float(row.get("scheduled_for") or 0) <= time.time()
                    ]
                for job in candidates:
                    self._run_scheduled(job)
            except Exception:
                pass

    def _run_scheduled(self, job: dict[str, Any]) -> None:
        with self.lock2:
            if job.get("state") != "scheduled":
                return
            job["state"] = "starting"
            self._save_state()
        try:
            self.start_prerecorded(str(job.get("media_id") or ""), bool(job.get("loop", True)))
            with self.lock2:
                job["state"] = "live"
                job["started_at"] = time.time()
                self._save_state()
        except Exception as exc:
            with self.lock2:
                job["state"] = "error"
                job["error"] = str(exc)[:500]
                self._save_state()

    def stop(self) -> dict[str, Any]:
        result = super().stop()
        with self.lock2:
            if self.current_job_id:
                self.current_job_id = ""
            self._save_state()
        return result


def install_prerecorded_live_routes(app: Any, broadcaster: PreRecordedLiveBroadcaster) -> None:
    @app.get("/api/tiktok/prerecorded/status")
    async def prerecorded_status() -> JSONResponse:
        return JSONResponse(broadcaster.status())

    @app.get("/api/tiktok/prerecorded/library")
    async def prerecorded_library() -> JSONResponse:
        return JSONResponse({"ok": True, "videos": broadcaster.library()})

    @app.post("/api/tiktok/prerecorded/upload/init")
    async def prerecorded_upload_init(
        payload: dict[str, Any] = Body(default_factory=dict),
        x_admin_token: str | None = Header(default=None),
    ) -> JSONResponse:
        broadcaster._control_auth(x_admin_token)
        return JSONResponse(broadcaster.init_upload(str(payload.get("name") or "video.mp4"), payload.get("size")))

    @app.post("/api/tiktok/prerecorded/upload/chunk")
    async def prerecorded_upload_chunk(
        media_id: str = Query(min_length=6, max_length=100),
        offset: int = Query(ge=0),
        file: UploadFile = UploadFile(...),
        x_admin_token: str | None = Header(default=None),
    ) -> JSONResponse:
        broadcaster._control_auth(x_admin_token)
        return JSONResponse(broadcaster.upload_chunk(media_id, offset, file))

    @app.delete("/api/tiktok/prerecorded/library/{media_id}")
    async def prerecorded_delete(media_id: str, x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        broadcaster._control_auth(x_admin_token)
        return JSONResponse(broadcaster.delete_media(media_id))

    @app.post("/api/tiktok/prerecorded/start")
    async def prerecorded_start(
        payload: dict[str, Any] = Body(default_factory=dict),
        x_admin_token: str | None = Header(default=None),
    ) -> JSONResponse:
        broadcaster._control_auth(x_admin_token)
        return JSONResponse(broadcaster.start_prerecorded(str(payload.get("media_id") or ""), bool(payload.get("loop", True))))

    @app.post("/api/tiktok/prerecorded/stop")
    async def prerecorded_stop(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        broadcaster._control_auth(x_admin_token)
        return JSONResponse(broadcaster.stop())

    @app.post("/api/tiktok/prerecorded/schedule")
    async def prerecorded_schedule(
        payload: dict[str, Any] = Body(default_factory=dict),
        x_admin_token: str | None = Header(default=None),
    ) -> JSONResponse:
        broadcaster._control_auth(x_admin_token)
        return JSONResponse(
            broadcaster.schedule(
                str(payload.get("media_id") or ""),
                float(payload.get("scheduled_for") or 0),
                bool(payload.get("loop", True)),
            )
        )

    @app.post("/api/tiktok/prerecorded/schedule/{job_id}/cancel")
    async def prerecorded_cancel(job_id: str, x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        broadcaster._control_auth(x_admin_token)
        return JSONResponse(broadcaster.cancel_schedule(job_id))

    @app.get("/api/tiktok/prerecorded/jobs")
    async def prerecorded_jobs(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        broadcaster._control_auth(x_admin_token)
        rows = sorted(broadcaster.jobs.values(), key=lambda row: float(row.get("scheduled_for") or 0))
        return JSONResponse({"ok": True, "jobs": rows})

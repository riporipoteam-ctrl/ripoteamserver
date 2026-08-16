from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import threading
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import Header, HTTPException
from fastapi.responses import JSONResponse


TARGET_APP = "471710"
TARGET_DEPOT = "471711"
TARGET_MANIFEST = "6337851004861751095"
DEPOTDOWNLOADER_VERSION = "DepotDownloader_3.4.0"
DEPOTDOWNLOADER_URL = (
    "https://github.com/SteamRE/DepotDownloader/releases/download/"
    f"{DEPOTDOWNLOADER_VERSION}/DepotDownloader-linux-x64.zip"
)


def _safe_extract(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            target = (destination / name).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise RuntimeError(f"Unsafe path in DepotDownloader archive: {name}") from exc
            if info.file_size > 256 * 1024 * 1024:
                raise RuntimeError(f"Unexpected oversized DepotDownloader archive member: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)


def _verified_client_layout(root: Path) -> bool:
    exe = next((root / name for name in ("RecRoom.exe", "Recroom_Release.exe") if (root / name).is_file()), None)
    assembly = root / "GameAssembly.dll"
    data = next((root / name for name in ("RecRoom_Data", "Recroom_Release_Data") if (root / name).is_dir()), None)
    metadata = data / "il2cpp_data" / "Metadata" / "global-metadata.dat" if data else None
    manifest = root / ".DepotDownloader" / f"{TARGET_DEPOT}_{TARGET_MANIFEST}.manifest"
    return bool(exe and assembly.is_file() and metadata and metadata.is_file() and manifest.is_file())


class RecRoomSteamRecovery:
    """Admin-only official Steam recovery for the server's base Rec Room image.

    DepotDownloader is downloaded only from SteamRE's pinned GitHub release. It
    authenticates directly with Steam using the tool's QR flow; Flux never asks
    for or stores a Steam password. The recovered depot stays server-side and is
    never exposed to browser clients.
    """

    def __init__(self, pool: Any, data_dir: Path) -> None:
        self.pool = pool
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tool_dir = self.data_dir / "depotdownloader"
        self.staging = self.data_dir / "client-staging"
        self.home_dir = self.data_dir / "steam-home"
        self.home_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.job: dict[str, Any] | None = None
        self.process: subprocess.Popen[str] | None = None

    def _append(self, line: str) -> None:
        clean = line.rstrip("\r\n")
        if not clean:
            clean = " "
        with self.lock:
            if not self.job:
                return
            logs = self.job.setdefault("logs", [])
            logs.append(clean[:1000])
            if len(logs) > 220:
                del logs[:-220]
            self.job["updatedAtMs"] = int(time.time() * 1000)
            lowered = clean.lower()
            if "use the steam mobile app" in lowered or "qr code" in lowered:
                self.job.update(state="waiting-for-steam", phase="Scan the Steam QR code", progress=12, qrReady=True)
            elif "success! next time you can login" in lowered or "got " in lowered and "licenses for account" in lowered:
                self.job.update(state="authenticated", phase="Steam account approved", progress=max(22, int(self.job.get("progress") or 0)))
            elif "got depot key" in lowered:
                self.job.update(state="downloading", phase="Steam granted depot access", progress=max(30, int(self.job.get("progress") or 0)))
            elif "downloading depot" in lowered or "processing depot" in lowered:
                self.job.update(state="downloading", phase="Downloading May 19 2022 client from Steam", progress=max(38, int(self.job.get("progress") or 0)))
            elif "total downloaded:" in lowered:
                self.job.update(state="validating", phase="Validating recovered client", progress=88)

    def public_status(self) -> dict[str, Any]:
        with self.lock:
            if not self.job:
                return {
                    "ok": True,
                    "state": "idle",
                    "phase": "Steam recovery has not been started",
                    "progress": 0,
                    "targetApp": TARGET_APP,
                    "targetDepot": TARGET_DEPOT,
                    "targetManifest": TARGET_MANIFEST,
                    "clientReady": bool(self.pool.capability().get("readyForGame")),
                    "logs": [],
                }
            return {
                key: value
                for key, value in self.job.items()
                if key not in {"pid"}
            }

    def _ensure_tool(self) -> Path:
        binary = self.tool_dir / "DepotDownloader"
        if binary.is_file():
            binary.chmod(binary.stat().st_mode | 0o111)
            return binary

        archive = self.data_dir / "DepotDownloader-linux-x64.zip"
        temp = archive.with_suffix(".download")
        shutil.rmtree(self.tool_dir, ignore_errors=True)
        self.tool_dir.mkdir(parents=True, exist_ok=True)
        try:
            request = urllib.request.Request(
                DEPOTDOWNLOADER_URL,
                headers={"User-Agent": "RipoTeamServer-Steam-Recovery/1.0"},
            )
            with urllib.request.urlopen(request, timeout=90) as response, temp.open("wb") as handle:
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > 96 * 1024 * 1024:
                        raise RuntimeError("Official DepotDownloader archive exceeded expected size.")
                    handle.write(chunk)
            temp.replace(archive)
            if not zipfile.is_zipfile(archive):
                raise RuntimeError("Official DepotDownloader release was not a valid ZIP file.")
            _safe_extract(archive, self.tool_dir)
        finally:
            temp.unlink(missing_ok=True)
            archive.unlink(missing_ok=True)

        binary = self.tool_dir / "DepotDownloader"
        if not binary.is_file():
            candidates = list(self.tool_dir.rglob("DepotDownloader"))
            if candidates:
                binary = candidates[0]
        if not binary.is_file():
            raise RuntimeError("Official DepotDownloader Linux binary was not present after extraction.")
        binary.chmod(binary.stat().st_mode | 0o111)
        return binary

    def start(self) -> dict[str, Any]:
        capability = self.pool.capability()
        if capability.get("readyForGame"):
            return {
                "ok": True,
                "state": "ready",
                "phase": "May 19 2022 server client is already installed",
                "progress": 100,
                "clientReady": True,
                "targetManifest": TARGET_MANIFEST,
                "logs": [],
            }
        with self.lock:
            if self.process and self.process.poll() is None:
                raise HTTPException(status_code=409, detail="Steam recovery is already running.")
            job_id = str(uuid.uuid4())
            now = int(time.time() * 1000)
            self.job = {
                "ok": True,
                "jobId": job_id,
                "state": "starting",
                "phase": "Starting official Steam recovery",
                "progress": 2,
                "qrReady": False,
                "clientReady": False,
                "targetApp": TARGET_APP,
                "targetDepot": TARGET_DEPOT,
                "targetManifest": TARGET_MANIFEST,
                "createdAtMs": now,
                "updatedAtMs": now,
                "error": None,
                "logs": [],
            }
        threading.Thread(target=self._worker, args=(job_id,), name=f"recroom-steam-{job_id[:8]}", daemon=True).start()
        return self.public_status()

    def cancel(self) -> dict[str, Any]:
        with self.lock:
            process = self.process
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except Exception:
                    process.terminate()
                if self.job:
                    self.job.update(ok=False, state="cancelled", phase="Steam recovery cancelled", error="Cancelled by Flux admin")
            self.process = None
        return self.public_status()

    def _install_staging(self) -> None:
        if not _verified_client_layout(self.staging):
            raise RuntimeError(
                "Steam download finished but the exact May 19 2022 client layout/manifest was not complete."
            )
        previous = self.pool.client_dir.with_name(self.pool.client_dir.name + ".steam-previous")
        installing = self.pool.client_dir.with_name(self.pool.client_dir.name + ".steam-installing")
        shutil.rmtree(previous, ignore_errors=True)
        shutil.rmtree(installing, ignore_errors=True)
        self.staging.replace(installing)
        swapped = False
        try:
            if self.pool.client_dir.exists():
                self.pool.client_dir.replace(previous)
            installing.replace(self.pool.client_dir)
            swapped = True
            capability = self.pool.capability()
            if not capability.get("readyForGame"):
                raise RuntimeError(str(capability.get("reason") or "Recovered client did not make the server game-ready."))
            shutil.rmtree(previous, ignore_errors=True)
        except Exception:
            if swapped:
                shutil.rmtree(self.pool.client_dir, ignore_errors=True)
            if previous.exists():
                previous.replace(self.pool.client_dir)
            raise
        finally:
            shutil.rmtree(installing, ignore_errors=True)

    def _worker(self, job_id: str) -> None:
        try:
            with self.lock:
                if not self.job or self.job.get("jobId") != job_id:
                    return
                self.job.update(state="preparing-tool", phase="Preparing official DepotDownloader", progress=4)
            binary = self._ensure_tool()
            shutil.rmtree(self.staging, ignore_errors=True)
            self.staging.mkdir(parents=True, exist_ok=True)

            env = os.environ.copy()
            env["HOME"] = str(self.home_dir)
            env.setdefault("DOTNET_CLI_TELEMETRY_OPTOUT", "1")
            command = [
                str(binary),
                "-app", TARGET_APP,
                "-depot", TARGET_DEPOT,
                "-manifest", TARGET_MANIFEST,
                "-os", "windows",
                "-osarch", "64",
                "-dir", str(self.staging),
                "-qr",
                "-remember-password",
                "-max-downloads", "8",
            ]
            self._append("Starting Steam's QR login for the exact Rec Room May 19 2022 depot...")
            process = subprocess.Popen(
                command,
                cwd=self.tool_dir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=True,
            )
            with self.lock:
                self.process = process
            assert process.stdout is not None
            for line in process.stdout:
                self._append(line)
            code = process.wait()
            with self.lock:
                self.process = None
            if code != 0:
                raise RuntimeError(f"DepotDownloader exited with code {code}. Steam may not grant this account access to the old manifest.")

            with self.lock:
                if self.job:
                    self.job.update(state="validating", phase="Validating Steam client files", progress=90)
            self._install_staging()
            capability = self.pool.capability()
            with self.lock:
                if self.job:
                    self.job.update(
                        ok=True,
                        state="ready",
                        phase="Rec Room May 19 2022 is installed on RipoTeamServer",
                        progress=100,
                        clientReady=True,
                        qrReady=False,
                        capability=capability,
                        error=None,
                    )
        except Exception as exc:
            with self.lock:
                self.process = None
                if self.job:
                    self.job.update(
                        ok=False,
                        state="failed",
                        phase="Steam recovery could not complete",
                        error=str(exc)[:1000],
                        clientReady=bool(self.pool.capability().get("readyForGame")),
                    )
            shutil.rmtree(self.staging, ignore_errors=True)


def install_recroom_steam_recovery_routes(app: Any, broker: Any, pool: Any, data_dir: Path) -> RecRoomSteamRecovery:
    recovery = RecRoomSteamRecovery(pool, data_dir)

    async def require_admin(authorization: str | None) -> None:
        identity = await asyncio.to_thread(broker.verify_flux_user, authorization)
        account = identity.get("account") if isinstance(identity, dict) else None
        if not isinstance(account, dict) or not bool(account.get("isAdmin")):
            raise HTTPException(status_code=403, detail="Flux admin account required.")

    def response(payload: dict[str, Any], status_code: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status_code, headers={"Cache-Control": "no-store, private"})

    @app.get("/api/recroom-public/steam-recovery")
    async def steam_recovery_status(authorization: str | None = Header(default=None)) -> JSONResponse:
        await require_admin(authorization)
        return response(recovery.public_status())

    @app.post("/api/recroom-public/steam-recovery/start")
    async def steam_recovery_start(authorization: str | None = Header(default=None)) -> JSONResponse:
        await require_admin(authorization)
        return response(recovery.start())

    @app.post("/api/recroom-public/steam-recovery/cancel")
    async def steam_recovery_cancel(authorization: str | None = Header(default=None)) -> JSONResponse:
        await require_admin(authorization)
        return response(recovery.cancel())

    return recovery

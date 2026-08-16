from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import shutil
import socket
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import Body, Header, HTTPException


TARGET_MANIFEST = "6337851004861751095"
TARGET_DEPOT = "471711"
MAX_ARCHIVE_BYTES = int(os.environ.get("RECROOM_CLIENT_ARCHIVE_MAX_BYTES", str(12 * 1024**3)))


def _is_public_host(host: str) -> bool:
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    if not addresses:
        return False
    for item in addresses:
        raw = item[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def _validate_source_url(value: str) -> str:
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise ValueError("Client archive URL must be HTTPS.")
    if parsed.username or parsed.password:
        raise ValueError("Credentials must not be embedded in the archive URL.")
    if not _is_public_host(parsed.hostname):
        raise ValueError("Client archive URL must resolve to a public Internet host.")
    return url


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
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
                raise RuntimeError(f"Unsafe path in client archive: {name}") from exc
            if info.file_size < 0 or info.file_size > MAX_ARCHIVE_BYTES:
                raise RuntimeError(f"Archive member is too large: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)


def _find_client_root(extracted: Path) -> Path:
    candidates: list[Path] = []
    for exe_name in ("RecRoom.exe", "Recroom_Release.exe"):
        candidates.extend(path.parent for path in extracted.rglob(exe_name) if path.is_file())
    for root in candidates:
        assembly = root / "GameAssembly.dll"
        data = next((root / name for name in ("RecRoom_Data", "Recroom_Release_Data") if (root / name).is_dir()), None)
        metadata = data / "il2cpp_data" / "Metadata" / "global-metadata.dat" if data else None
        manifest = root / ".DepotDownloader" / f"{TARGET_DEPOT}_{TARGET_MANIFEST}.manifest"
        if assembly.is_file() and metadata and metadata.is_file() and manifest.is_file():
            return root
    raise RuntimeError(
        "Archive does not contain a verified May 19 2022 Rec Room layout with RecRoom.exe, "
        "GameAssembly.dll, IL2CPP metadata, and the exact DepotDownloader manifest."
    )


class RecRoomClientInstaller:
    def __init__(self, pool: Any, data_dir: Path) -> None:
        self.pool = pool
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.active_job_id: str | None = None

    def _set(self, job_id: str, **fields: Any) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            job.update(fields)
            job["updatedAtMs"] = int(time.time() * 1000)

    def public_job(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise HTTPException(status_code=404, detail="Client install job not found.")
            return {key: value for key, value in job.items() if key not in {"sourceUrl"}}

    def start(self, source_url: str, expected_sha256: str = "") -> dict[str, Any]:
        source_url = _validate_source_url(source_url)
        expected_sha256 = expected_sha256.strip().lower()
        if expected_sha256 and (len(expected_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in expected_sha256)):
            raise HTTPException(status_code=400, detail="sha256 must be a 64-character hexadecimal digest.")
        with self.lock:
            if self.active_job_id:
                current = self.jobs.get(self.active_job_id)
                if current and current.get("state") in {"queued", "downloading", "extracting", "validating", "installing"}:
                    raise HTTPException(status_code=409, detail="A Rec Room client install is already running.")
            job_id = str(uuid.uuid4())
            now = int(time.time() * 1000)
            self.jobs[job_id] = {
                "ok": True,
                "jobId": job_id,
                "state": "queued",
                "progress": 1,
                "createdAtMs": now,
                "updatedAtMs": now,
                "sourceUrl": source_url,
                "expectedSha256": bool(expected_sha256),
                "error": None,
            }
            self.active_job_id = job_id
        threading.Thread(
            target=self._worker,
            args=(job_id, source_url, expected_sha256),
            name=f"recroom-client-install-{job_id[:8]}",
            daemon=True,
        ).start()
        return self.public_job(job_id)

    def _download(self, job_id: str, source_url: str, destination: Path, expected_sha256: str) -> str:
        current = source_url
        digest = hashlib.sha256()
        total = 0
        with httpx.Client(timeout=httpx.Timeout(connect=20.0, read=120.0, write=30.0, pool=20.0), follow_redirects=False) as client:
            for _ in range(6):
                current = _validate_source_url(current)
                with client.stream("GET", current, headers={"user-agent": "RipoTeam-RecRoom-ServerInstaller/1.0"}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise RuntimeError("Archive download redirect did not include a Location header.")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    length = int(response.headers.get("content-length", "0") or "0")
                    if length and length > MAX_ARCHIVE_BYTES:
                        raise RuntimeError("Client archive is larger than the configured server limit.")
                    self._set(job_id, state="downloading", progress=8)
                    with destination.open("wb") as handle:
                        for chunk in response.iter_bytes(4 * 1024 * 1024):
                            if not chunk:
                                continue
                            total += len(chunk)
                            if total > MAX_ARCHIVE_BYTES:
                                raise RuntimeError("Client archive exceeded the configured server limit while downloading.")
                            digest.update(chunk)
                            handle.write(chunk)
                            if length:
                                pct = min(58, 8 + int((total / length) * 50))
                                self._set(job_id, progress=pct, downloadedBytes=total, totalBytes=length)
                    actual = digest.hexdigest()
                    if expected_sha256 and actual != expected_sha256:
                        raise RuntimeError("Client archive SHA-256 does not match the expected digest.")
                    return actual
            raise RuntimeError("Client archive exceeded the maximum redirect count.")

    def _worker(self, job_id: str, source_url: str, expected_sha256: str) -> None:
        job_dir = self.data_dir / job_id
        archive = job_dir / "client.zip"
        extracted = job_dir / "extracted"
        install_tmp = self.pool.client_dir.with_name(self.pool.client_dir.name + ".installing")
        try:
            shutil.rmtree(job_dir, ignore_errors=True)
            job_dir.mkdir(parents=True, exist_ok=True)
            actual_sha256 = self._download(job_id, source_url, archive, expected_sha256)
            self._set(job_id, state="extracting", progress=62, actualSha256=actual_sha256)
            if not zipfile.is_zipfile(archive):
                raise RuntimeError("Client source is not a valid ZIP archive.")
            _safe_extract_zip(archive, extracted)
            self._set(job_id, state="validating", progress=80)
            root = _find_client_root(extracted)
            shutil.rmtree(install_tmp, ignore_errors=True)
            self._set(job_id, state="installing", progress=88)
            shutil.copytree(root, install_tmp, symlinks=False)
            check_manifest = install_tmp / ".DepotDownloader" / f"{TARGET_DEPOT}_{TARGET_MANIFEST}.manifest"
            if not check_manifest.is_file():
                raise RuntimeError("Exact May 19 2022 DepotDownloader manifest disappeared during install.")
            old = self.pool.client_dir.with_name(self.pool.client_dir.name + ".previous")
            shutil.rmtree(old, ignore_errors=True)
            if self.pool.client_dir.exists():
                self.pool.client_dir.replace(old)
            install_tmp.replace(self.pool.client_dir)
            shutil.rmtree(old, ignore_errors=True)
            capability = self.pool.capability()
            if not capability.get("readyForGame"):
                raise RuntimeError(str(capability.get("reason") or "Installed client did not make Wine runtime game-ready."))
            self._set(job_id, state="ready", progress=100, installed=True, capability=capability)
        except Exception as exc:
            self._set(job_id, ok=False, state="failed", error=str(exc)[:1000])
            shutil.rmtree(install_tmp, ignore_errors=True)
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)
            with self.lock:
                if self.active_job_id == job_id:
                    self.active_job_id = None

    def maybe_auto_install(self) -> None:
        if self.pool.capability().get("readyForGame"):
            return
        url = os.environ.get("RECROOM_WINE_CLIENT_ARCHIVE_URL", "").strip()
        if not url:
            return
        sha256 = os.environ.get("RECROOM_WINE_CLIENT_ARCHIVE_SHA256", "").strip()
        try:
            self.start(url, sha256)
        except Exception as exc:
            print(f"Rec Room client auto-install did not start: {exc}")


def install_recroom_client_installer_routes(app: Any, broker: Any, pool: Any, data_dir: Path) -> RecRoomClientInstaller:
    installer = RecRoomClientInstaller(pool, data_dir)

    async def require_admin(authorization: str | None) -> dict[str, Any]:
        identity = await asyncio.to_thread(broker.verify_flux_user, authorization)
        account = identity.get("account") if isinstance(identity, dict) else None
        if not isinstance(account, dict) or not bool(account.get("isAdmin")):
            raise HTTPException(status_code=403, detail="Flux admin account required.")
        return identity

    @app.get("/api/recroom-public/client-install/{job_id}")
    async def client_install_status(job_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        await require_admin(authorization)
        return installer.public_job(job_id)

    @app.post("/api/recroom-public/client-install")
    async def client_install(
        payload: dict[str, Any] = Body(default_factory=dict),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        await require_admin(authorization)
        source_url = str(payload.get("url") or "").strip()
        if not source_url:
            raise HTTPException(status_code=400, detail="HTTPS client archive url is required.")
        try:
            return installer.start(source_url, str(payload.get("sha256") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    threading.Thread(target=installer.maybe_auto_install, name="recroom-client-auto-install", daemon=True).start()
    return installer

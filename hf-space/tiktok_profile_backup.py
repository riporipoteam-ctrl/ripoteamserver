from __future__ import annotations

import base64
import hashlib
import io
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from server_tiktok_connect import ServerTikTokConnect

_PREFIX = "rpb1."
_AAD = b"ripo-tiktok-firefox-profile-v1"
_MAX_BLOB_CHARS = 5_500_000
_OLD_POLL = ServerTikTokConnect.poll
_OLD_STATUS = ServerTikTokConnect.status


def _key(connector: ServerTikTokConnect) -> bytes:
    material = (
        os.environ.get("ADMIN_TOKEN", "")
        + "\0"
        + str(getattr(connector.ai, "client_secret", ""))
        + "\0"
        + os.environ.get("VNC_PASSWORD", "")
    ).encode("utf-8")
    if len(material.replace(b"\0", b"")) < 24:
        raise RuntimeError("Server secrets are not configured strongly enough for encrypted TikTok session backup.")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=hashlib.sha256(b"ripo-tiktok-profile-backup-salt-v1").digest(),
        info=_AAD,
    ).derive(material)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _safe_member(name: str) -> bool:
    p = Path(name)
    if p.is_absolute() or ".." in p.parts:
        return False
    if name in {"cookies.sqlite", "storage.sqlite", "webappsstore.sqlite"}:
        return True
    return len(p.parts) >= 3 and p.parts[0] == "storage" and p.parts[1] == "default" and ("tiktok" in p.parts[2].lower() or "musical" in p.parts[2].lower())


def _sqlite_snapshot(source: Path, destination: Path) -> bool:
    if not source.exists():
        return False
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=4)
        dst = sqlite3.connect(str(destination), timeout=4)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return destination.exists() and destination.stat().st_size > 0
    except Exception:
        try:
            shutil.copy2(source, destination)
            return destination.exists() and destination.stat().st_size > 0
        except Exception:
            return False


def _make_archive(connector: ServerTikTokConnect, include_storage: bool = True) -> bytes:
    profile = connector.profile_dir
    if not profile.exists():
        raise RuntimeError("TikTok Firefox profile does not exist yet.")

    with tempfile.TemporaryDirectory(prefix="ripo-tiktok-profile-") as tmp_name:
        tmp = Path(tmp_name)
        for filename in ("cookies.sqlite", "storage.sqlite", "webappsstore.sqlite"):
            _sqlite_snapshot(profile / filename, tmp / filename)

        if include_storage:
            storage_root = profile / "storage" / "default"
            if storage_root.exists():
                for child in storage_root.iterdir():
                    low = child.name.lower()
                    if not child.is_dir() or ("tiktok" not in low and "musical" not in low):
                        continue
                    target = tmp / "storage" / "default" / child.name
                    try:
                        shutil.copytree(child, target, dirs_exist_ok=True)
                    except Exception:
                        pass

        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode="w:gz", compresslevel=7) as tar:
            for path in tmp.rglob("*"):
                if path.is_file():
                    arc = path.relative_to(tmp).as_posix()
                    if _safe_member(arc):
                        tar.add(path, arcname=arc, recursive=False)
        return out.getvalue()


def export_profile(connector: ServerTikTokConnect) -> tuple[str, int]:
    archive = _make_archive(connector, include_storage=True)
    # Keep the encrypted value comfortably below common mobile localStorage limits.
    if len(archive) > 2_600_000:
        archive = _make_archive(connector, include_storage=False)
    if len(archive) > 3_400_000:
        raise RuntimeError("TikTok session backup is unexpectedly large; refusing to export browser data.")

    nonce = os.urandom(12)
    ciphertext = ChaCha20Poly1305(_key(connector)).encrypt(nonce, archive, _AAD)
    blob = _PREFIX + _b64(nonce + ciphertext)
    if len(blob) > _MAX_BLOB_CHARS:
        raise RuntimeError("Encrypted TikTok session backup is too large for browser persistence.")
    return blob, len(archive)


def _stop_firefox(connector: ServerTikTokConnect) -> None:
    proc = getattr(connector, "browser", None)
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=3)
            except Exception:
                pass
    connector.browser = None


def restore_profile(connector: ServerTikTokConnect, blob: str) -> dict[str, Any]:
    value = str(blob or "").strip()
    if not value.startswith(_PREFIX) or len(value) > _MAX_BLOB_CHARS:
        raise HTTPException(400, "Invalid encrypted TikTok session backup.")
    try:
        raw = _unb64(value[len(_PREFIX):])
        if len(raw) < 29:
            raise ValueError("short backup")
        nonce, ciphertext = raw[:12], raw[12:]
        archive = ChaCha20Poly1305(_key(connector)).decrypt(nonce, ciphertext, _AAD)
    except Exception as exc:
        raise HTTPException(400, "TikTok session backup cannot be decrypted on this server. Connect once to create a fresh backup.") from exc

    _stop_firefox(connector)
    profile = connector.profile_dir
    profile.mkdir(parents=True, exist_ok=True)

    # Remove only the session stores we are about to restore; never wipe the whole profile.
    for filename in ("cookies.sqlite", "cookies.sqlite-wal", "cookies.sqlite-shm", "storage.sqlite", "storage.sqlite-wal", "storage.sqlite-shm", "webappsstore.sqlite"):
        try:
            (profile / filename).unlink(missing_ok=True)
        except Exception:
            pass
    storage_default = profile / "storage" / "default"
    if storage_default.exists():
        for child in list(storage_default.iterdir()):
            low = child.name.lower()
            if "tiktok" in low or "musical" in low:
                try:
                    shutil.rmtree(child)
                except Exception:
                    pass

    restored = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            for member in tar.getmembers():
                if not member.isfile() or not _safe_member(member.name):
                    continue
                src = tar.extractfile(member)
                if src is None:
                    continue
                dest = profile / member.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                data = src.read()
                if len(data) > 20_000_000:
                    continue
                dest.write_bytes(data)
                restored += 1
    except Exception as exc:
        raise HTTPException(400, "Encrypted TikTok session backup is damaged.") from exc

    connector._write_profile_prefs()
    return {
        "ok": True,
        "restored": restored > 0,
        "restored_files": restored,
        "message": "Encrypted TikTok server login restored." if restored else "Backup contained no usable TikTok session files.",
    }


def _poll(self: ServerTikTokConnect, flow_id: str) -> dict[str, Any]:
    result = _OLD_POLL(self, flow_id)
    if not result.get("connected"):
        return result

    # Close Firefox gracefully so cookies.sqlite is flushed before we snapshot it.
    _stop_firefox(self)
    time.sleep(0.25)
    try:
        blob, plain_bytes = export_profile(self)
        result["profile_blob"] = blob
        result["profile_backup_bytes"] = plain_bytes
        result["profile_persistence"] = "encrypted-dashboard-backup"
    except Exception as exc:
        result["profile_blob"] = ""
        result["profile_backup_error"] = str(exc)[:500]
    return result


def _status(self: ServerTikTokConnect) -> dict[str, Any]:
    data = _OLD_STATUS(self)
    data["encrypted_profile_backup_supported"] = True
    if data.get("login_storage") == "ephemeral":
        data["login_storage"] = "ephemeral+encrypted-dashboard-backup"
    return data


ServerTikTokConnect.poll = _poll
ServerTikTokConnect.status = _status


def install_tiktok_profile_backup_routes(app: Any, connector: ServerTikTokConnect) -> None:
    @app.post("/api/tiktok/server-connect/restore")
    async def restore_server_tiktok_profile(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(400, "Expected JSON body.") from exc
        blob = str((payload or {}).get("blob") or "")
        return JSONResponse(restore_profile(connector, blob))

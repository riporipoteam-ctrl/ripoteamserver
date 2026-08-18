from __future__ import annotations

import hashlib
import os
import shutil
import tarfile
import tempfile
import threading
import urllib.request
from pathlib import Path

from recroom_wine_pool import RecRoomWinePool

_PATCH_REVISION = "kron4ek-wine-11.13-amd64-wow64-v1"
_ARCHIVE_URL = "https://github.com/Kron4ek/Wine-Builds/releases/download/11.13/wine-11.13-amd64-wow64.tar.xz"
_ARCHIVE_SHA256 = "889423273334f12bf2a4e2249f6ade72d7ceb466f72274925fda1e11b8326164"
_ARCHIVE_NAME = "wine-11.13-amd64-wow64.tar.xz"
_ROOT_NAME = "wine-11.13-amd64-wow64"
_INSTALL_LOCK = threading.Lock()
_ORIGINAL_INIT = RecRoomWinePool.__init__


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract(archive: Path, destination: Path) -> None:
    destination_resolved = destination.resolve()
    with tarfile.open(archive, mode="r:xz") as bundle:
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if target != destination_resolved and destination_resolved not in target.parents:
                raise RuntimeError(f"Portable Wine archive contains unsafe path: {member.name}")
        bundle.extractall(destination)


def ensure_portable_wine() -> Path:
    cache_root = Path(os.environ.get("RIPO_PORTABLE_WINE_DIR", str(Path.home() / ".cache" / "ripo-portable-wine"))).expanduser()
    runtime_root = cache_root / _ROOT_NAME
    wine = runtime_root / "bin" / "wine"
    wineboot = runtime_root / "bin" / "wineboot"
    wineserver = runtime_root / "bin" / "wineserver"
    marker = runtime_root / ".ripo-wow64-ready"

    if marker.is_file() and all(path.is_file() for path in (wine, wineboot, wineserver)):
        return runtime_root

    with _INSTALL_LOCK:
        if marker.is_file() and all(path.is_file() for path in (wine, wineboot, wineserver)):
            return runtime_root

        cache_root.mkdir(parents=True, exist_ok=True)
        archive = cache_root / _ARCHIVE_NAME
        if not archive.is_file() or _sha256(archive) != _ARCHIVE_SHA256:
            archive.unlink(missing_ok=True)
            temp = archive.with_suffix(archive.suffix + ".download")
            temp.unlink(missing_ok=True)
            request = urllib.request.Request(_ARCHIVE_URL, headers={"User-Agent": "RipoTeamServer-WoW64/1.0"})
            with urllib.request.urlopen(request, timeout=180) as response, temp.open("wb") as output:
                shutil.copyfileobj(response, output, length=4 * 1024 * 1024)
            actual = _sha256(temp)
            if actual != _ARCHIVE_SHA256:
                temp.unlink(missing_ok=True)
                raise RuntimeError(f"Portable Wine SHA256 mismatch: expected {_ARCHIVE_SHA256}, got {actual}")
            temp.replace(archive)

        staging = Path(tempfile.mkdtemp(prefix="ripo-wine-wow64-", dir=str(cache_root)))
        try:
            _safe_extract(archive, staging)
            extracted = staging / _ROOT_NAME
            required = [extracted / "bin" / name for name in ("wine", "wineboot", "wineserver")]
            if not all(path.is_file() for path in required):
                raise RuntimeError("Portable Wine archive is missing required executables.")
            for path in required:
                path.chmod(path.stat().st_mode | 0o111)
            shutil.rmtree(runtime_root, ignore_errors=True)
            extracted.replace(runtime_root)
            marker.write_text(_PATCH_REVISION + "\n", encoding="utf-8")
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    return runtime_root


def _portable_init(self: RecRoomWinePool, *args, **kwargs) -> None:
    _ORIGINAL_INIT(self, *args, **kwargs)
    runtime = ensure_portable_wine()
    bindir = runtime / "bin"
    self.wine = str(bindir / "wine")
    self.wineboot = str(bindir / "wineboot")
    self.wineserver = str(bindir / "wineserver")
    os.environ["PATH"] = f"{bindir}:{os.environ.get('PATH', '')}"
    os.environ["RIPO_PORTABLE_WINE_REVISION"] = _PATCH_REVISION


RecRoomWinePool.__init__ = _portable_init  # type: ignore[method-assign]
print(f"Rec Room portable WoW64 runtime loaded: {_PATCH_REVISION}")

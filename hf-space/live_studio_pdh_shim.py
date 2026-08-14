from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from live_studio_wine import LiveStudioWine
import live_studio_wine_launch_fix as launch_fix

_LOCK = threading.RLock()
_OLD_ENV = LiveStudioWine._env
_OLD_LAUNCH_ONE = launch_fix._launch_one


def _patched_env(self: LiveStudioWine) -> dict[str, str]:
    env = _OLD_ENV(self)
    existing = str(env.get("WINEDLLOVERRIDES") or "").strip(";")
    override = "pdh=n,b"
    if existing and "pdh=" not in existing.lower():
        override = override + ";" + existing
    elif existing:
        override = existing
    env["WINEDLLOVERRIDES"] = override
    return env


def _compiler() -> str | None:
    return shutil.which("x86_64-w64-mingw32-gcc")


def _source() -> Path:
    return Path(__file__).with_name("pdh_shim.c")


def _shim_path(exe: Path) -> Path:
    return exe.parent / "pdh.dll"


def _ensure_shim(exe: Path) -> Path:
    with _LOCK:
        target = _shim_path(exe)
        marker = exe.parent / ".ripo-pdh-shim-v1"
        if target.exists() and marker.exists() and target.stat().st_size > 10_000:
            return target

        compiler = _compiler()
        if not compiler:
            raise RuntimeError("MinGW x64 compiler is not installed; cannot build the LIVE Studio PDH compatibility shim.")
        source = _source()
        if not source.exists():
            raise RuntimeError("PDH compatibility shim source is missing from the Space image.")

        # Do not overwrite a vendor-supplied pdh.dll. LIVE Studio normally does
        # not ship one; if TikTok starts shipping its own, prefer theirs.
        if target.exists() and not marker.exists():
            return target

        temp = target.with_suffix(".ripo.tmp.dll")
        temp.unlink(missing_ok=True)
        command = [
            compiler,
            "-shared",
            "-O2",
            "-s",
            "-static-libgcc",
            str(source),
            "-o",
            str(temp),
        ]
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=90,
            check=False,
        )
        if proc.returncode != 0 or not temp.exists():
            raise RuntimeError("PDH shim compilation failed: " + (proc.stdout or "unknown compiler error")[-2500:])
        temp.replace(target)
        marker.write_text("Ripo PDH GPU-counter compatibility shim v1\n", encoding="utf-8")
        return target


def _launch_one(self: LiveStudioWine, wine: str, exe: Path, flags: list[str], label: str) -> bool:
    try:
        shim = _ensure_shim(exe)
        try:
            with self.logs.open("ab", buffering=0) as log:
                log.write((f"\n===== RIPO PDH shim active: {shim} | WINEDLLOVERRIDES=pdh=n,b =====\n").encode())
        except Exception:
            pass
    except Exception as exc:
        try:
            with self.logs.open("ab", buffering=0) as log:
                log.write((f"\n===== RIPO PDH shim setup failed: {exc} =====\n").encode())
        except Exception:
            pass
        # Let the normal launch still run so diagnostics remain truthful.
    return _OLD_LAUNCH_ONE(self, wine, exe, flags, label)


LiveStudioWine._env = _patched_env
launch_fix._launch_one = _launch_one

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import live_studio_wine_launch_fix as launch_fix


def _env(self: Any) -> dict[str, str]:
    env = self._env()
    env["DISPLAY"] = str(getattr(self, "display", env.get("DISPLAY", ":99")))
    return env


def _app_pids() -> list[int]:
    rows: list[int] = []
    try:
        proc_dirs = list(Path('/proc').iterdir())
    except Exception:
        return rows
    for proc in proc_dirs:
        if not proc.name.isdigit():
            continue
        try:
            cmd = (proc / 'cmdline').read_bytes().replace(b'\x00', b' ').decode('utf-8', errors='ignore').lower()
        except Exception:
            continue
        if 'tiktok live studio.exe' in cmd and '--type=' not in cmd:
            rows.append(int(proc.name))
    return rows


def _usable_windows(self: Any) -> list[str]:
    xdotool = shutil.which('xdotool')
    if not xdotool:
        return []
    env = self._env()
    rows: list[str] = []
    for pid in _app_pids():
        try:
            out = subprocess.check_output(
                [xdotool, 'search', '--onlyvisible', '--pid', str(pid)],
                env=env, text=True, timeout=4,
            )
        except Exception:
            continue
        for wid in [line.strip() for line in out.splitlines() if line.strip()]:
            try:
                title = subprocess.check_output([xdotool, 'getwindowname', wid], env=env, text=True, timeout=2).strip()
            except Exception:
                title = ''
            try:
                geom = subprocess.check_output([xdotool, 'getwindowgeometry', '--shell', wid], env=env, text=True, timeout=2)
                values = {}
                for line in geom.splitlines():
                    if '=' in line:
                        k, v = line.split('=', 1)
                        if k in {'WIDTH','HEIGHT'}:
                            values[k] = int(v)
                area = int(values.get('WIDTH',0)) * int(values.get('HEIGHT',0))
            except Exception:
                area = 0
            # Ignore tiny helper/tool windows. A real LIVE Studio surface should
            # be large enough to be usable from the 1366x768 virtual desktop.
            if area < 80_000:
                continue
            rows.append(f"wid={wid} pid={pid} area={area} title={title[:100]!r}")
    return rows[:12]


launch_fix._usable_windows = _usable_windows

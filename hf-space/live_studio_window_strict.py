from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import live_studio_wine_launch_fix as launch_fix


def _main_exe_pids() -> list[int]:
    """Return only the real TikTok LIVE Studio main executable process.

    Wine explorer/start command lines also contain the TikTok EXE path, so a
    substring check incorrectly treated the outer Wine desktop as the app.
    Requiring argv[0] itself to be TikTok LIVE Studio.exe avoids that.
    """
    rows: list[int] = []
    try:
        proc_dirs = list(Path('/proc').iterdir())
    except Exception:
        return rows
    for proc in proc_dirs:
        if not proc.name.isdigit():
            continue
        try:
            raw = (proc / 'cmdline').read_bytes()
            args = [part.decode('utf-8', errors='ignore') for part in raw.split(b'\x00') if part]
        except Exception:
            continue
        if not args:
            continue
        first = args[0].replace('\\', '/').lower().rstrip('/')
        first_name = first.rsplit('/', 1)[-1]
        rest = ' '.join(args[1:]).lower()
        if first_name != 'tiktok live studio.exe':
            continue
        if '--type=' in rest:
            continue
        rows.append(int(proc.name))
    return rows


def _usable_windows(self: Any) -> list[str]:
    xdotool = shutil.which('xdotool')
    if not xdotool:
        return []
    env = self._env()
    rows: list[str] = []
    for pid in _main_exe_pids():
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
                values: dict[str, int] = {}
                for line in geom.splitlines():
                    if '=' not in line:
                        continue
                    key, value = line.split('=', 1)
                    if key in {'WIDTH', 'HEIGHT'}:
                        values[key] = int(value)
                area = int(values.get('WIDTH', 0)) * int(values.get('HEIGHT', 0))
            except Exception:
                area = 0
            # Ignore tiny helper/tool windows. A usable LIVE Studio surface must
            # be large enough to contain its controls on the 1366x768 desktop.
            if area < 80_000:
                continue
            rows.append(f"wid={wid} pid={pid} area={area} title={title[:100]!r}")
    return rows[:12]


launch_fix._usable_windows = _usable_windows
launch_fix._app_pids = _main_exe_pids

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from live_studio_wine import LiveStudioWine


def _init_prefix(self: LiveStudioWine) -> None:
    wine = self._wine()
    boot = self._wineboot()
    if not wine:
        raise RuntimeError("64-bit Wine is not installed in the Space.")

    system_reg = self.prefix / "system.reg"
    if system_reg.exists():
        return

    # A previous interrupted wineboot can leave hundreds of MB behind without
    # a usable registry. Start clean rather than treating that as a valid prefix.
    if self.prefix.exists():
        try:
            shutil.rmtree(self.prefix)
        except Exception:
            pass
    self.prefix.mkdir(parents=True, exist_ok=True)

    self.phase = "initializing-wine"
    env = self._env()
    log_path = self.data_dir / "wineboot.log"
    command = [boot, "-i"] if boot else [wine, "wineboot", "-i"]

    with log_path.open("ab", buffering=0) as log:
        try:
            subprocess.run(
                command,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=90,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log.write(b"\n[ripo] wineboot --init timed out; waiting for wineserver state.\n")

        # wineboot can return while child initialization work is still running.
        wineserver = shutil.which("wineserver")
        if wineserver:
            try:
                subprocess.run(
                    [wineserver, "-w"],
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=35,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                log.write(b"\n[ripo] wineserver -w timed out; checking registry files anyway.\n")

    deadline = time.time() + 20
    while time.time() < deadline and not system_reg.exists():
        time.sleep(1)

    if not system_reg.exists():
        tail = ""
        try:
            tail = log_path.read_text(errors="replace")[-1800:]
        except Exception:
            pass
        names = []
        try:
            names = sorted(p.name for p in self.prefix.iterdir())[:40]
        except Exception:
            pass
        raise RuntimeError(
            "Wine could not finish creating a 64-bit Windows prefix. "
            f"Prefix files: {names}."
            + ((" wineboot: " + tail) if tail else "")
        )


def _has_live_studio_window(self: LiveStudioWine) -> bool:
    if self.process and self.process.poll() is None:
        return True

    xdotool = shutil.which("xdotool")
    if not xdotool:
        return False
    env = self._env()
    for name in ("TikTok LIVE Studio", "LIVE Studio", "TikTok Studio"):
        try:
            out = subprocess.check_output(
                [xdotool, "search", "--onlyvisible", "--name", name],
                env=env,
                text=True,
                timeout=3,
            )
            for wid in (x.strip() for x in out.splitlines() if x.strip()):
                try:
                    pid_text = subprocess.check_output(
                        [xdotool, "getwindowpid", wid], env=env, text=True, timeout=3
                    ).strip()
                    pid = int(pid_text)
                    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore").lower()
                except Exception:
                    continue
                # The server Firefox tab is also titled "TikTok LIVE Studio";
                # never count a browser window as the Windows application.
                if any(x in cmdline for x in ("firefox", "chromium", "chrome")):
                    continue
                if any(x in cmdline for x in ("wine", ".exe", "tiktok", "live studio")):
                    return True
        except Exception:
            pass
    return False


LiveStudioWine._init_prefix = _init_prefix
LiveStudioWine._has_live_studio_window = _has_live_studio_window

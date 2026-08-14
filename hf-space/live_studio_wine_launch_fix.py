from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from live_studio_wine import LiveStudioWine

_OLD_STATUS = LiveStudioWine.status


def _score_exe(path: Path) -> int:
    name = path.name.lower().strip()
    rel = path.as_posix().lower()
    reject = (
        "uninstall", "unins", "setup", "installer", "update", "updater", "crash", "dump",
        "helper", "elevat", "inject", "vc_redist", "dxsetup", "notification_helper",
    )
    if any(x in name for x in reject):
        return -10000
    score = 0
    if name == "tiktok live studio launcher.exe":
        score = 5000
    elif "launcher" in name and ("tiktok" in rel or "live" in rel or "studio" in rel):
        score = 4500
    elif name in {"launcher.exe", "launch.exe"}:
        score = 3900
    elif name == "tiktok live studio.exe":
        score = 3000
    elif "tiktok" in name and "live" in name and "studio" in name:
        score = 2700
    elif "livestudio" in name:
        score = 2500
    if score <= 0:
        return score
    if "win32-x64" in rel or "x64" in rel:
        score += 120
    if "$pluginsdir" in rel:
        score -= 1000
    try:
        if path.stat().st_size > 5_000_000:
            score += 50
    except OSError:
        pass
    return score


def _all_extracted_candidates(self: LiveStudioWine) -> list[Path]:
    rows: list[tuple[int, int, Path]] = []
    roots = sorted(self.data_dir.glob("extracted-live-studio-*"), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    for root in roots[:2]:
        try:
            for path in root.rglob("*.exe"):
                score = _score_exe(path)
                if score <= 0:
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                rows.append((score, size, path))
        except Exception:
            pass
    rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
    result: list[Path] = []
    seen: set[str] = set()
    for _, _, path in rows:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result[:8]


def _process_rows(self: LiveStudioWine) -> list[str]:
    rows: list[str] = []
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        try:
            text = (proc_dir / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()
        except Exception:
            continue
        low = text.lower()
        if "tiktok live studio" in low or ("wine" in low and "tiktok" in low):
            rows.append(f"{proc_dir.name}: {text[:500]}")
    return rows[:20]


def _window_rows(self: LiveStudioWine) -> list[str]:
    xdotool = shutil.which("xdotool")
    if not xdotool:
        return []
    env = self._env()
    rows: list[str] = []
    try:
        out = subprocess.check_output([xdotool, "search", "--onlyvisible", "--name", "."], env=env, text=True, timeout=4)
    except Exception:
        return []
    for wid in [x.strip() for x in out.splitlines() if x.strip()][:80]:
        try:
            title = subprocess.check_output([xdotool, "getwindowname", wid], env=env, text=True, timeout=2).strip()
        except Exception:
            title = ""
        try:
            pid = subprocess.check_output([xdotool, "getwindowpid", wid], env=env, text=True, timeout=2).strip()
            cmd = Path(f"/proc/{int(pid)}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="ignore").strip()
        except Exception:
            pid, cmd = "", ""
        low = (title + " " + cmd).lower()
        if any(x in low for x in ("tiktok live studio", "livestudio", "wine")) and "firefox" not in low:
            rows.append(f"{wid} pid={pid} title={title!r} cmd={cmd[:350]}")
    return rows[:20]


def _has_app_alive(self: LiveStudioWine, proc: subprocess.Popen[Any] | None) -> bool:
    if proc is not None and proc.poll() is None:
        return True
    if _process_rows(self):
        return True
    if _window_rows(self):
        return True
    try:
        return bool(self._has_live_studio_window())
    except Exception:
        return False


def _kill_prefix(self: LiveStudioWine) -> None:
    wineserver = shutil.which("wineserver")
    if wineserver:
        try:
            subprocess.run([wineserver, "-k"], env=self._env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12, check=False)
        except Exception:
            pass
    time.sleep(2)


def _launch_one(self: LiveStudioWine, wine: str, exe: Path, flags: list[str], label: str) -> bool:
    env = self._env()
    env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    env.setdefault("MESA_LOADER_DRIVER_OVERRIDE", "llvmpipe")
    env.setdefault("ELECTRON_OZONE_PLATFORM_HINT", "x11")
    env.setdefault("QT_X11_NO_MITSHM", "1")
    self.logs.parent.mkdir(parents=True, exist_ok=True)
    with self.logs.open("ab", buffering=0) as log:
        log.write((f"\n\n===== RIPO launch attempt: {label} | {exe} | flags={flags} =====\n").encode())
        proc = subprocess.Popen(
            [wine, str(exe), *flags],
            cwd=str(exe.parent),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.process = proc
        deadline = time.time() + 28
        alive_streak = 0
        while time.time() < deadline:
            time.sleep(2)
            if _has_app_alive(self, proc):
                alive_streak += 1
                if alive_streak >= 5:
                    return True
            else:
                alive_streak = 0
                if proc.poll() is not None:
                    break
    return _has_app_alive(self, proc)


def _worker(self: LiveStudioWine) -> None:
    self.launch_attempts = []
    try:
        wine = self._wine()
        if not wine:
            raise RuntimeError("Wine is not installed on the Space.")
        self._init_prefix()

        candidates = _all_extracted_candidates(self)
        if not candidates:
            package_url, version, expected_md5 = self._resolve_package()
            installer = self._download_package(package_url, version, expected_md5)
            self._extract_package(installer, version)
            candidates = _all_extracted_candidates(self)
        if not candidates:
            raise RuntimeError("TikTok package was extracted but no plausible LIVE Studio launcher/application EXE could be found.")

        modes = [
            ("normal", []),
            ("software-gpu", ["--disable-gpu", "--disable-gpu-compositing", "--disable-gpu-sandbox"]),
            ("software-no-sandbox", ["--disable-gpu", "--disable-gpu-compositing", "--disable-gpu-sandbox", "--no-sandbox", "--use-gl=swiftshader"]),
        ]

        self.phase = "launching-live-studio"
        failures: list[str] = []
        # Try the most launcher-like candidates first, then the direct app.
        for exe in candidates[:4]:
            self.installed_exe = str(exe)
            for label, flags in modes:
                self.launch_attempts.append(f"{exe.name}: {label}")
                if _launch_one(self, wine, exe, flags, label):
                    self.phase = "running-wine"
                    self.last_error = ""
                    self.last_note = f"TikTok LIVE Studio is alive under Wine using {exe.name} ({label})."
                    return
                failures.append(f"{exe.name}/{label}")
                _kill_prefix(self)

        tail = ""
        try:
            tail = self.logs.read_text(errors="replace")[-5500:]
        except Exception:
            pass
        processes = _process_rows(self)
        windows = _window_rows(self)
        raise RuntimeError(
            "TikTok LIVE Studio extracted successfully but every launcher/app mode exited. "
            f"Tried: {', '.join(failures)}. "
            f"Remaining processes: {processes[:5]}. Visible Wine windows: {windows[:5]}."
            + ((" Log: " + tail) if tail else "")
        )
    except Exception as exc:
        self.phase = "wine-failed"
        self.last_error = str(exc)[:9000]
        probe = self._vm_probe()
        if probe.get("kvm_access"):
            self.last_note = "Wine failed. KVM is available for a Windows VM fallback."
        else:
            self.last_note = "Wine failed. This Hugging Face Space does not expose KVM; a Windows VM here would use slow software emulation."


def _status(self: LiveStudioWine) -> dict[str, Any]:
    data = _OLD_STATUS(self)
    data["launch_attempts"] = list(getattr(self, "launch_attempts", []))[-20:]
    data["live_studio_processes"] = _process_rows(self)
    data["live_studio_windows"] = _window_rows(self)
    candidates = _all_extracted_candidates(self)
    data["extracted_candidates"] = [p.name for p in candidates[:8]]
    return data


LiveStudioWine._all_extracted_candidates = _all_extracted_candidates
LiveStudioWine._worker = _worker
LiveStudioWine.status = _status

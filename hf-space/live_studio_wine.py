from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import Header
from fastapi.responses import JSONResponse


class LiveStudioWine:
    """Best-effort TikTok LIVE Studio launcher using 64-bit Wine.

    This is intentionally small: it uses the distro's 64-bit Wine packages and
    a dedicated win64 prefix. If LIVE Studio cannot run under Wine, status()
    exposes a VM capability probe before we consider installing QEMU/Windows.
    """

    DOWNLOAD_PAGE = "https://www.tiktok.com/studio/download?open_ls=1"

    def __init__(self, ai: Any, connector: Any, data_dir: Path, authorize: Callable[[str | None], None], display: str) -> None:
        self.ai = ai
        self.connector = connector
        self.data_dir = data_dir
        self.authorize = authorize
        self.display = display
        self.prefix = data_dir / "wine-prefix"
        self.downloads = data_dir / "downloads"
        self.logs = data_dir / "wine-live-studio.log"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.downloads.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.worker: threading.Thread | None = None
        self.process: subprocess.Popen[Any] | None = None
        self.phase = "idle"
        self.last_error = ""
        self.last_note = ""
        self.installer: str = ""
        self.installed_exe: str = ""
        self.started_at: float | None = None

    def _auth(self, token: str | None) -> None:
        if self.ai.session_valid(token):
            return
        self.authorize(token)

    def _wine(self) -> str | None:
        for candidate in (shutil.which("wine64"), shutil.which("wine"), "/usr/lib/wine/wine64"):
            if candidate and Path(candidate).exists():
                return str(candidate)
        return None

    def _wineboot(self) -> str | None:
        for candidate in (shutil.which("wineboot"), "/usr/lib/wine/wineboot"):
            if candidate and Path(candidate).exists():
                return str(candidate)
        return None

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["DISPLAY"] = self.display
        env["WINEPREFIX"] = str(self.prefix)
        env["WINEARCH"] = "win64"
        env.setdefault("HOME", str(Path.home()))
        env.setdefault("USER", Path.home().name)
        env.setdefault("WINEDEBUG", "-all")
        runtime = Path(f"/tmp/ripo-runtime-{os.getuid()}")
        runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        env.setdefault("XDG_RUNTIME_DIR", str(runtime))
        return env

    def _wine_version(self) -> str:
        wine = self._wine()
        if not wine:
            return ""
        try:
            out = subprocess.check_output([wine, "--version"], env=self._env(), text=True, stderr=subprocess.STDOUT, timeout=8)
            return out.strip()[:120]
        except Exception as exc:
            return f"error: {exc}"[:180]

    def _vm_probe(self) -> dict[str, Any]:
        kvm = Path("/dev/kvm")
        try:
            usage = shutil.disk_usage(self.data_dir)
            free_gb = round(usage.free / (1024**3), 1)
        except Exception:
            free_gb = None
        cpu_flags = ""
        try:
            cpu_flags = Path("/proc/cpuinfo").read_text(errors="ignore")[:200000]
        except Exception:
            pass
        virt_flag = (" vmx " in f" {cpu_flags} ") or (" svm " in f" {cpu_flags} ")
        return {
            "kvm_device": kvm.exists(),
            "kvm_access": bool(kvm.exists() and os.access(kvm, os.R_OK | os.W_OK)),
            "cpu_virtualization_flag": virt_flag,
            "qemu_installed": bool(shutil.which("qemu-system-x86_64")),
            "free_disk_gb": free_gb,
            "vm_install_started": False,
        }

    def _prefix_bytes(self) -> int:
        total = 0
        if not self.prefix.exists():
            return 0
        try:
            for root, _, files in os.walk(self.prefix):
                for name in files:
                    try:
                        total += (Path(root) / name).stat().st_size
                    except OSError:
                        pass
        except Exception:
            pass
        return total

    def status(self) -> dict[str, Any]:
        running = bool(self.process and self.process.poll() is None)
        return {
            "ok": True,
            "phase": self.phase,
            "wine_installed": bool(self._wine()),
            "wine_version": self._wine_version(),
            "wine_prefix_mb": round(self._prefix_bytes() / (1024**2), 1),
            "live_studio_installer": bool(self.installer and Path(self.installer).exists()),
            "live_studio_installed": bool(self.installed_exe and Path(self.installed_exe).exists()),
            "live_studio_running": running or self._has_live_studio_window(),
            "installer_path": Path(self.installer).name if self.installer else "",
            "installed_exe": Path(self.installed_exe).name if self.installed_exe else "",
            "last_error": self.last_error,
            "note": self.last_note,
            "started_at": self.started_at,
            "vm_fallback": self._vm_probe(),
        }

    def _firefox_window(self) -> str:
        xdotool = shutil.which("xdotool")
        if not xdotool:
            raise RuntimeError("xdotool is missing.")
        env = self._env()
        for args in (("--class", "firefox"), ("--name", "Firefox"), ("--name", "TikTok")):
            try:
                out = subprocess.check_output([xdotool, "search", "--onlyvisible", *args], env=env, text=True, timeout=5)
                ids = [x.strip() for x in out.splitlines() if x.strip()]
                if ids:
                    return ids[-1]
            except Exception:
                pass
        raise RuntimeError("Server Firefox is not open. Connect TikTok to the server first.")

    def _key(self, window: str, key: str) -> None:
        subprocess.run(["xdotool", "key", "--window", window, "--clearmodifiers", key], env=self._env(), timeout=8, check=True)

    def _type(self, window: str, text: str) -> None:
        subprocess.run(["xdotool", "type", "--window", window, "--clearmodifiers", "--delay", "0", "--", text], env=self._env(), timeout=25, check=True)

    def _navigate(self, url: str) -> str:
        w = self._firefox_window()
        subprocess.run(["xdotool", "windowactivate", "--sync", w], env=self._env(), timeout=8, check=True)
        self._key(w, "ctrl+l")
        self._type(w, url)
        self._key(w, "Return")
        return w

    def _click_download(self, window: str) -> None:
        js = "javascript:(()=>{const t=e=>((e.innerText||e.textContent||e.getAttribute?.('aria-label')||'').replace(/\\s+/g,' ').trim());const a=[...document.querySelectorAll('a,button,[role=button]')].find(e=>/download for windows/i.test(t(e)))||[...document.querySelectorAll('a')].find(e=>/windows/i.test(t(e))&&/download/i.test(t(e)));if(a){a.click();document.title='RIPO_DOWNLOAD_CLICKED'}else{document.title='RIPO_DOWNLOAD_NOT_FOUND'}})()"
        self._key(window, "ctrl+l")
        self._type(window, js)
        self._key(window, "Return")

    def _find_downloaded_installer(self, after: float) -> Path | None:
        roots = [self.downloads, Path.home() / "Downloads", self.connector.profile_dir / "downloads"]
        choices: list[Path] = []
        for root in roots:
            if not root.exists():
                continue
            try:
                for p in root.rglob("*.exe"):
                    try:
                        if p.stat().st_mtime >= after - 3 and p.stat().st_size > 1_000_000:
                            choices.append(p)
                    except OSError:
                        pass
            except Exception:
                pass
        if not choices:
            return None
        choices.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return choices[0]

    def _init_prefix(self) -> None:
        self.prefix.mkdir(parents=True, exist_ok=True)
        if (self.prefix / "system.reg").exists():
            return
        boot = self._wineboot()
        wine = self._wine()
        if not wine:
            raise RuntimeError("64-bit Wine is not installed in the Space.")
        self.phase = "initializing-wine"
        if boot:
            cmd = [boot, "-u"]
        else:
            cmd = [wine, "wineboot", "-u"]
        subprocess.run(cmd, env=self._env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90, check=False)
        if not (self.prefix / "system.reg").exists():
            raise RuntimeError("Wine could not create a 64-bit Windows prefix.")

    def _candidate_exes(self) -> list[Path]:
        if not self.prefix.exists():
            return []
        rows: list[Path] = []
        try:
            for p in self.prefix.rglob("*.exe"):
                s = str(p).lower()
                name = p.name.lower()
                if "tiktok" in s and ("live" in s or "studio" in s):
                    if not any(x in name for x in ("unins", "uninstall", "setup", "installer", "update", "crash")):
                        rows.append(p)
        except Exception:
            pass
        rows.sort(key=lambda p: ("live studio" in p.name.lower(), p.stat().st_size if p.exists() else 0), reverse=True)
        return rows

    def _has_live_studio_window(self) -> bool:
        xdotool = shutil.which("xdotool")
        if not xdotool:
            return False
        for name in ("TikTok LIVE Studio", "LIVE Studio", "TikTok Studio"):
            try:
                out = subprocess.check_output([xdotool, "search", "--onlyvisible", "--name", name], env=self._env(), text=True, timeout=3)
                if out.strip():
                    return True
            except Exception:
                pass
        return False

    def _drive_installer(self) -> None:
        xdotool = shutil.which("xdotool")
        if not xdotool:
            return
        for _ in range(18):
            if self._candidate_exes():
                return
            for pattern in ("TikTok", "LIVE Studio", "Setup", "Installer"):
                try:
                    out = subprocess.check_output([xdotool, "search", "--onlyvisible", "--name", pattern], env=self._env(), text=True, timeout=3)
                    ids = [x.strip() for x in out.splitlines() if x.strip()]
                    if ids:
                        wid = ids[-1]
                        subprocess.run([xdotool, "windowactivate", "--sync", wid], env=self._env(), timeout=5, check=False)
                        subprocess.run([xdotool, "key", "--window", wid, "Return"], env=self._env(), timeout=5, check=False)
                except Exception:
                    pass
            time.sleep(2.5)

    def try_start(self) -> dict[str, Any]:
        with self.lock:
            if self.worker and self.worker.is_alive():
                return {"ok": True, "message": "Wine LIVE Studio test is already running.", **self.status()}
            self.started_at = time.time()
            self.phase = "starting"
            self.last_error = ""
            self.last_note = ""
            self.worker = threading.Thread(target=self._worker, daemon=True, name="ripo-live-studio-wine")
            self.worker.start()
        return {"ok": True, "message": "Testing TikTok LIVE Studio under 64-bit Wine on the Ripo server.", **self.status()}

    def _worker(self) -> None:
        try:
            wine = self._wine()
            if not wine:
                raise RuntimeError("Wine is not installed yet. The Space must finish rebuilding with wine64.")
            if not self.connector.status().get("browser_running"):
                raise RuntimeError("TikTok server browser is not running. Connect TikTok again after the Space restart, then press START LIVE.")

            self._init_prefix()
            exes = self._candidate_exes()
            if exes:
                self.installed_exe = str(exes[0])
            else:
                self.phase = "downloading-live-studio"
                mark = time.time()
                w = self._navigate(self.DOWNLOAD_PAGE)
                time.sleep(6)
                self._click_download(w)
                installer: Path | None = None
                deadline = time.time() + 75
                while time.time() < deadline:
                    installer = self._find_downloaded_installer(mark)
                    if installer and not Path(str(installer) + ".part").exists():
                        break
                    time.sleep(2)
                if not installer:
                    raise RuntimeError("TikTok's official page did not produce a Windows installer download in server Firefox.")
                self.installer = str(installer)
                self.phase = "installing-live-studio"
                log = self.logs.open("ab", buffering=0)
                installer_proc = subprocess.Popen([wine, str(installer)], env=self._env(), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                self._drive_installer()
                try:
                    installer_proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
                deadline = time.time() + 35
                while time.time() < deadline:
                    exes = self._candidate_exes()
                    if exes:
                        self.installed_exe = str(exes[0])
                        break
                    time.sleep(2)
                if not self.installed_exe:
                    raise RuntimeError("LIVE Studio installer opened under Wine but no installed LIVE Studio executable appeared. Wine compatibility is likely insufficient.")

            self.phase = "launching-live-studio"
            log = self.logs.open("ab", buffering=0)
            self.process = subprocess.Popen([wine, self.installed_exe], env=self._env(), stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            time.sleep(18)
            if self.process.poll() is not None and not self._has_live_studio_window():
                tail = ""
                try:
                    tail = self.logs.read_text(errors="replace")[-1400:]
                except Exception:
                    pass
                raise RuntimeError("TikTok LIVE Studio exited under Wine before opening a usable window." + ((" Log: " + tail) if tail else ""))
            self.phase = "running-wine"
            self.last_note = "TikTok LIVE Studio is running on the Linux server through 64-bit Wine."
        except Exception as exc:
            self.phase = "wine-failed"
            self.last_error = str(exc)[:3500]
            probe = self._vm_probe()
            if probe.get("kvm_access"):
                self.last_note = "Wine failed. KVM is available, so a small Windows VM is technically possible as the fallback."
            else:
                self.last_note = "Wine failed. /dev/kvm is not accessible, so a Windows VM would have to use slow software emulation and is not a good fallback on this Space."

    def stop(self) -> dict[str, Any]:
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=8)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        self.process = None
        self.phase = "idle"
        return {"ok": True, "message": "Wine LIVE Studio process stopped.", **self.status()}


def install_live_studio_wine_routes(app: Any, wine_runner: LiveStudioWine) -> None:
    @app.get("/api/tiktok/live-studio-linux/status")
    async def wine_status() -> JSONResponse:
        return JSONResponse(wine_runner.status())

    @app.post("/api/tiktok/live-studio-linux/try")
    async def wine_try(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        wine_runner._auth(x_admin_token)
        return JSONResponse(wine_runner.try_start())

    @app.post("/api/tiktok/live-studio-linux/stop")
    async def wine_stop(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        wine_runner._auth(x_admin_token)
        return JSONResponse(wine_runner.stop())

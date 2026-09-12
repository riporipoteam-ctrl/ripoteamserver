from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable

import recroom_wine_runtime_fix as runtime_fix
from recroom_wine_pool import RecRoomWinePool, WineInstance


_PATCH_REVISION = "aug25-2021-official-steam-runtime-v2"
_STEAM_SETUP_URL = "https://cdn.fastly.steamstatic.com/client/installer/SteamSetup.exe"
_STEAM_APP_ID = "471710"
_STEAM_LOGIN_WAIT_SECONDS = max(120, int(os.environ.get("RECROOM_STEAM_LOGIN_WAIT_SECONDS", "1200")))
_STEAM_INSTALL_LOCK = threading.Lock()
_CALLBACK_LOCK = threading.RLock()
_CALLBACKS: dict[str, tuple[Callable[[str, int], None], Callable[[str], None], Callable[[str], None]]] = {}
_ORIGINAL_ENSURE_BASE_PREFIX = RecRoomWinePool._ensure_base_prefix
_ORIGINAL_START_STREAM = RecRoomWinePool._start_stream
_ORIGINAL_PROVISION = RecRoomWinePool.provision
_ORIGINAL_RENDER_CHECK = runtime_fix._has_rendered_content


def _steam_exe(prefix: Path) -> Path:
    return prefix / "drive_c" / "Program Files (x86)" / "Steam" / "Steam.exe"


def _steam_loginusers(prefix: Path) -> Path:
    return prefix / "drive_c" / "Program Files (x86)" / "Steam" / "config" / "loginusers.vdf"


def _steam_setup_path(self: RecRoomWinePool) -> Path:
    root = self.data_dir / "_steam-runtime"
    root.mkdir(parents=True, exist_ok=True)
    return root / "SteamSetup.exe"


def _download_steam_setup(self: RecRoomWinePool) -> Path:
    target = _steam_setup_path(self)
    if target.is_file() and target.stat().st_size > 1_000_000:
        return target
    temp = target.with_suffix(".download")
    temp.unlink(missing_ok=True)
    request = urllib.request.Request(
        _STEAM_SETUP_URL,
        headers={"User-Agent": "RipoTeamServer-SteamBootstrap/1.0"},
    )
    with urllib.request.urlopen(request, timeout=120) as response, temp.open("wb") as handle:
        shutil.copyfileobj(response, handle, length=1024 * 1024)
    if temp.stat().st_size <= 1_000_000 or temp.read_bytes()[:2] != b"MZ":
        temp.unlink(missing_ok=True)
        raise RuntimeError("Official Steam installer download was invalid.")
    temp.replace(target)
    return target


def _base_env(self: RecRoomWinePool, display: int) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "DISPLAY": f":{display}",
            "WINEPREFIX": str(self.base_prefix),
            "WINEARCH": "win64",
            "WINEDEBUG": "-all",
        }
    )
    return env


def _ensure_official_steam(self: RecRoomWinePool, display: int) -> None:
    _ORIGINAL_ENSURE_BASE_PREFIX(self, display)
    installed = _steam_exe(self.base_prefix)
    if installed.is_file():
        return

    with _STEAM_INSTALL_LOCK:
        if installed.is_file():
            return
        if not self.wine:
            raise RuntimeError("Wine is unavailable for the Steam client bootstrap.")
        setup = _download_steam_setup(self)
        env = _base_env(self, display)
        result = subprocess.run(
            [str(self.wine), str(setup), "/S"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=180,
            check=False,
        )
        deadline = time.time() + 90
        while time.time() < deadline and not installed.is_file():
            time.sleep(1)
        if not installed.is_file():
            compact = " ".join((result.stdout or "").split())[-1200:]
            raise RuntimeError(f"Official Steam client did not install into the Wine prefix. {compact}")
        if self.wineserver:
            subprocess.run(
                [str(self.wineserver), "-k"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=12,
                check=False,
            )


def _has_remembered_steam_user(prefix: Path) -> bool:
    path = _steam_loginusers(prefix)
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return False
    return bool(
        re.search(r'"AccountName"\s+"[^"]+"', text, flags=re.IGNORECASE)
        and re.search(r'"MostRecent"\s+"1"', text, flags=re.IGNORECASE)
    )


def _find_steam_windows(self: RecRoomWinePool, instance: WineInstance) -> list[str]:
    xdotool = shutil.which("xdotool")
    if not xdotool:
        return []
    env = self._wine_env(instance)
    windows: list[str] = []
    for mode, value in (("--name", "Steam"), ("--class", "Steam"), ("--class", "steam")):
        try:
            result = subprocess.run(
                [xdotool, "search", "--onlyvisible", mode, value],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=3,
                check=False,
            )
        except Exception:
            continue
        for item in result.stdout.splitlines():
            item = item.strip()
            if item and item not in windows:
                windows.append(item)
    return windows


def _focus_steam_window(self: RecRoomWinePool, instance: WineInstance) -> bool:
    xdotool = shutil.which("xdotool")
    if not xdotool:
        return False
    env = self._wine_env(instance)
    deadline = time.time() + 45
    while time.time() < deadline:
        windows = _find_steam_windows(self, instance)
        if windows:
            window = windows[-1]
            subprocess.run([xdotool, "windowactivate", "--sync", window], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4, check=False)
            subprocess.run([xdotool, "windowraise", window], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4, check=False)
            subprocess.run([xdotool, "windowsize", window, str(self.width), str(self.height)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4, check=False)
            return True
        time.sleep(0.5)
    return False


def _minimize_steam_windows(self: RecRoomWinePool, instance: WineInstance) -> None:
    xdotool = shutil.which("xdotool")
    if not xdotool:
        return
    env = self._wine_env(instance)
    for window in _find_steam_windows(self, instance):
        subprocess.run([xdotool, "windowminimize", window], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4, check=False)


def _start_steam_and_wait(self: RecRoomWinePool, instance: WineInstance) -> None:
    _ORIGINAL_START_STREAM(self, instance)

    appid = instance.client_dir / "steam_appid.txt"
    appid.write_text(_STEAM_APP_ID + "\n", encoding="ascii")

    steam = _steam_exe(instance.prefix_dir)
    if not steam.is_file():
        raise RuntimeError("Official Steam client is missing from the cloned Wine sandbox.")
    env = self._wine_env(instance)
    log_path = instance.work_dir / "wine-steam.log"
    log = log_path.open("ab", buffering=0)
    process = subprocess.Popen(
        [str(self.wine), str(steam), "-no-cef-sandbox"],
        cwd=steam.parent,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    setattr(instance, "steam_process", process)

    with _CALLBACK_LOCK:
        callbacks = _CALLBACKS.get(instance.host_id)
    if not callbacks:
        raise RuntimeError("Steam sandbox callbacks were not registered.")
    on_progress, on_ready, _on_failed = callbacks

    focused = _focus_steam_window(self, instance)
    if not focused:
        time.sleep(2)
    on_ready(self.public_stream_url(instance))
    on_progress("steam-login-required", 60 if focused else 58)

    deadline = time.time() + _STEAM_LOGIN_WAIT_SECONDS
    while time.time() < deadline:
        if instance.destroying:
            return
        if _has_remembered_steam_user(instance.prefix_dir):
            on_progress("steam-authenticated", 64)
            _minimize_steam_windows(self, instance)
            time.sleep(3)
            return
        if process.poll() is not None:
            process = subprocess.Popen(
                [str(self.wine), str(steam), "-no-cef-sandbox"],
                cwd=steam.parent,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            setattr(instance, "steam_process", process)
            _focus_steam_window(self, instance)
        time.sleep(2)

    raise RuntimeError(
        "Steam sign-in was not completed in the browser session. Sign in to Steam in the streamed desktop and retry."
    )


def _provision_with_steam_callbacks(
    self: RecRoomWinePool,
    host_id: str,
    session_id: str,
    session_token: str,
    on_progress: Callable[[str, int], None],
    on_ready: Callable[[str], None],
    on_failed: Callable[[str], None],
) -> tuple[bool, str | None]:
    with _CALLBACK_LOCK:
        _CALLBACKS[host_id] = (on_progress, on_ready, on_failed)
    try:
        return _ORIGINAL_PROVISION(self, host_id, session_id, session_token, on_progress, on_ready, on_failed)
    except Exception:
        with _CALLBACK_LOCK:
            _CALLBACKS.pop(host_id, None)
        raise


def _render_check_rejects_steam_failure(instance: WineInstance) -> tuple[bool, str]:
    try:
        logs = sorted(instance.work_dir.glob("wine-game-*.log"), key=lambda p: p.stat().st_mtime_ns)
    except OSError:
        logs = []
    for path in logs[-2:]:
        try:
            tail = path.read_bytes()[-96_000:].decode("utf-8", "replace").casefold()
        except OSError:
            continue
        if "failed to initialize steam platform" in tail:
            return False, "steam-platform-initialization-failed"
    return _ORIGINAL_RENDER_CHECK(instance)


RecRoomWinePool._ensure_base_prefix = _ensure_official_steam  # type: ignore[method-assign]
RecRoomWinePool._start_stream = _start_steam_and_wait  # type: ignore[method-assign]
RecRoomWinePool.provision = _provision_with_steam_callbacks  # type: ignore[method-assign]
runtime_fix._has_rendered_content = _render_check_rejects_steam_failure
print(f"Rec Room official Steam runtime loaded: {_PATCH_REVISION}")

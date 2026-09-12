from __future__ import annotations

import os
import secrets
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException

from desktop_http import _new_session
from server_tiktok_connect import ServerTikTokConnect


_OLD_INIT = ServerTikTokConnect.__init__
_OLD_WRITE_PREFS = ServerTikTokConnect._write_profile_prefs


def _init(self: ServerTikTokConnect, ai, data_dir: Path, display: str) -> None:
    _OLD_INIT(self, ai, data_dir, display)
    self.login_storage = "ephemeral"

    # Hugging Face persistent storage, when attached, is mounted at /data.
    # Use it for Firefox cookies/profile automatically. On free Spaces without
    # /data we keep the normal local profile and the signed Ripo control session
    # still survives restarts on the user's device.
    root = Path(os.environ.get("RIPO_TIKTOK_PERSISTENT_DIR", "/data/ripo-tiktok"))
    try:
        parent = root.parent
        if parent.exists() and os.access(parent, os.W_OK):
            root.mkdir(parents=True, exist_ok=True)
            self.profile_dir = root / "firefox-profile"
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self.login_storage = "persistent-/data"
    except Exception:
        self.login_storage = "ephemeral"

    self._write_profile_prefs()


def _write_profile_prefs(self: ServerTikTokConnect) -> None:
    _OLD_WRITE_PREFS(self)
    extra = [
        'user_pref("privacy.clearOnShutdown.cookies", false);',
        'user_pref("privacy.clearOnShutdown.siteSettings", false);',
        'user_pref("network.cookie.lifetimePolicy", 0);',
        'user_pref("browser.privatebrowsing.autostart", false);',
        'user_pref("signon.rememberSignons", true);',
        'user_pref("browser.sessionstore.resume_from_crash", true);',
    ]
    try:
        path = self.profile_dir / "user.js"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        for line in extra:
            if line not in existing:
                existing += line + "\n"
        path.write_text(existing, encoding="utf-8")
    except Exception:
        pass


def _navigate_running_firefox(self: ServerTikTokConnect, url: str) -> bool:
    xdotool = subprocess.run(["sh", "-lc", "command -v xdotool"], capture_output=True, text=True).stdout.strip()
    if not xdotool:
        return False
    env = self._env()
    try:
        out = subprocess.check_output(
            [xdotool, "search", "--onlyvisible", "--class", "firefox"],
            env=env,
            text=True,
            timeout=5,
        )
        windows = [row.strip() for row in out.splitlines() if row.strip()]
        if not windows:
            return False
        wid = windows[-1]
        subprocess.run([xdotool, "windowactivate", "--sync", wid], env=env, timeout=5, check=False)
        subprocess.run([xdotool, "key", "--window", wid, "--clearmodifiers", "ctrl+l"], env=env, timeout=5, check=True)
        subprocess.run([xdotool, "type", "--window", wid, "--clearmodifiers", "--delay", "0", "--", url], env=env, timeout=20, check=True)
        subprocess.run([xdotool, "key", "--window", wid, "Return"], env=env, timeout=5, check=True)
        return True
    except Exception:
        return False


def _status(self: ServerTikTokConnect) -> dict:
    browser_running = bool(self.browser and self.browser.poll() is None)
    return {
        "ok": True,
        "firefox_installed": bool(self._firefox()),
        "browser_running": browser_running,
        "display": self.display,
        "oauth_configured": bool(self.ai.client_key and self.ai.client_secret and self.ai.redirect_uri),
        "account": self.ai.status().get("oauth_account", {}),
        "active_flows": sum(1 for row in self.flows.values() if float(row.get("expires", 0)) > time.time()),
        "download_dir": str(self.download_dir),
        "profile_dir": str(self.profile_dir),
        "login_storage": getattr(self, "login_storage", "ephemeral"),
        "restart_safe_control_session": True,
    }


def _start(self: ServerTikTokConnect) -> dict:
    now = time.time()
    with self.lock:
        if now - self.last_start < 1.5:
            raise HTTPException(429, "Connect TikTok was just started. Wait a moment and try again.")
        self.last_start = now

    try:
        oauth = self.ai.oauth_start(True)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc

    self._write_profile_prefs()
    url = str(oauth["url"])
    browser_running = bool(self.browser and self.browser.poll() is None)

    # Keep the existing Firefox process/profile alive. Repeatedly killing it was
    # one reason TikTok's login state behaved badly.
    if browser_running:
        if not _navigate_running_firefox(self, url):
            try:
                self.browser.terminate()
                self.browser.wait(timeout=8)
            except Exception:
                try:
                    self.browser.kill()
                except Exception:
                    pass
            browser_running = False

    if not browser_running:
        try:
            self.browser = subprocess.Popen(
                [
                    self._firefox(),
                    "--no-remote",
                    "--profile",
                    str(self.profile_dir),
                    "--new-window",
                    url,
                ],
                env=self._env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise HTTPException(500, f"Could not open TikTok on the Ripo server computer: {exc}") from exc

    desktop_token, desktop_expires = _new_session()
    flow_id = secrets.token_urlsafe(22)
    with self.lock:
        self.flows[flow_id] = {
            "expires": time.time() + 900,
            "baseline_sessions": set(self.ai.oauth_sessions.keys()),
            "desktop_token": desktop_token,
        }
        for key, row in list(self.flows.items()):
            if float(row.get("expires", 0)) < time.time():
                self.flows.pop(key, None)

    viewer_path = f"/tiktok/server-connect?flow={quote(flow_id)}&token={quote(desktop_token)}"
    return {
        "ok": True,
        "flow_id": flow_id,
        "desktop_token": desktop_token,
        "desktop_expires": desktop_expires,
        "viewer_path": viewer_path,
        "login_storage": getattr(self, "login_storage", "ephemeral"),
        "message": "TikTok opened inside the existing Ripo server Firefox session.",
    }


ServerTikTokConnect.__init__ = _init
ServerTikTokConnect._write_profile_prefs = _write_profile_prefs
ServerTikTokConnect.status = _status
ServerTikTokConnect.start = _start

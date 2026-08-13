from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Query
from fastapi.responses import JSONResponse

from desktop_http import _new_session


class ServerTikTokConnect:
    """Run TikTok Login Kit inside the Space's own Firefox session.

    The user still performs TikTok's own login/consent UI, but doing it inside
    the server browser means TikTok's website cookies stay on the server while
    the normal Login Kit callback creates the Ripo OAuth control session.
    """

    def __init__(self, ai: Any, data_dir: Path, display: str) -> None:
        self.ai = ai
        self.data_dir = data_dir
        self.display = display
        self.profile_dir = data_dir / "tiktok-firefox-profile"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.flows: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.browser: subprocess.Popen[Any] | None = None
        self.last_start = 0.0

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["DISPLAY"] = self.display
        env.setdefault("HOME", str(Path.home()))
        env.setdefault("USER", Path.home().name)
        runtime_dir = Path(f"/tmp/ripo-runtime-{os.getuid()}")
        runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        env.setdefault("XDG_RUNTIME_DIR", str(runtime_dir))
        env.setdefault("MOZ_DISABLE_CONTENT_SANDBOX", "1")
        return env

    def _firefox(self) -> str:
        executable = shutil.which("firefox-esr") or shutil.which("firefox")
        if not executable:
            raise HTTPException(503, "Firefox is not installed on the server computer.")
        return executable

    def start(self) -> dict[str, Any]:
        now = time.time()
        with self.lock:
            if now - self.last_start < 3:
                raise HTTPException(429, "Connect TikTok was just started. Wait a moment and try again.")
            self.last_start = now

        try:
            oauth = self.ai.oauth_start(True)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

        # Restart only our dedicated TikTok browser instance. Its Firefox
        # profile is kept on disk so TikTok website cookies can survive an
        # ordinary browser restart within the Space runtime.
        if self.browser and self.browser.poll() is None:
            try:
                self.browser.terminate()
                self.browser.wait(timeout=4)
            except Exception:
                try:
                    self.browser.kill()
                except Exception:
                    pass

        try:
            self.browser = subprocess.Popen(
                [
                    self._firefox(),
                    "--no-remote",
                    "--profile",
                    str(self.profile_dir),
                    "--new-window",
                    str(oauth["url"]),
                ],
                env=self._env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise HTTPException(500, f"Could not open TikTok on the server computer: {exc}") from exc

        desktop_token, desktop_expires = _new_session()
        flow_id = secrets.token_urlsafe(22)
        with self.lock:
            self.flows[flow_id] = {
                "expires": time.time() + 600,
                "baseline_sessions": set(self.ai.oauth_sessions.keys()),
                "desktop_token": desktop_token,
            }
            for key, row in list(self.flows.items()):
                if float(row.get("expires", 0)) < time.time():
                    self.flows.pop(key, None)

        return {
            "ok": True,
            "flow_id": flow_id,
            "desktop_token": desktop_token,
            "desktop_expires": desktop_expires,
            "message": "TikTok Login Kit opened inside the server computer.",
        }

    def poll(self, flow_id: str) -> dict[str, Any]:
        with self.lock:
            flow = self.flows.get(flow_id)
            if not flow or float(flow.get("expires", 0)) < time.time():
                self.flows.pop(flow_id, None)
                raise HTTPException(410, "Server TikTok connection expired. Press Connect TikTok again.")
            baseline = set(flow.get("baseline_sessions") or set())

        current = list(self.ai.oauth_sessions.keys())
        new_tokens = [token for token in current if token not in baseline and self.ai.session_valid(token)]
        if not new_tokens:
            return {
                "ok": True,
                "connected": False,
                "message": "Waiting for TikTok login/consent on the server computer.",
            }

        session_token = new_tokens[-1]
        with self.lock:
            self.flows.pop(flow_id, None)
        return {
            "ok": True,
            "connected": True,
            "session_token": session_token,
            "oauth_account": self.ai.status().get("oauth_account", {}),
            "unique_id": self.ai.settings.get("unique_id", ""),
            "message": "TikTok is connected to the Ripo server computer.",
        }


def install_server_tiktok_connect_routes(app: Any, connector: ServerTikTokConnect) -> None:
    @app.post("/api/tiktok/server-connect/start")
    async def server_connect_start() -> JSONResponse:
        return JSONResponse(connector.start())

    @app.get("/api/tiktok/server-connect/poll")
    async def server_connect_poll(flow_id: str = Query(min_length=10, max_length=120)) -> JSONResponse:
        return JSONResponse(connector.poll(flow_id))

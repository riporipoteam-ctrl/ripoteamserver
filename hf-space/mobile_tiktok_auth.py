from __future__ import annotations

import html
import secrets
import time
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse


class MobileTikTokAuth:
    """Web OAuth handoff so TikTok login happens directly in the user's phone browser."""

    def __init__(self, ai: Any, public_origin: str) -> None:
        self.ai = ai
        self.public_origin = public_origin.rstrip("/")
        self.handoffs: dict[str, dict[str, Any]] = {}

    def _cleanup(self) -> None:
        now = time.time()
        for key, row in list(self.handoffs.items()):
            if float(row.get("expires", 0)) < now:
                self.handoffs.pop(key, None)

    def start(self) -> RedirectResponse:
        self._cleanup()
        before = set(self.ai.oauth_states)
        oauth = self.ai.oauth_start(False)
        new_states = [key for key in self.ai.oauth_states if key not in before]
        if not new_states:
            raise HTTPException(500, "Could not create a TikTok authorization state.")
        state = new_states[-1]
        handoff = secrets.token_urlsafe(24)
        self.handoffs[handoff] = {"state": state, "expires": time.time() + 600, "used": False}
        return RedirectResponse(oauth["url"], status_code=302)

    async def callback(self, code: str, state: str, error: str = "", error_description: str = "") -> HTMLResponse | RedirectResponse:
        self._cleanup()
        if error:
            message = html.escape(error_description or error)
            return HTMLResponse(self._page(False, f"TikTok authorization failed: {message}"), status_code=400)
        try:
            session_token = str(self.ai.oauth_states.get(state, {}).get("session_token") or "")
            if not session_token:
                raise ValueError("This TikTok authorization request has expired. Start again.")
            result = await self.ai.oauth_callback(code, state)
            token = session_token or str(result.get("session_token") or "")
            if not token:
                raise ValueError("TikTok authorization succeeded but no Ripo session was created.")
            handoff = secrets.token_urlsafe(24)
            self.handoffs[handoff] = {
                "session_token": token,
                "oauth_account": result.get("oauth_account") or self.ai.status().get("oauth_account", {}),
                "expires": time.time() + 120,
                "used": False,
            }
            target = f"{self.public_origin}/tiktok-prerecorded.html#tiktok_handoff={quote(handoff)}"
            return RedirectResponse(target, status_code=302)
        except Exception as exc:
            return HTMLResponse(self._page(False, str(exc)), status_code=400)

    def exchange(self, handoff: str) -> dict[str, Any]:
        self._cleanup()
        row = self.handoffs.get(str(handoff or ""))
        if not row or row.get("used") or float(row.get("expires", 0)) < time.time():
            raise HTTPException(410, "TikTok handoff is invalid or expired. Connect again.")
        row["used"] = True
        return {
            "ok": True,
            "session_token": row.get("session_token"),
            "oauth_account": row.get("oauth_account") or {},
        }

    @staticmethod
    def _page(ok: bool, message: str) -> str:
        title = "TikTok connected ✅" if ok else "TikTok connection failed"
        return f"<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>{title}</title><style>body{{font-family:system-ui;background:#070912;color:white;display:grid;place-items:center;min-height:100vh;margin:0;padding:24px;text-align:center}}main{{max-width:520px}}p{{color:#aab3cc;line-height:1.6}}</style></head><body><main><h1>{title}</h1><p>{html.escape(message)}</p></main></body></html>"


def install_mobile_tiktok_auth(app: Any, auth: MobileTikTokAuth) -> None:
    @app.get("/api/tiktok/mobile/start")
    async def mobile_start() -> RedirectResponse:
        return auth.start()

    @app.get("/api/tiktok/oauth/callback", response_class=HTMLResponse)
    async def mobile_callback(
        code: str = Query(default=""),
        state: str = Query(default=""),
        error: str = Query(default=""),
        error_description: str = Query(default=""),
    ) -> HTMLResponse | RedirectResponse:
        return await auth.callback(code, state, error, error_description)

    @app.get("/api/tiktok/mobile/exchange")
    async def mobile_exchange(handoff: str = Query(min_length=10, max_length=120)) -> JSONResponse:
        return JSONResponse(auth.exchange(handoff))

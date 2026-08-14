from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import Header, HTTPException
from fastapi.responses import JSONResponse
from websockets.sync.client import connect as ws_connect

import live_studio_wine_launch_fix as launch_fix

CDP_HOST = "127.0.0.1"
CDP_PORT = 9223
CDP_BASE = f"http://{CDP_HOST}:{CDP_PORT}"
_DEBUG_FLAGS = [
    f"--remote-debugging-port={CDP_PORT}",
    f"--remote-debugging-address={CDP_HOST}",
]

# The launch worker resolves this function by module global at runtime, so a
# small wrapper is enough to add a localhost-only debugging socket to every
# launch mode without exposing it on the Space network interface.
_ORIGINAL_LAUNCH_ONE = launch_fix._launch_one


def _launch_one_with_cdp(self: Any, wine: str, exe: Path, flags: list[str], label: str) -> bool:
    merged: list[str] = []
    for item in [*_DEBUG_FLAGS, *flags]:
        if item not in merged:
            merged.append(item)
    return _ORIGINAL_LAUNCH_ONE(self, wine, exe, merged, label)


launch_fix._launch_one = _launch_one_with_cdp


def _http_json(path: str, timeout: float = 3.0) -> Any:
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        response = client.get(CDP_BASE + path)
        response.raise_for_status()
        return response.json()


def _targets() -> list[dict[str, Any]]:
    try:
        rows = _http_json("/json/list", 3.0)
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ws = str(row.get("webSocketDebuggerUrl") or "")
        if not ws.startswith("ws://127.0.0.1:") and not ws.startswith("ws://localhost:"):
            continue
        result.append(row)
    return result


def _call(ws_url: str, method: str, params: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    request_id = int(time.time() * 1000000) % 2_000_000_000
    with ws_connect(ws_url, open_timeout=timeout, close_timeout=1) as websocket:
        websocket.send(json.dumps({"id": request_id, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = websocket.recv(timeout=max(0.2, deadline - time.time()))
            message = json.loads(raw)
            if message.get("id") != request_id:
                continue
            if message.get("error"):
                raise RuntimeError(str(message["error"])[:800])
            return message.get("result") or {}
    raise TimeoutError(f"CDP call {method} timed out")


def _evaluate(target: dict[str, Any], expression: str, timeout: float = 5.0) -> Any:
    result = _call(
        str(target["webSocketDebuggerUrl"]),
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True, "awaitPromise": True},
        timeout,
    )
    remote = result.get("result") or {}
    return remote.get("value")


def _safe_target_summary(row: dict[str, Any]) -> dict[str, str]:
    raw_url = str(row.get("url") or "")
    # Never expose query strings/fragments from an authenticated target.
    clean_url = raw_url.split("?", 1)[0].split("#", 1)[0][:240]
    return {
        "type": str(row.get("type") or "")[:40],
        "title": str(row.get("title") or "")[:160],
        "url": clean_url,
    }


def _firefox_tiktok_cookies(profile: Path) -> list[dict[str, Any]]:
    db = profile / "cookies.sqlite"
    if not db.exists():
        return []
    rows: list[dict[str, Any]] = []
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        query = """
            SELECT name, value, host, path, expiry, isSecure, isHttpOnly
            FROM moz_cookies
            WHERE lower(host) LIKE '%tiktok.com'
               OR lower(host) LIKE '%tiktokv.com'
               OR lower(host) LIKE '%tiktokcdn.com'
        """
        for name, value, host, path, expiry, secure, httponly in connection.execute(query):
            name = str(name or "")
            value = str(value or "")
            host = str(host or "")
            if not name or not value or not host:
                continue
            cookie: dict[str, Any] = {
                "name": name,
                "value": value,
                "domain": host,
                "path": str(path or "/"),
                "secure": bool(secure),
                "httpOnly": bool(httponly),
            }
            try:
                expires = float(expiry or 0)
                if expires > time.time():
                    cookie["expires"] = expires
            except Exception:
                pass
            rows.append(cookie)
    except Exception:
        return []
    finally:
        if connection is not None:
            connection.close()
    # A sanity cap prevents accidental migration of an unrelated giant store.
    return rows[:300]


_ACTION_JS = r"""
(() => {
  const out=[]; const seen=new Set();
  function walk(root){
    if(!root||seen.has(root)) return; seen.add(root);
    let nodes=[]; try{nodes=[...root.querySelectorAll('*')]}catch(e){}
    for(const el of nodes){
      const sr=el.shadowRoot; if(sr) walk(sr);
      const tag=(el.tagName||'').toLowerCase();
      const role=(el.getAttribute?.('role')||'').toLowerCase();
      if(tag==='button'||tag==='a'||role==='button'||role==='link'){
        const text=((el.innerText||el.textContent||el.getAttribute?.('aria-label')||'')+'').replace(/\s+/g,' ').trim();
        if(text && /go\s*live|start\s*(live|stream)|sign\s*in|log\s*in|confirm|next|continue/i.test(text)) out.push(text.slice(0,120));
      }
    }
  }
  walk(document);
  return [...new Set(out)].slice(0,80);
})()
"""


def _action_labels() -> list[str]:
    labels: list[str] = []
    for target in _targets():
        if str(target.get("type") or "") not in {"page", "webview", "iframe", "other"}:
            continue
        try:
            value = _evaluate(target, _ACTION_JS)
            if isinstance(value, list):
                for item in value:
                    text = re.sub(r"\s+", " ", str(item)).strip()[:120]
                    if text and text not in labels:
                        labels.append(text)
        except Exception:
            pass
    return labels[:80]


def _click_matching(pattern: str) -> list[str]:
    # Pattern is generated by this module, never user supplied.
    expression = r"""
(() => {
  const rx=new RegExp(%s,'i'); const seen=new Set(); const candidates=[];
  function walk(root){
    if(!root||seen.has(root)) return; seen.add(root);
    let nodes=[]; try{nodes=[...root.querySelectorAll('*')]}catch(e){}
    for(const el of nodes){
      if(el.shadowRoot) walk(el.shadowRoot);
      const tag=(el.tagName||'').toLowerCase(); const role=(el.getAttribute?.('role')||'').toLowerCase();
      if(tag==='button'||tag==='a'||role==='button'||role==='link'){
        const text=((el.innerText||el.textContent||el.getAttribute?.('aria-label')||'')+'').replace(/\s+/g,' ').trim();
        if(text && rx.test(text)) candidates.push({el,text});
      }
    }
  }
  walk(document);
  if(!candidates.length) return '';
  candidates.sort((a,b)=>a.text.length-b.text.length);
  const hit=candidates[0]; hit.el.scrollIntoView?.({block:'center'}); hit.el.click();
  return hit.text.slice(0,120);
})()
""" % json.dumps(pattern)
    clicked: list[str] = []
    for target in _targets():
        try:
            value = _evaluate(target, expression)
            text = re.sub(r"\s+", " ", str(value or "")).strip()[:120]
            if text:
                clicked.append(text)
        except Exception:
            pass
    return clicked


class LiveStudioCDP:
    def __init__(self, ai: Any, connector: Any, wine_runner: Any) -> None:
        self.ai = ai
        self.connector = connector
        self.wine_runner = wine_runner
        self.lock = threading.RLock()
        self.last_sync_at: float | None = None
        self.last_cookie_count = 0
        self.last_actions: list[str] = []
        self.last_error = ""

    def status(self) -> dict[str, Any]:
        targets = _targets()
        labels = _action_labels() if targets else []
        return {
            "ok": True,
            "cdp_ready": bool(targets),
            "target_count": len(targets),
            "targets": [_safe_target_summary(row) for row in targets[:12]],
            "actions": labels,
            "session_cookie_sync": self.last_cookie_count,
            "last_sync_at": self.last_sync_at,
            "last_actions": self.last_actions[-8:],
            "last_error": self.last_error,
        }

    def sync_session(self) -> dict[str, Any]:
        with self.lock:
            targets = _targets()
            if not targets:
                raise RuntimeError("LIVE Studio DevTools bridge is not ready yet.")
            cookies = _firefox_tiktok_cookies(self.connector.profile_dir)
            if not cookies:
                raise RuntimeError("The saved server Firefox profile has no TikTok website cookies to reuse. Connect TikTok once first.")
            synced = False
            errors: list[str] = []
            for target in targets:
                ws = str(target.get("webSocketDebuggerUrl") or "")
                if not ws:
                    continue
                try:
                    _call(ws, "Network.enable", {}, 4)
                    _call(ws, "Network.setCookies", {"cookies": cookies}, 8)
                    synced = True
                except Exception as exc:
                    errors.append(str(exc)[:240])
            if not synced:
                raise RuntimeError("LIVE Studio would not accept the saved TikTok browser session. " + " | ".join(errors[:3]))
            # Reload page/webview targets so they re-check the imported session.
            for target in targets:
                try:
                    _call(str(target["webSocketDebuggerUrl"]), "Page.reload", {"ignoreCache": True}, 4)
                except Exception:
                    pass
            self.last_cookie_count = len(cookies)
            self.last_sync_at = time.time()
            self.last_error = ""
            return {"ok": True, "synced": len(cookies), "message": "Saved TikTok server session copied into LIVE Studio locally."}

    def go_live(self) -> dict[str, Any]:
        with self.lock:
            state = self.wine_runner.status()
            if not state.get("live_studio_running"):
                self.wine_runner.try_start()
                deadline = time.time() + 150
                while time.time() < deadline:
                    time.sleep(2)
                    state = self.wine_runner.status()
                    if state.get("live_studio_running"):
                        break
                    if state.get("phase") == "wine-failed":
                        raise RuntimeError(str(state.get("last_error") or "LIVE Studio failed to start."))
                if not state.get("live_studio_running"):
                    raise RuntimeError("LIVE Studio did not become ready in time.")

            cdp_deadline = time.time() + 35
            while time.time() < cdp_deadline and not _targets():
                time.sleep(1)
            if not _targets():
                raise RuntimeError("LIVE Studio is open, but its localhost UI bridge did not start.")

            try:
                self.sync_session()
                time.sleep(6)
            except Exception as exc:
                # Keep going: LIVE Studio may already own a valid app session.
                self.last_error = str(exc)[:700]

            clicked: list[str] = []
            # First select the explicit Go LIVE/Start LIVE control.
            for pattern in (r"^\s*go\s*live\s*$", r"^\s*start\s*live\s*$", r"^\s*start\s*stream(ing)?\s*$"):
                hits = _click_matching(pattern)
                if hits:
                    clicked.extend(hits)
                    break
            if not clicked:
                labels = _action_labels()
                raise RuntimeError("LIVE Studio is running but no Go LIVE button is exposed yet. Visible actions: " + ", ".join(labels[:12]))

            # Some versions show one confirmation dialog after the first click.
            time.sleep(3)
            for pattern in (r"^\s*confirm\s*$", r"^\s*go\s*live\s*$", r"^\s*start\s*live\s*$"):
                hits = _click_matching(pattern)
                if hits:
                    clicked.extend(hits)
                    time.sleep(2)
                    break

            self.last_actions.extend(clicked)
            self.last_error = ""
            # Start the AI event host immediately; its resilience loop waits until
            # TikTok reports that the account is actually LIVE.
            try:
                self.ai.start()
            except Exception:
                pass
            return {
                "ok": True,
                "clicked": clicked,
                "message": "LIVE Studio Go LIVE action was sent; Ripo Bot AI host is starting.",
            }


def install_live_studio_cdp_routes(app: Any, bridge: LiveStudioCDP, authorize: Any) -> None:
    @app.get("/api/tiktok/live-studio-linux/ui-status")
    async def live_studio_ui_status(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        authorize(x_admin_token)
        return JSONResponse(bridge.status())

    @app.post("/api/tiktok/live-studio-linux/sync-session")
    async def live_studio_sync_session(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        authorize(x_admin_token)
        try:
            return JSONResponse(bridge.sync_session())
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/tiktok/live-studio-linux/go-live")
    async def live_studio_go_live(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        authorize(x_admin_token)
        try:
            return JSONResponse(bridge.go_live())
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

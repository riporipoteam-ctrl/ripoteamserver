from __future__ import annotations

import html
import os
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from desktop_http import _new_session


class ServerTikTokConnect:
    """Run TikTok Login Kit inside the Space's own persistent Firefox profile."""

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
            raise HTTPException(503, "Firefox is not installed on the Ripo server computer.")
        return executable

    def status(self) -> dict[str, Any]:
        browser_running = bool(self.browser and self.browser.poll() is None)
        return {
            "ok": True,
            "firefox_installed": bool(shutil.which("firefox-esr") or shutil.which("firefox")),
            "browser_running": browser_running,
            "display": self.display,
            "oauth_configured": bool(self.ai.client_key and self.ai.client_secret and self.ai.redirect_uri),
            "account": self.ai.status().get("oauth_account", {}),
            "active_flows": sum(1 for row in self.flows.values() if float(row.get("expires", 0)) > time.time()),
        }

    def start(self) -> dict[str, Any]:
        now = time.time()
        with self.lock:
            if now - self.last_start < 2:
                raise HTTPException(429, "Connect TikTok was just started. Wait a moment and try again.")
            self.last_start = now

        try:
            oauth = self.ai.oauth_start(True)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

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
            raise HTTPException(500, f"Could not open TikTok on the Ripo server computer: {exc}") from exc

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

        viewer_path = f"/tiktok/server-connect?flow={quote(flow_id)}&token={quote(desktop_token)}"
        return {
            "ok": True,
            "flow_id": flow_id,
            "desktop_token": desktop_token,
            "desktop_expires": desktop_expires,
            "viewer_path": viewer_path,
            "message": "TikTok opened inside Firefox on the Ripo server computer.",
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
            return {"ok": True, "connected": False, "message": "Waiting for TikTok login/consent on the Ripo server computer."}

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
    @app.get("/api/tiktok/server-connect/status")
    async def server_connect_status() -> JSONResponse:
        return JSONResponse(connector.status())

    @app.post("/api/tiktok/server-connect/start")
    async def server_connect_start() -> JSONResponse:
        return JSONResponse(connector.start())

    @app.get("/api/tiktok/server-connect/poll")
    async def server_connect_poll(flow_id: str = Query(min_length=10, max_length=120)) -> JSONResponse:
        return JSONResponse(connector.poll(flow_id))

    @app.get("/tiktok/server-connect", response_class=HTMLResponse)
    async def server_connect_page(flow: str = "", token: str = "") -> str:
        flow_safe = html.escape(flow, quote=True)
        token_safe = html.escape(token, quote=True)
        return f'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no"><title>Connect TikTok to Ripo Server</title>
<style>*{{box-sizing:border-box}}body{{margin:0;background:#080910;color:#fff;font-family:system-ui}}header{{height:52px;display:flex;align-items:center;justify-content:space-between;padding:0 12px;border-bottom:1px solid #ffffff1a}}#status{{font-size:12px;color:#b8bfd3}}main{{height:calc(100vh - 118px);display:grid;place-items:center;background:#151515}}#screen{{display:block;max-width:100%;max-height:100%;touch-action:none;user-select:none;-webkit-user-drag:none}}footer{{height:66px;padding:8px;background:#0d0f18;border-top:1px solid #ffffff1a}}.row{{display:flex;gap:6px}}input{{flex:1;min-width:0;background:#171a27;color:#fff;border:1px solid #ffffff1a;border-radius:9px;padding:9px}}button{{background:#25f4ee;color:#04110f;border:0;border-radius:9px;padding:9px 11px;font-weight:800}}.dark{{background:#232634;color:#fff}}#done{{display:none;position:fixed;inset:0;place-items:center;text-align:center;background:#080910f5;padding:24px}}#done.show{{display:grid}}</style></head>
<body><header><strong>Ripo Server · TikTok</strong><span id="status">Starting server Firefox…</span></header><main><img id="screen" alt="TikTok on Ripo server"></main><footer><div class="row"><input id="text" placeholder="Type into TikTok"><button id="send">Send</button><button id="back" class="dark">⌫</button><button id="enter" class="dark">Enter</button></div></footer><section id="done"><div><h1>Connected ✅</h1><p>TikTok is connected to the Ripo server computer.</p></div></section>
<script>
const flow={flow_safe!r}; const token={token_safe!r}; const statusEl=document.querySelector('#status'),screen=document.querySelector('#screen'),done=document.querySelector('#done'); let run=true,last='';
async function j(path,opt={{}}){{const r=await fetch(path,{{cache:'no-store',...opt,headers:{{'content-type':'application/json',...(opt.headers||{{}})}}}});const x=await r.json();if(!r.ok)throw Error(x.detail||x.message||`HTTP ${{r.status}}`);return x}}
async function frame(){{if(!run)return;try{{const r=await fetch(`/api/desktop/frame?token=${{encodeURIComponent(token)}}&width=1100&quality=72`,{{cache:'no-store'}});if(!r.ok)throw Error(`Screen ${{r.status}}`);const b=await r.blob(),u=URL.createObjectURL(b);screen.src=u;if(last)URL.revokeObjectURL(last);last=u;statusEl.textContent='TikTok is running on the Ripo server';}}catch(e){{statusEl.textContent=e.message}}finally{{if(run)setTimeout(frame,300)}}}}
function xy(ev){{const r=screen.getBoundingClientRect(),p=ev.changedTouches?.[0]||ev.touches?.[0]||ev;return{{x:Math.max(0,Math.min(1365,Math.round((p.clientX-r.left)*1366/r.width))),y:Math.max(0,Math.min(767,Math.round((p.clientY-r.top)*768/r.height)))}}}}
async function click(ev){{ev.preventDefault();const p=xy(ev);try{{await j(`/api/desktop/input?token=${{encodeURIComponent(token)}}`,{{method:'POST',body:JSON.stringify({{action:'click',x:p.x,y:p.y,button:1}})}})}}catch(e){{statusEl.textContent=e.message}}}}
screen.addEventListener('click',click);screen.addEventListener('touchend',click,{{passive:false}});
async function key(k){{try{{await j(`/api/desktop/input?token=${{encodeURIComponent(token)}}`,{{method:'POST',body:JSON.stringify({{action:'key',key:k,modifiers:[]}})}})}}catch(e){{statusEl.textContent=e.message}}}}
document.querySelector('#send').onclick=async()=>{{const e=document.querySelector('#text');if(!e.value)return;try{{await j(`/api/desktop/input?token=${{encodeURIComponent(token)}}`,{{method:'POST',body:JSON.stringify({{action:'text',text:e.value}})}});e.value=''}}catch(x){{statusEl.textContent=x.message}}}};document.querySelector('#back').onclick=()=>key('Backspace');document.querySelector('#enter').onclick=()=>key('Enter');
async function poll(){{if(!run)return;try{{const x=await j(`/api/tiktok/server-connect/poll?flow_id=${{encodeURIComponent(flow)}}`);if(x.connected&&x.session_token){{run=false;done.classList.add('show');window.opener?.postMessage({{type:'ripo-server-tiktok-connected',session_token:x.session_token,oauth_account:x.oauth_account||{{}},unique_id:x.unique_id||''}},'https://riporipoteam-ctrl.github.io');setTimeout(()=>window.close(),1300);return}}statusEl.textContent=x.message||'Waiting for TikTok…'}}catch(e){{statusEl.textContent=e.message}}setTimeout(poll,1200)}}
if(!flow||!token){{run=false;statusEl.textContent='Missing connection information.'}}else{{frame();poll()}}
</script></body></html>'''

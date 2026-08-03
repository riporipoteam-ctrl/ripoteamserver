from __future__ import annotations

import asyncio
import io
import os
import secrets
import shutil
import subprocess
import threading
import time
from typing import Any, Callable

import mss
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from PIL import Image

SESSION_TTL_SECONDS = 12 * 60 * 60
_SESSIONS: dict[str, float] = {}
_SESSION_LOCK = threading.Lock()
_CAPTURE_LOCK = threading.Lock()


def _new_session() -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + SESSION_TTL_SECONDS
    with _SESSION_LOCK:
        now = time.time()
        expired = [key for key, expiry in _SESSIONS.items() if expiry <= now]
        for key in expired:
            _SESSIONS.pop(key, None)
        _SESSIONS[token] = expires_at
    return token, SESSION_TTL_SECONDS


def _require_session(token: str) -> None:
    with _SESSION_LOCK:
        expiry = _SESSIONS.get(token)
        if not expiry or expiry <= time.time():
            _SESSIONS.pop(token, None)
            raise HTTPException(status_code=401, detail="Desktop session expired.")


def _run_xdotool(arguments: list[str], display: str) -> None:
    if shutil.which("xdotool") is None:
        raise HTTPException(status_code=503, detail="xdotool is not installed.")
    env = os.environ.copy()
    env["DISPLAY"] = display
    try:
        subprocess.run(
            ["xdotool", *arguments],
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Desktop input failed: {exc}") from exc


def _capture_jpeg(display: str, requested_width: int) -> bytes:
    old_display = os.environ.get("DISPLAY")
    os.environ["DISPLAY"] = display
    try:
        with _CAPTURE_LOCK, mss.mss() as screenshotter:
            monitor = screenshotter.monitors[1] if len(screenshotter.monitors) > 1 else screenshotter.monitors[0]
            shot = screenshotter.grab(monitor)
            image = Image.frombytes("RGB", shot.size, shot.rgb)
            width = max(480, min(int(requested_width), 1366))
            if image.width > width:
                height = max(1, round(image.height * width / image.width))
                image = image.resize((width, height), Image.Resampling.BILINEAR)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=72, optimize=False)
            return output.getvalue()
    finally:
        if old_display is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = old_display


def _special_key(name: str) -> str:
    mapping = {
        "Enter": "Return",
        "Backspace": "BackSpace",
        "Tab": "Tab",
        "Escape": "Escape",
        "Delete": "Delete",
        "Insert": "Insert",
        "ArrowUp": "Up",
        "ArrowDown": "Down",
        "ArrowLeft": "Left",
        "ArrowRight": "Right",
        "Home": "Home",
        "End": "End",
        "PageUp": "Page_Up",
        "PageDown": "Page_Down",
        " ": "space",
    }
    return mapping.get(name, name)


def install_desktop_routes(app: FastAPI, *, password: str, display: str) -> None:
    @app.get("/desktop", response_class=HTMLResponse)
    async def desktop_page() -> str:
        return DESKTOP_HTML

    @app.post("/api/desktop/login")
    async def desktop_login(payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
        submitted = str(payload.get("password", ""))
        if not secrets.compare_digest(submitted, password):
            await asyncio.sleep(0.35)
            raise HTTPException(status_code=401, detail="Incorrect desktop password.")
        token, expires_in = _new_session()
        return JSONResponse({"ok": True, "token": token, "expires_in": expires_in})

    @app.get("/api/desktop/frame")
    async def desktop_frame(
        token: str = Query(min_length=16),
        width: int = Query(default=1000, ge=480, le=1366),
    ) -> Response:
        _require_session(token)
        try:
            content = await asyncio.to_thread(_capture_jpeg, display, width)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Screen capture unavailable: {exc}") from exc
        return Response(
            content,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/api/desktop/input")
    async def desktop_input(
        payload: dict[str, Any] = Body(default_factory=dict),
        token: str = Query(min_length=16),
    ) -> JSONResponse:
        _require_session(token)
        action = str(payload.get("action", ""))

        if action in {"move", "down", "up", "click", "doubleclick"}:
            x = max(0, min(1365, int(payload.get("x", 0))))
            y = max(0, min(767, int(payload.get("y", 0))))
            button = max(1, min(3, int(payload.get("button", 1))))
            await asyncio.to_thread(_run_xdotool, ["mousemove", "--sync", str(x), str(y)], display)
            if action == "down":
                await asyncio.to_thread(_run_xdotool, ["mousedown", str(button)], display)
            elif action == "up":
                await asyncio.to_thread(_run_xdotool, ["mouseup", str(button)], display)
            elif action == "click":
                await asyncio.to_thread(_run_xdotool, ["click", str(button)], display)
            elif action == "doubleclick":
                await asyncio.to_thread(_run_xdotool, ["click", "--repeat", "2", "--delay", "110", str(button)], display)

        elif action == "scroll":
            direction = int(payload.get("direction", 1))
            amount = max(1, min(8, abs(int(payload.get("amount", 2)))))
            button = "4" if direction < 0 else "5"
            await asyncio.to_thread(_run_xdotool, ["click", "--repeat", str(amount), button], display)

        elif action == "text":
            text = str(payload.get("text", ""))[:1000]
            if text:
                await asyncio.to_thread(
                    _run_xdotool,
                    ["type", "--clearmodifiers", "--delay", "1", "--", text],
                    display,
                )

        elif action == "key":
            key = _special_key(str(payload.get("key", "")))
            allowed_modifiers = {
                "Control": "ctrl",
                "Alt": "alt",
                "Shift": "shift",
                "Meta": "super",
            }
            modifiers = [
                allowed_modifiers[item]
                for item in payload.get("modifiers", [])
                if item in allowed_modifiers
            ]
            chord = "+".join([*modifiers, key]) if modifiers else key
            if not chord or len(chord) > 80:
                raise HTTPException(status_code=400, detail="Invalid key input.")
            await asyncio.to_thread(_run_xdotool, ["key", "--clearmodifiers", chord], display)

        else:
            raise HTTPException(status_code=400, detail="Unknown desktop input action.")

        return JSONResponse({"ok": True})

    @app.get("/api/desktop/capabilities")
    async def desktop_capabilities() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "transport": "https-frame-polling",
                "display": display,
                "screen": {"width": 1366, "height": 768},
                "xdotool": shutil.which("xdotool") is not None,
            }
        )


DESKTOP_HTML = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
  <meta name="theme-color" content="#080b14">
  <title>Ripo Team Linux Desktop</title>
  <style>
    :root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#080b14;color:#f6f7ff}
    *{box-sizing:border-box}html,body{width:100%;height:100%;margin:0;overflow:hidden;background:#080b14}
    button,input{font:inherit}.shell{position:fixed;inset:0;display:grid;grid-template-rows:48px 1fr;background:#080b14}
    .bar{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:0 12px;border-bottom:1px solid #ffffff18;background:#0d1222}
    .brand,.controls{display:flex;align-items:center;gap:9px}.brand strong{font-size:.92rem}.status{font-size:.78rem;color:#aeb8db}.dot{width:9px;height:9px;border-radius:50%;background:#ffd166;box-shadow:0 0 14px currentColor}.dot.online{color:#49e5a2;background:currentColor}.dot.bad{color:#ff6578;background:currentColor}
    .controls button{width:34px;height:32px;border:1px solid #ffffff18;border-radius:10px;color:#e8ebff;background:#ffffff0b;cursor:pointer}
    .stage{position:relative;display:grid;place-items:center;min-height:0;background:#171717;touch-action:none;user-select:none;-webkit-user-select:none}
    #screen{display:block;max-width:100%;max-height:100%;width:auto;height:auto;background:#111;object-fit:contain;touch-action:none;-webkit-user-drag:none}
    .login,.notice{position:absolute;z-index:10;width:min(430px,calc(100% - 28px));padding:24px;border:1px solid #ffffff1a;border-radius:22px;background:#0d1222ee;box-shadow:0 24px 80px #0009;backdrop-filter:blur(20px)}
    .login h1{margin:0 0 8px;font-size:1.7rem}.login p,.notice p{color:#aeb8db;line-height:1.55}.login input{width:100%;margin:10px 0 12px;padding:13px 14px;border:1px solid #ffffff20;border-radius:12px;color:#fff;background:#ffffff0a;outline:none}.login button,.notice button{width:100%;border:0;border-radius:12px;padding:13px 16px;color:#071020;background:#f5f7ff;font-weight:850;cursor:pointer}.error{min-height:20px;color:#ff8b99;font-size:.85rem}.hidden{display:none!important}
    .keyboard{position:absolute;z-index:8;left:50%;bottom:max(12px,env(safe-area-inset-bottom));transform:translateX(-50%);display:flex;gap:8px;width:min(760px,calc(100% - 20px));padding:8px;border:1px solid #ffffff1a;border-radius:16px;background:#0d1222e8;backdrop-filter:blur(18px)}
    .keyboard input{min-width:0;flex:1;border:1px solid #ffffff1a;border-radius:10px;padding:11px 12px;color:#fff;background:#ffffff0a}.keyboard button{border:1px solid #ffffff1a;border-radius:10px;padding:0 13px;color:#fff;background:#ffffff0c;font-weight:750}.hint{position:absolute;left:10px;bottom:10px;padding:7px 10px;border-radius:10px;color:#c5ccef;background:#05070db8;font-size:.72rem;pointer-events:none}
    @media(max-width:650px){.bar{height:46px}.brand strong{font-size:.82rem}.status{display:none}.keyboard{bottom:max(7px,env(safe-area-inset-bottom))}.hint{display:none}}
  </style>
</head>
<body>
  <main class="shell">
    <header class="bar">
      <div class="brand"><span id="dot" class="dot"></span><strong>Ripo Team Linux</strong><span id="status" class="status">Waiting for login</span></div>
      <div class="controls"><button id="keyboardButton" title="Keyboard">⌨</button><button id="refreshButton" title="Reconnect">↻</button><button id="fullscreenButton" title="Fullscreen">⛶</button></div>
    </header>
    <section id="stage" class="stage">
      <img id="screen" alt="Linux desktop" draggable="false">
      <section id="login" class="login">
        <h1>Linux Desktop</h1>
        <p>Enter the VNC password you created in Hugging Face Secrets.</p>
        <input id="password" type="password" autocomplete="current-password" placeholder="VNC password">
        <button id="loginButton">Connect</button>
        <div id="loginError" class="error"></div>
      </section>
      <section id="notice" class="notice hidden"><h2>Connection paused</h2><p id="noticeText">The desktop session stopped.</p><button id="noticeButton">Reconnect</button></section>
      <div id="keyboard" class="keyboard hidden"><input id="textInput" autocomplete="off" autocapitalize="none" placeholder="Type into Linux…"><button data-key="Enter">Enter</button><button data-key="Backspace">⌫</button><button id="keyboardClose">×</button></div>
      <div class="hint">Tap, drag, scroll and type directly into Linux</div>
    </section>
  </main>
  <script>
    "use strict";
    const q=s=>document.querySelector(s);
    const el={stage:q("#stage"),screen:q("#screen"),login:q("#login"),password:q("#password"),loginButton:q("#loginButton"),loginError:q("#loginError"),dot:q("#dot"),status:q("#status"),notice:q("#notice"),noticeText:q("#noticeText"),noticeButton:q("#noticeButton"),keyboard:q("#keyboard"),textInput:q("#textInput")};
    let token=sessionStorage.getItem("ripo-desktop-token")||"",running=false,inFlight=false,lastObjectUrl="",pointerDown=false,pointerButton=1,lastMove=0;
    const api=(path,options={})=>fetch(path,{cache:"no-store",...options,headers:{"content-type":"application/json",...(options.headers||{})}});
    function state(kind,text){el.dot.className=`dot ${kind}`;el.status.textContent=text}
    async function login(){el.loginError.textContent="Connecting…";try{const r=await api("/api/desktop/login",{method:"POST",body:JSON.stringify({password:el.password.value})});const data=await r.json();if(!r.ok)throw new Error(data.detail||`HTTP ${r.status}`);token=data.token;sessionStorage.setItem("ripo-desktop-token",token);el.login.classList.add("hidden");el.notice.classList.add("hidden");el.loginError.textContent="";start()}catch(e){el.loginError.textContent=e.message;state("bad","Login failed")}}
    function coordinates(event){const rect=el.screen.getBoundingClientRect();return{x:Math.max(0,Math.min(1365,Math.round((event.clientX-rect.left)/rect.width*1366))),y:Math.max(0,Math.min(767,Math.round((event.clientY-rect.top)/rect.height*768)))}}
    async function input(payload){if(!token)return;try{const r=await api(`/api/desktop/input?token=${encodeURIComponent(token)}`,{method:"POST",body:JSON.stringify(payload)});if(r.status===401)expired()}catch{}}
    function expired(){running=false;token="";sessionStorage.removeItem("ripo-desktop-token");el.login.classList.remove("hidden");state("bad","Session expired")}
    async function frame(){if(!running||inFlight)return;inFlight=true;try{const width=Math.max(640,Math.min(1366,Math.round(el.stage.clientWidth*1.25)));const r=await fetch(`/api/desktop/frame?token=${encodeURIComponent(token)}&width=${width}&t=${Date.now()}`,{cache:"no-store"});if(r.status===401){expired();return}if(!r.ok)throw new Error(`HTTP ${r.status}`);const blob=await r.blob();const next=URL.createObjectURL(blob);el.screen.src=next;if(lastObjectUrl)URL.revokeObjectURL(lastObjectUrl);lastObjectUrl=next;state("online","Connected")}catch(e){state("bad","Reconnecting…")}finally{inFlight=false;if(running)setTimeout(frame,260)}}
    function start(){if(!token){el.login.classList.remove("hidden");return}running=true;state("","Loading desktop…");frame()}
    el.loginButton.onclick=login;el.password.addEventListener("keydown",e=>{if(e.key==="Enter")login()});q("#refreshButton").onclick=()=>{running=false;setTimeout(start,100)};q("#fullscreenButton").onclick=async()=>{if(!document.fullscreenElement)await document.documentElement.requestFullscreen();else await document.exitFullscreen()};q("#keyboardButton").onclick=()=>{el.keyboard.classList.remove("hidden");el.textInput.focus()};q("#keyboardClose").onclick=()=>el.keyboard.classList.add("hidden");el.noticeButton.onclick=()=>{el.notice.classList.add("hidden");start()};
    el.textInput.addEventListener("input",()=>{const text=el.textInput.value;if(text)input({action:"text",text});el.textInput.value=""});document.querySelectorAll("[data-key]").forEach(button=>button.onclick=()=>input({action:"key",key:button.dataset.key}));
    el.screen.addEventListener("pointerdown",e=>{e.preventDefault();el.screen.setPointerCapture(e.pointerId);pointerDown=true;pointerButton=e.button===2?3:1;const p=coordinates(e);input({action:"down",...p,button:pointerButton})});
    el.screen.addEventListener("pointermove",e=>{if(!pointerDown)return;const now=performance.now();if(now-lastMove<45)return;lastMove=now;const p=coordinates(e);input({action:"move",...p})});
    el.screen.addEventListener("pointerup",e=>{e.preventDefault();const p=coordinates(e);input({action:"up",...p,button:pointerButton});pointerDown=false});
    el.screen.addEventListener("pointercancel",()=>{pointerDown=false});el.screen.addEventListener("dblclick",e=>{const p=coordinates(e);input({action:"doubleclick",...p,button:1})});el.screen.addEventListener("contextmenu",e=>{e.preventDefault();const p=coordinates(e);input({action:"click",...p,button:3})});el.screen.addEventListener("wheel",e=>{e.preventDefault();input({action:"scroll",direction:e.deltaY<0?-1:1,amount:2})},{passive:false});
    document.addEventListener("keydown",e=>{if(document.activeElement===el.password||document.activeElement===el.textInput)return;if(["Enter","Backspace","Tab","Escape","Delete","ArrowUp","ArrowDown","ArrowLeft","ArrowRight","Home","End","PageUp","PageDown"].includes(e.key)){e.preventDefault();input({action:"key",key:e.key,modifiers:[e.ctrlKey?"Control":"",e.altKey?"Alt":"",e.shiftKey?"Shift":"",e.metaKey?"Meta":""].filter(Boolean)})}else if(e.key.length===1&&!e.ctrlKey&&!e.metaKey){e.preventDefault();input({action:"text",text:e.key})}});
    if(token){el.login.classList.add("hidden");start()}else el.password.focus();
  </script>
</body>
</html>'''

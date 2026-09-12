from __future__ import annotations

import asyncio
import io
import os
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import mss
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from PIL import Image

SESSION_TTL_SECONDS = 12 * 60 * 60
_SCREEN_WIDTH = 1366
_SCREEN_HEIGHT = 768
_SESSIONS: dict[str, float] = {}
_SESSION_LOCK = threading.Lock()
_CAPTURE_LOCK = threading.Lock()

APP_COMMANDS: dict[str, list[str]] = {
    "browser": ["firefox-esr", "--new-window", "https://www.google.com"],
    "files": ["pcmanfm"],
    "terminal": ["lxterminal"],
    "editor": ["mousepad"],
    "settings": ["lxappearance"],
}


def _desktop_env(display: str) -> dict[str, str]:
    env = os.environ.copy()
    env["DISPLAY"] = display
    env.setdefault("HOME", str(Path.home()))
    env.setdefault("USER", Path.home().name)
    runtime_dir = Path(f"/tmp/ripo-runtime-{os.getuid()}")
    runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    env.setdefault("XDG_RUNTIME_DIR", str(runtime_dir))
    env.setdefault("MOZ_DISABLE_CONTENT_SANDBOX", "1")
    return env


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


def _require_session(token: str, *, refresh: bool = True) -> None:
    with _SESSION_LOCK:
        expiry = _SESSIONS.get(token)
        if not expiry or expiry <= time.time():
            _SESSIONS.pop(token, None)
            raise HTTPException(status_code=401, detail="Desktop session expired.")
        if refresh:
            _SESSIONS[token] = time.time() + SESSION_TTL_SECONDS


def _run_xdotool(arguments: list[str], display: str) -> None:
    if shutil.which("xdotool") is None:
        raise HTTPException(status_code=503, detail="xdotool is not installed.")
    try:
        subprocess.run(
            ["xdotool", *arguments],
            env=_desktop_env(display),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Desktop input failed: {exc}") from exc


def _launch_app(name: str, display: str) -> None:
    command = APP_COMMANDS.get(name)
    if command is None:
        raise HTTPException(status_code=400, detail="Unknown desktop application.")
    executable = shutil.which(command[0])
    if executable is None:
        raise HTTPException(status_code=503, detail=f"{command[0]} is not installed.")
    try:
        subprocess.Popen(
            [executable, *command[1:]],
            env=_desktop_env(display),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not launch {name}: {exc}") from exc


def _capture_jpeg(display: str, requested_width: int, requested_quality: int) -> bytes:
    old_display = os.environ.get("DISPLAY")
    os.environ["DISPLAY"] = display
    try:
        with _CAPTURE_LOCK, mss.mss() as screenshotter:
            monitor = screenshotter.monitors[1] if len(screenshotter.monitors) > 1 else screenshotter.monitors[0]
            shot = screenshotter.grab(monitor)
            image = Image.frombytes("RGB", shot.size, shot.rgb)
            width = max(360, min(int(requested_width), _SCREEN_WIDTH))
            quality = max(40, min(int(requested_quality), 85))
            if image.width != width:
                height = max(1, round(image.height * width / image.width))
                image = image.resize((width, height), Image.Resampling.BILINEAR)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=False, subsampling=2)
            return output.getvalue()
    finally:
        if old_display is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = old_display


def _special_key(name: str) -> str:
    mapping = {
        "Enter": "Return", "Backspace": "BackSpace", "Tab": "Tab", "Escape": "Escape",
        "Delete": "Delete", "Insert": "Insert", "ArrowUp": "Up", "ArrowDown": "Down",
        "ArrowLeft": "Left", "ArrowRight": "Right", "Home": "Home", "End": "End",
        "PageUp": "Page_Up", "PageDown": "Page_Down", " ": "space",
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
        width: int = Query(default=1000, ge=360, le=_SCREEN_WIDTH),
        quality: int = Query(default=68, ge=40, le=85),
    ) -> Response:
        _require_session(token)
        try:
            content = await asyncio.to_thread(_capture_jpeg, display, width, quality)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Screen capture unavailable: {exc}") from exc
        return Response(
            content,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'",
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
            x = max(0, min(_SCREEN_WIDTH - 1, int(payload.get("x", 0))))
            y = max(0, min(_SCREEN_HEIGHT - 1, int(payload.get("y", 0))))
            button = max(1, min(3, int(payload.get("button", 1))))
            await asyncio.to_thread(_run_xdotool, ["mousemove", "--sync", str(x), str(y)], display)
            if action == "down":
                await asyncio.to_thread(_run_xdotool, ["mousedown", str(button)], display)
            elif action == "up":
                await asyncio.to_thread(_run_xdotool, ["mouseup", str(button)], display)
            elif action == "click":
                await asyncio.to_thread(_run_xdotool, ["click", str(button)], display)
            elif action == "doubleclick":
                await asyncio.to_thread(_run_xdotool, ["click", "--repeat", "2", "--delay", "105", str(button)], display)

        elif action == "scroll":
            direction = int(payload.get("direction", 1))
            amount = max(1, min(8, abs(int(payload.get("amount", 2)))))
            button = "4" if direction < 0 else "5"
            await asyncio.to_thread(_run_xdotool, ["click", "--repeat", str(amount), button], display)

        elif action == "text":
            text = str(payload.get("text", ""))[:1000]
            if text:
                await asyncio.to_thread(_run_xdotool, ["type", "--clearmodifiers", "--delay", "1", "--", text], display)

        elif action == "key":
            key = _special_key(str(payload.get("key", "")))
            allowed_modifiers = {"Control": "ctrl", "Alt": "alt", "Shift": "shift", "Meta": "super"}
            modifiers = [allowed_modifiers[item] for item in payload.get("modifiers", []) if item in allowed_modifiers]
            chord = "+".join([*modifiers, key]) if modifiers else key
            if not chord or len(chord) > 80:
                raise HTTPException(status_code=400, detail="Invalid key input.")
            await asyncio.to_thread(_run_xdotool, ["key", "--clearmodifiers", chord], display)

        else:
            raise HTTPException(status_code=400, detail="Unknown desktop input action.")

        return JSONResponse({"ok": True})

    @app.post("/api/desktop/launch")
    async def desktop_launch(
        payload: dict[str, Any] = Body(default_factory=dict),
        token: str = Query(min_length=16),
    ) -> JSONResponse:
        _require_session(token)
        name = str(payload.get("app", ""))
        await asyncio.to_thread(_launch_app, name, display)
        return JSONResponse({"ok": True, "app": name, "message": f"Launching {name}."})

    @app.get("/api/desktop/ping")
    async def desktop_ping(token: str = Query(min_length=16)) -> JSONResponse:
        _require_session(token)
        return JSONResponse({"ok": True, "expires_in": SESSION_TTL_SECONDS})

    @app.get("/api/desktop/capabilities")
    async def desktop_capabilities() -> JSONResponse:
        return JSONResponse(
            {
                "ok": True,
                "transport": "adaptive-https-frame-polling",
                "display": display,
                "screen": {"width": _SCREEN_WIDTH, "height": _SCREEN_HEIGHT},
                "xdotool": shutil.which("xdotool") is not None,
                "apps": {name: shutil.which(command[0]) is not None for name, command in APP_COMMANDS.items()},
                "mobile_controls": True,
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
    :root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:#080b14;color:#f6f7ff;--line:#ffffff1a;--panel:#0d1222ef;--muted:#abb5d8;--good:#49e5a2;--bad:#ff6578}
    *{box-sizing:border-box}html,body{width:100%;height:100%;margin:0;overflow:hidden;background:#080b14}button,input{font:inherit;-webkit-tap-highlight-color:transparent}
    .shell{position:fixed;inset:0;display:grid;grid-template-rows:52px 1fr;background:#080b14}.bar{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:0 max(10px,env(safe-area-inset-right)) 0 max(12px,env(safe-area-inset-left));border-bottom:1px solid var(--line);background:#0d1222}
    .brand,.controls{display:flex;align-items:center;gap:9px}.brand strong{font-size:.92rem}.status{font-size:.76rem;color:var(--muted)}.dot{width:9px;height:9px;border-radius:50%;background:#ffd166;box-shadow:0 0 14px currentColor}.dot.online{color:var(--good);background:currentColor}.dot.bad{color:var(--bad);background:currentColor}
    .controls button{min-width:38px;height:34px;border:1px solid var(--line);border-radius:10px;color:#e8ebff;background:#ffffff0b;cursor:pointer}.controls button.active{background:#8d9cff;color:#071020}.controls button span{font-size:.68rem;margin-left:4px}.stage{position:relative;display:grid;place-items:center;min-height:0;padding-bottom:74px;background:#15171b;touch-action:none;user-select:none;-webkit-user-select:none}
    #screen{display:block;max-width:100%;max-height:100%;width:auto;height:auto;background:#111;object-fit:contain;touch-action:none;-webkit-user-drag:none;box-shadow:0 20px 70px #0007}
    .login,.notice{position:absolute;z-index:20;width:min(430px,calc(100% - 28px));padding:24px;border:1px solid var(--line);border-radius:22px;background:var(--panel);box-shadow:0 24px 80px #0009;backdrop-filter:blur(20px)}.login h1{margin:0 0 8px;font-size:1.7rem}.login p,.notice p{color:var(--muted);line-height:1.55}.login input{width:100%;margin:10px 0 12px;padding:13px 14px;border:1px solid #ffffff20;border-radius:12px;color:#fff;background:#ffffff0a;outline:none}.login button,.notice button{width:100%;border:0;border-radius:12px;padding:13px 16px;color:#071020;background:#f5f7ff;font-weight:850;cursor:pointer}.error{min-height:20px;color:#ff8b99;font-size:.85rem}.hidden{display:none!important}
    .launcher{position:absolute;z-index:11;left:50%;bottom:max(8px,env(safe-area-inset-bottom));transform:translateX(-50%);width:min(780px,calc(100% - 16px));display:grid;grid-template-columns:repeat(5,1fr);gap:6px;padding:7px;border:1px solid var(--line);border-radius:18px;background:#0d1222ed;backdrop-filter:blur(18px);box-shadow:0 18px 55px #0008}.launcher button{min-width:0;min-height:54px;display:grid;place-items:center;gap:2px;border:0;border-radius:12px;color:#f4f6ff;background:transparent;cursor:pointer}.launcher button:active{background:#ffffff18}.launcher .app-icon{font-size:1.18rem}.launcher small{font-size:.62rem;color:#bdc6e8;font-weight:750}
    .keyboard{position:absolute;z-index:18;left:50%;bottom:max(8px,env(safe-area-inset-bottom));transform:translateX(-50%);width:min(820px,calc(100% - 16px));padding:9px;border:1px solid var(--line);border-radius:17px;background:#0d1222f5;backdrop-filter:blur(18px);box-shadow:0 20px 70px #0009}.keyboard-row{display:flex;gap:7px}.keyboard-row+.keyboard-row{margin-top:7px}.keyboard input{min-width:0;flex:1;border:1px solid var(--line);border-radius:10px;padding:11px 12px;color:#fff;background:#ffffff0a}.keyboard button{min-width:44px;border:1px solid var(--line);border-radius:10px;padding:9px 11px;color:#fff;background:#ffffff0c;font-weight:750}.keyboard .wide{flex:1}.hint{position:absolute;left:10px;bottom:84px;padding:7px 10px;border-radius:10px;color:#c5ccef;background:#05070db8;font-size:.7rem;pointer-events:none}.toast{position:absolute;z-index:30;top:12px;left:50%;transform:translateX(-50%);padding:9px 13px;border:1px solid var(--line);border-radius:12px;background:#0d1222ef;color:#e8ebff;font-size:.78rem;box-shadow:0 16px 40px #0008}
    @media(max-width:650px){.shell{grid-template-rows:48px 1fr}.bar{min-width:0}.brand strong{font-size:.8rem}.status{display:none}.controls{gap:5px}.controls button{min-width:35px;height:33px}.controls button span{display:none}.stage{padding-bottom:70px}.launcher{grid-template-columns:repeat(5,1fr);gap:2px;padding:5px}.launcher button{min-height:52px}.launcher small{font-size:.56rem}.hint{display:none}.keyboard{padding:7px}.keyboard-row{gap:4px}.keyboard button{min-width:38px;padding:9px 7px;font-size:.73rem}}
    @media(max-width:380px){.launcher small{font-size:.52rem}.launcher .app-icon{font-size:1.05rem}.brand strong{font-size:.74rem}}
  </style>
</head>
<body>
  <main class="shell">
    <header class="bar">
      <div class="brand"><span id="dot" class="dot"></span><strong>Ripo Team Linux</strong><span id="status" class="status">Waiting for login</span></div>
      <div class="controls">
        <button id="qualityButton" title="Stream quality">Auto</button>
        <button id="rightClickButton" title="Right-click mode">⌁</button>
        <button id="keyboardButton" title="Keyboard">⌨<span>Type</span></button>
        <button id="refreshButton" title="Reconnect">↻</button>
        <button id="fullscreenButton" title="Fullscreen">⛶</button>
      </div>
    </header>
    <section id="stage" class="stage">
      <img id="screen" alt="Linux desktop" draggable="false">
      <section id="login" class="login">
        <h1>Your Linux computer</h1>
        <p>Enter the desktop password saved in your Hugging Face Space secrets.</p>
        <input id="password" type="password" autocomplete="current-password" placeholder="Desktop password">
        <button id="loginButton">Connect</button>
        <div id="loginError" class="error"></div>
      </section>
      <section id="notice" class="notice hidden"><h2>Connection paused</h2><p id="noticeText">The desktop session stopped.</p><button id="noticeButton">Reconnect</button></section>
      <nav id="launcher" class="launcher hidden" aria-label="Linux app launcher">
        <button data-app="browser"><span class="app-icon">◎</span><small>Browser</small></button>
        <button data-app="files"><span class="app-icon">▱</span><small>Files</small></button>
        <button data-app="terminal"><span class="app-icon">›_</span><small>Terminal</small></button>
        <button data-app="editor"><span class="app-icon">✎</span><small>Editor</small></button>
        <button data-app="settings"><span class="app-icon">⚙</span><small>Settings</small></button>
      </nav>
      <div id="keyboard" class="keyboard hidden">
        <div class="keyboard-row"><input id="textInput" autocomplete="off" autocapitalize="none" spellcheck="false" placeholder="Type into Linux…"><button id="sendText">Send</button><button id="keyboardClose">×</button></div>
        <div class="keyboard-row"><button data-key="Escape">Esc</button><button data-key="Tab">Tab</button><button data-key="Enter">Enter</button><button data-key="Backspace">⌫</button><button data-chord="Control,l">Ctrl+L</button><button data-chord="Control,Alt,t" class="wide">New terminal</button></div>
      </div>
      <div id="toast" class="toast hidden"></div>
      <div class="hint">Single-tap icons, double-tap stubborn items, drag windows, or use the launcher below</div>
    </section>
  </main>
  <script>
    "use strict";
    const q=s=>document.querySelector(s), qa=s=>[...document.querySelectorAll(s)];
    const el={stage:q("#stage"),screen:q("#screen"),login:q("#login"),password:q("#password"),loginButton:q("#loginButton"),loginError:q("#loginError"),dot:q("#dot"),status:q("#status"),notice:q("#notice"),noticeText:q("#noticeText"),noticeButton:q("#noticeButton"),keyboard:q("#keyboard"),textInput:q("#textInput"),launcher:q("#launcher"),toast:q("#toast"),quality:q("#qualityButton"),rightClick:q("#rightClickButton")};
    let token=sessionStorage.getItem("ripo-desktop-token")||"",running=false,inFlight=false,lastObjectUrl="",pointerDown=false,pointerButton=1,lastMove=0,gesture=null,lastTap=null,rightClickNext=false,movePending=null,moveBusy=false,toastTimer=0;
    const coarse=matchMedia("(pointer:coarse)").matches;
    const profiles={auto:{label:"Auto",quality:coarse?62:72,delay:coarse?330:220,factor:coarse?1.08:1.22,min:560},sharp:{label:"Sharp",quality:82,delay:coarse?430:290,factor:1.35,min:760},saver:{label:"Saver",quality:48,delay:coarse?620:480,factor:.9,min:430}};
    let profileName=localStorage.getItem("ripo-desktop-quality")||"auto";
    if(!profiles[profileName])profileName="auto";
    const api=(path,options={})=>fetch(path,{cache:"no-store",...options,headers:{"content-type":"application/json",...(options.headers||{})}});
    function state(kind,text){el.dot.className=`dot ${kind}`;el.status.textContent=text}
    function toast(text){clearTimeout(toastTimer);el.toast.textContent=text;el.toast.classList.remove("hidden");toastTimer=setTimeout(()=>el.toast.classList.add("hidden"),1800)}
    function profile(){return profiles[profileName]}
    function applyProfile(){el.quality.textContent=profile().label;localStorage.setItem("ripo-desktop-quality",profileName)}
    async function login(){el.loginError.textContent="Connecting…";try{const r=await api("/api/desktop/login",{method:"POST",body:JSON.stringify({password:el.password.value})});const data=await r.json();if(!r.ok)throw new Error(data.detail||`HTTP ${r.status}`);token=data.token;sessionStorage.setItem("ripo-desktop-token",token);el.login.classList.add("hidden");el.notice.classList.add("hidden");el.launcher.classList.remove("hidden");el.loginError.textContent="";start()}catch(e){el.loginError.textContent=e.message;state("bad","Login failed")}}
    function coordinates(event){const rect=el.screen.getBoundingClientRect();if(!rect.width||!rect.height)return{x:0,y:0};return{x:Math.max(0,Math.min(1365,Math.round((event.clientX-rect.left)/rect.width*1366))),y:Math.max(0,Math.min(767,Math.round((event.clientY-rect.top)/rect.height*768)))}}
    async function input(payload){if(!token)return;try{const r=await api(`/api/desktop/input?token=${encodeURIComponent(token)}`,{method:"POST",body:JSON.stringify(payload)});if(r.status===401)expired()}catch{}}
    async function launch(name){if(!token)return;toast(`Launching ${name}…`);try{const r=await api(`/api/desktop/launch?token=${encodeURIComponent(token)}`,{method:"POST",body:JSON.stringify({app:name})});const data=await r.json();if(!r.ok)throw new Error(data.detail||`HTTP ${r.status}`);toast(`${name[0].toUpperCase()+name.slice(1)} opened`)}catch(e){toast(e.message)}}
    function expired(){running=false;token="";sessionStorage.removeItem("ripo-desktop-token");el.launcher.classList.add("hidden");el.login.classList.remove("hidden");state("bad","Session expired")}
    async function frame(){if(!running||inFlight||document.hidden)return;inFlight=true;const p=profile();try{const width=Math.max(p.min,Math.min(1366,Math.round(el.stage.clientWidth*p.factor)));const r=await fetch(`/api/desktop/frame?token=${encodeURIComponent(token)}&width=${width}&quality=${p.quality}&t=${Date.now()}`,{cache:"no-store"});if(r.status===401){expired();return}if(!r.ok)throw new Error(`HTTP ${r.status}`);const blob=await r.blob();const next=URL.createObjectURL(blob);await new Promise(resolve=>{el.screen.onload=resolve;el.screen.onerror=resolve;el.screen.src=next});if(lastObjectUrl)URL.revokeObjectURL(lastObjectUrl);lastObjectUrl=next;state("online","Connected")}catch{state("bad","Reconnecting…")}finally{inFlight=false;if(running&&!document.hidden)setTimeout(frame,p.delay)}}
    function start(){if(!token){el.login.classList.remove("hidden");return}running=true;el.launcher.classList.remove("hidden");state("","Loading desktop…");frame()}
    function queueMove(point){movePending=point;if(moveBusy)return;moveBusy=true;(async()=>{while(movePending){const next=movePending;movePending=null;await input({action:"move",...next})}moveBusy=false})()}
    function tap(point){const now=performance.now();const button=rightClickNext?3:1;rightClickNext=false;el.rightClick.classList.remove("active");input({action:"click",...point,button});if(button===1&&lastTap&&now-lastTap.time<360&&Math.hypot(point.x-lastTap.x,point.y-lastTap.y)<45){input({action:"doubleclick",...point,button:1});lastTap=null}else lastTap={...point,time:now}}
    el.loginButton.onclick=login;el.password.addEventListener("keydown",e=>{if(e.key==="Enter")login()});q("#refreshButton").onclick=()=>{running=false;setTimeout(start,100)};q("#fullscreenButton").onclick=async()=>{try{if(!document.fullscreenElement)await document.documentElement.requestFullscreen();else await document.exitFullscreen()}catch{toast("Open this page directly for fullscreen")}};
    q("#keyboardButton").onclick=()=>{el.keyboard.classList.remove("hidden");el.launcher.classList.add("hidden");el.textInput.focus()};q("#keyboardClose").onclick=()=>{el.keyboard.classList.add("hidden");el.launcher.classList.remove("hidden")};q("#sendText").onclick=()=>{const text=el.textInput.value;if(text)input({action:"text",text});el.textInput.value=""};el.textInput.addEventListener("keydown",e=>{if(e.key==="Enter"&&e.shiftKey===false){e.preventDefault();q("#sendText").click()}});
    qa("[data-key]").forEach(button=>button.onclick=()=>input({action:"key",key:button.dataset.key}));qa("[data-chord]").forEach(button=>button.onclick=()=>{const parts=button.dataset.chord.split(",");const key=parts.pop();input({action:"key",key,modifiers:parts.map(x=>x==="Control"?"Control":x==="Alt"?"Alt":x)})});qa("[data-app]").forEach(button=>button.onclick=()=>launch(button.dataset.app));
    el.quality.onclick=()=>{profileName=profileName==="auto"?"sharp":profileName==="sharp"?"saver":"auto";applyProfile();toast(`${profile().label} stream mode`)};el.rightClick.onclick=()=>{rightClickNext=!rightClickNext;el.rightClick.classList.toggle("active",rightClickNext);toast(rightClickNext?"Next tap is a right-click":"Right-click cancelled")};
    el.screen.addEventListener("pointerdown",e=>{e.preventDefault();el.screen.setPointerCapture(e.pointerId);pointerDown=true;pointerButton=e.button===2?3:1;gesture={type:e.pointerType,start:coordinates(e),startX:e.clientX,startY:e.clientY,moved:false,dragStarted:false};if(e.pointerType!=="touch")input({action:"down",...gesture.start,button:pointerButton})});
    el.screen.addEventListener("pointermove",e=>{if(!pointerDown||!gesture)return;const now=performance.now();if(now-lastMove<38)return;lastMove=now;const point=coordinates(e);const distance=Math.hypot(e.clientX-gesture.startX,e.clientY-gesture.startY);if(distance>7)gesture.moved=true;if(gesture.type==="touch"&&gesture.moved&&!gesture.dragStarted){gesture.dragStarted=true;input({action:"down",...gesture.start,button:1})}if(gesture.type!=="touch"||gesture.dragStarted)queueMove(point)});
    el.screen.addEventListener("pointerup",e=>{e.preventDefault();const point=coordinates(e);if(gesture?.type==="touch"){if(gesture.dragStarted)input({action:"up",...point,button:1});else tap(point)}else input({action:"up",...point,button:pointerButton});pointerDown=false;gesture=null});
    el.screen.addEventListener("pointercancel",()=>{if(gesture?.dragStarted)input({action:"up",...gesture.start,button:1});pointerDown=false;gesture=null});el.screen.addEventListener("dblclick",e=>{if(e.pointerType==="touch")return;input({action:"doubleclick",...coordinates(e),button:1})});el.screen.addEventListener("contextmenu",e=>{e.preventDefault();input({action:"click",...coordinates(e),button:3})});el.screen.addEventListener("wheel",e=>{e.preventDefault();input({action:"scroll",direction:e.deltaY<0?-1:1,amount:Math.min(5,Math.max(1,Math.round(Math.abs(e.deltaY)/50)))})},{passive:false});
    document.addEventListener("keydown",e=>{if(document.activeElement===el.password||document.activeElement===el.textInput)return;if(["Enter","Backspace","Tab","Escape","Delete","ArrowUp","ArrowDown","ArrowLeft","ArrowRight","Home","End","PageUp","PageDown"].includes(e.key)){e.preventDefault();input({action:"key",key:e.key,modifiers:[e.ctrlKey?"Control":"",e.altKey?"Alt":"",e.shiftKey?"Shift":"",e.metaKey?"Meta":""].filter(Boolean)})}else if(e.key.length===1&&!e.ctrlKey&&!e.metaKey){e.preventDefault();input({action:"text",text:e.key})}});
    document.addEventListener("visibilitychange",()=>{if(document.hidden){running=false}else if(token){running=true;frame()}});setInterval(()=>{if(token&&!document.hidden)fetch(`/api/desktop/ping?token=${encodeURIComponent(token)}`,{cache:"no-store"}).catch(()=>{})},240000);
    applyProfile();if(token){el.login.classList.add("hidden");start()}else el.password.focus();
  </script>
</body>
</html>'''

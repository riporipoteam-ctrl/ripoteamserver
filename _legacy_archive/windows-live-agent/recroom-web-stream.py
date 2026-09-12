from __future__ import annotations

import argparse
import ctypes
import io
import json
import os
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

try:
    from PIL import Image, ImageGrab
except ImportError as exc:
    raise SystemExit("Pillow is required. Install with: py -3 -m pip install Pillow") from exc

if os.name != "nt":
    raise SystemExit("recroom_web_stream.py only runs on Windows.")

user32 = ctypes.windll.user32

SW_RESTORE = 9
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120

VK = {
    "Backspace": 0x08,
    "Tab": 0x09,
    "Enter": 0x0D,
    "Shift": 0x10,
    "Control": 0x11,
    "Alt": 0x12,
    "Escape": 0x1B,
    "Space": 0x20,
    " ": 0x20,
    "ArrowLeft": 0x25,
    "ArrowUp": 0x26,
    "ArrowRight": 0x27,
    "ArrowDown": 0x28,
    "Delete": 0x2E,
}
for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    VK[ch.lower()] = ord(ch)
    VK[ch] = ord(ch)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def process_id_for_window(hwnd: int) -> int:
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def find_window(pid: int) -> int | None:
    matches: list[int] = []
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @enum_proc
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        if process_id_for_window(hwnd) != pid:
            return True
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        if rect.right - rect.left >= 320 and rect.bottom - rect.top >= 240:
            matches.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    return matches[0] if matches else None


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("Could not read Rec Room window bounds.")
    return rect.left, rect.top, rect.right, rect.bottom


def focus_window(hwnd: int) -> None:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.05)
    user32.SetForegroundWindow(hwnd)


def send_key(hwnd: int, key: str, down: bool) -> None:
    code = VK.get(key)
    if not code and len(key) == 1:
        code = ord(key.upper())
    if not code:
        return
    focus_window(hwnd)
    user32.keybd_event(code, 0, 0 if down else KEYEVENTF_KEYUP, 0)


def button_flags(button: str) -> tuple[int, int]:
    normalized = button.lower()
    if normalized == "right":
        return MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
    if normalized == "middle":
        return MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP
    return MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP


def move_absolute(hwnd: int, x_norm: float, y_norm: float) -> None:
    left, top, right, bottom = window_rect(hwnd)
    x_norm = min(1.0, max(0.0, x_norm))
    y_norm = min(1.0, max(0.0, y_norm))
    x = int(left + (right - left) * x_norm)
    y = int(top + (bottom - top) * y_norm)
    focus_window(hwnd)
    user32.SetCursorPos(x, y)


def send_mouse_button(hwnd: int, button: str, down: bool, x_norm: float | None = None, y_norm: float | None = None) -> None:
    if x_norm is not None and y_norm is not None:
        move_absolute(hwnd, x_norm, y_norm)
    else:
        focus_window(hwnd)
    down_flag, up_flag = button_flags(button)
    user32.mouse_event(down_flag if down else up_flag, 0, 0, 0, 0)


def send_click(hwnd: int, x_norm: float, y_norm: float, button: str) -> None:
    send_mouse_button(hwnd, button, True, x_norm, y_norm)
    send_mouse_button(hwnd, button, False)


def send_mouse_move(hwnd: int, dx: int, dy: int) -> None:
    focus_window(hwnd)
    dx = max(-5000, min(5000, int(dx)))
    dy = max(-5000, min(5000, int(dy)))
    if dx or dy:
        user32.mouse_event(MOUSEEVENTF_MOVE, dx, dy, 0, 0)


def send_wheel(hwnd: int, delta: int) -> None:
    focus_window(hwnd)
    delta = max(-10, min(10, int(delta)))
    if delta:
        user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, delta * WHEEL_DELTA, 0)


def capture_jpeg(hwnd: int, max_width: int, quality: int) -> bytes:
    bbox = window_rect(hwnd)
    image = ImageGrab.grab(bbox=bbox, all_screens=True)
    if image.width > max_width:
        height = max(1, int(image.height * max_width / image.width))
        image = image.resize((max_width, height), Image.Resampling.BILINEAR)
    if image.mode != "RGB":
        image = image.convert("RGB")
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=quality, optimize=True)
    return output.getvalue()


def html_page(token: str) -> bytes:
    safe = json.dumps(token)
    return f"""<!doctype html>
<html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover\">
<title>Flux Rec Room Host</title>
<style>
html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#000;color:#fff;font-family:system-ui,sans-serif;touch-action:none;-webkit-user-select:none;user-select:none}}
#wrap{{position:fixed;inset:0;display:grid;place-items:center;background:#000}}
#frame{{max-width:100%;max-height:100%;width:100%;height:100%;object-fit:contain;outline:none;cursor:crosshair;user-select:none;-webkit-user-drag:none;touch-action:none}}
#status{{position:fixed;left:max(10px,env(safe-area-inset-left));bottom:max(10px,env(safe-area-inset-bottom));padding:6px 9px;border-radius:999px;background:#000b;font-size:11px;pointer-events:none;z-index:30}}
#hint{{position:fixed;right:10px;bottom:10px;padding:6px 9px;border-radius:999px;background:#0009;font-size:11px;color:#ddd;pointer-events:none;z-index:30}}
#touchControls{{display:none}}
@media (pointer:coarse){{
  #hint{{display:none}}
  #touchControls{{display:block;position:fixed;inset:0;z-index:20;pointer-events:none}}
  #movePad{{position:absolute;left:max(18px,env(safe-area-inset-left));bottom:max(48px,calc(env(safe-area-inset-bottom) + 20px));width:138px;height:138px;border-radius:50%;border:1px solid #ffffff32;background:#0007;box-shadow:inset 0 0 0 22px #ffffff08;pointer-events:auto;touch-action:none}}
  #moveStick{{position:absolute;left:43px;top:43px;width:52px;height:52px;border-radius:50%;background:#ffffff38;border:1px solid #ffffff66;transform:translate(0,0);pointer-events:none}}
  #lookPad{{position:absolute;right:0;top:0;width:58%;height:100%;pointer-events:auto;touch-action:none}}
  .action{{position:absolute;display:grid;place-items:center;border-radius:50%;border:1px solid #ffffff45;background:#0009;color:#fff;font:800 11px system-ui;letter-spacing:.03em;pointer-events:auto;touch-action:none;box-shadow:0 4px 22px #0008}}
  #jumpBtn{{right:max(20px,env(safe-area-inset-right));bottom:max(46px,calc(env(safe-area-inset-bottom) + 18px));width:76px;height:76px}}
  #actBtn{{right:max(102px,calc(env(safe-area-inset-right) + 84px));bottom:max(94px,calc(env(safe-area-inset-bottom) + 66px));width:62px;height:62px}}
  #runBtn{{left:max(120px,calc(env(safe-area-inset-left) + 102px));bottom:max(178px,calc(env(safe-area-inset-bottom) + 150px));width:50px;height:50px;font-size:9px}}
}}
</style></head><body>
<div id=\"wrap\"><img id=\"frame\" tabindex=\"0\" alt=\"Rec Room live game window\"></div>
<div id=\"status\">Flux remote control</div><div id=\"hint\">Click to capture mouse · Esc releases</div>
<div id=\"touchControls\"><div id=\"lookPad\"></div><div id=\"movePad\"><div id=\"moveStick\"></div></div><button id=\"jumpBtn\" class=\"action\">JUMP</button><button id=\"actBtn\" class=\"action\">ACT</button><button id=\"runBtn\" class=\"action\">RUN</button></div>
<script>
const token={safe};const frame=document.getElementById('frame'),status=document.getElementById('status');const coarse=matchMedia('(pointer:coarse)').matches;
let stopped=false,last=0;const fps=15;let pendingDx=0,pendingDy=0,moveScheduled=false;
function refresh(){{if(stopped)return;const now=Date.now();if(now-last>1000/fps){{last=now;frame.src=`frame.jpg?token=${{encodeURIComponent(token)}}&t=${{now}}`;}}requestAnimationFrame(refresh);}}
frame.onload=()=>{{status.textContent=coarse?'Connected · touch controls ready':(document.pointerLockElement===frame?'Connected · mouse captured':'Connected · click to control');}};
frame.onerror=()=>{{status.textContent='Waiting for Rec Room window…';}};
async function input(payload){{try{{await fetch(`input?token=${{encodeURIComponent(token)}}`,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify(payload),cache:'no-store'}});}}catch{{}}}}
function norm(ev){{const r=frame.getBoundingClientRect();const iw=frame.naturalWidth||r.width,ih=frame.naturalHeight||r.height;const scale=Math.min(r.width/iw,r.height/ih);const dw=iw*scale,dh=ih*scale;const ox=r.left+(r.width-dw)/2,oy=r.top+(r.height-dh)/2;return{{x:(ev.clientX-ox)/dw,y:(ev.clientY-oy)/dh}};}}
function buttonName(button){{return button===2?'right':button===1?'middle':'left';}}
function flushMove(){{moveScheduled=false;const dx=Math.round(pendingDx),dy=Math.round(pendingDy);pendingDx=0;pendingDy=0;if(dx||dy)input({{type:'move',dx,dy}});}}
function queueMove(dx,dy){{pendingDx+=dx;pendingDy+=dy;if(!moveScheduled){{moveScheduled=true;requestAnimationFrame(flushMove);}}}}
if(!coarse){{
 frame.addEventListener('pointerdown',ev=>{{ev.preventDefault();frame.focus();const button=buttonName(ev.button);if(document.pointerLockElement===frame){{input({{type:'button',button,down:true}});}}else{{const p=norm(ev);input({{type:'button',button,down:true,x:p.x,y:p.y}});if(ev.button===0&&frame.requestPointerLock){{try{{const result=frame.requestPointerLock();result?.catch?.(()=>{{}});}}catch{{}}}}}}}});
 frame.addEventListener('pointerup',ev=>{{ev.preventDefault();input({{type:'button',button:buttonName(ev.button),down:false}});}});
 frame.addEventListener('mousemove',ev=>{{if(document.pointerLockElement!==frame)return;queueMove(ev.movementX||0,ev.movementY||0);}});
 frame.addEventListener('wheel',ev=>{{ev.preventDefault();input({{type:'wheel',delta:ev.deltaY<0?1:-1}});}},{{passive:false}});
 frame.addEventListener('contextmenu',ev=>ev.preventDefault());
 document.addEventListener('pointerlockchange',()=>{{const locked=document.pointerLockElement===frame;status.textContent=locked?'Connected · mouse captured':'Connected · click to control';if(!locked)input({{type:'release'}});}});
 window.addEventListener('keydown',ev=>{{if(['F5','F11','F12'].includes(ev.key))return;ev.preventDefault();input({{type:'key',key:ev.key,down:true}});}});
 window.addEventListener('keyup',ev=>{{if(['F5','F11','F12'].includes(ev.key))return;ev.preventDefault();input({{type:'key',key:ev.key,down:false}});}});
}}
const movePad=document.getElementById('movePad'),moveStick=document.getElementById('moveStick'),lookPad=document.getElementById('lookPad');
let movePointer=null,moveCx=0,moveCy=0,activeMove=new Set(),lookPointer=null,lookX=0,lookY=0;
function setMove(next){{const desired=new Set(next);for(const key of activeMove)if(!desired.has(key))input({{type:'key',key,down:false}});for(const key of desired)if(!activeMove.has(key))input({{type:'key',key,down:true}});activeMove=desired;}}
function stopMove(){{setMove([]);moveStick.style.transform='translate(0,0)';movePointer=null;}}
movePad.addEventListener('pointerdown',ev=>{{if(!coarse)return;ev.preventDefault();movePointer=ev.pointerId;movePad.setPointerCapture(ev.pointerId);const r=movePad.getBoundingClientRect();moveCx=r.left+r.width/2;moveCy=r.top+r.height/2;}});
movePad.addEventListener('pointermove',ev=>{{if(ev.pointerId!==movePointer)return;ev.preventDefault();let dx=ev.clientX-moveCx,dy=ev.clientY-moveCy;const mag=Math.hypot(dx,dy)||1,max=42,scale=Math.min(1,max/mag);dx*=scale;dy*=scale;moveStick.style.transform=`translate(${{dx}}px,${{dy}}px)`;const keys=[];if(dy<-13)keys.push('w');if(dy>13)keys.push('s');if(dx<-13)keys.push('a');if(dx>13)keys.push('d');setMove(keys);}});
movePad.addEventListener('pointerup',stopMove);movePad.addEventListener('pointercancel',stopMove);
lookPad.addEventListener('pointerdown',ev=>{{if(!coarse)return;ev.preventDefault();lookPointer=ev.pointerId;lookX=ev.clientX;lookY=ev.clientY;lookPad.setPointerCapture(ev.pointerId);}});
lookPad.addEventListener('pointermove',ev=>{{if(ev.pointerId!==lookPointer)return;ev.preventDefault();const dx=(ev.clientX-lookX)*1.45,dy=(ev.clientY-lookY)*1.45;lookX=ev.clientX;lookY=ev.clientY;queueMove(dx,dy);}});
function stopLook(ev){{if(ev.pointerId!==lookPointer)return;lookPointer=null;}}lookPad.addEventListener('pointerup',stopLook);lookPad.addEventListener('pointercancel',stopLook);
function bindHold(id,payloadDown,payloadUp){{const el=document.getElementById(id);const down=ev=>{{if(!coarse)return;ev.preventDefault();el.setPointerCapture?.(ev.pointerId);input(payloadDown);}};const up=ev=>{{if(!coarse)return;ev.preventDefault();input(payloadUp);}};el.addEventListener('pointerdown',down);el.addEventListener('pointerup',up);el.addEventListener('pointercancel',up);}}
bindHold('jumpBtn',{{type:'key',key:'Space',down:true}},{{type:'key',key:'Space',down:false}});bindHold('actBtn',{{type:'button',button:'left',down:true}},{{type:'button',button:'left',down:false}});bindHold('runBtn',{{type:'key',key:'Shift',down:true}},{{type:'key',key:'Shift',down:false}});
window.addEventListener('blur',()=>{{stopMove();input({{type:'release'}});}});window.addEventListener('beforeunload',()=>{{stopped=true;stopMove();input({{type:'release'}});}});frame.focus();refresh();
</script></body></html>""".encode("utf-8")


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, *, pid: int, token: str, max_width: int, quality: int):
        super().__init__(address, handler)
        self.pid = pid
        self.token = token
        self.max_width = max_width
        self.quality = quality
        self.lock = threading.Lock()
        self.keys_down: set[str] = set()
        self.buttons_down: set[str] = set()

    def hwnd(self) -> int:
        hwnd = find_window(self.pid)
        if not hwnd:
            raise RuntimeError("Rec Room window is not visible yet.")
        return hwnd

    def release_inputs(self) -> None:
        try:
            hwnd = self.hwnd()
        except Exception:
            self.keys_down.clear()
            self.buttons_down.clear()
            return
        for key in list(self.keys_down):
            try:
                send_key(hwnd, key, False)
            except Exception:
                pass
        for button in list(self.buttons_down):
            try:
                send_mouse_button(hwnd, button, False)
            except Exception:
                pass
        self.keys_down.clear()
        self.buttons_down.clear()


class Handler(BaseHTTPRequestHandler):
    server_version = "FluxRecRoomStream/1.2"

    def log_message(self, _format: str, *_args) -> None:
        return

    def parsed(self):
        return urlparse(self.path)

    def authorized(self, parsed) -> bool:
        return parse_qs(parsed.query).get("token", [""])[0] == self.server.token

    def send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, payload: dict) -> None:
        self.send_bytes(status, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))

    def do_GET(self) -> None:
        parsed = self.parsed()
        if parsed.path == "/health":
            try:
                hwnd = self.server.hwnd()
                body = {"ok": True, "pid": self.server.pid, "window": hwnd, "inputVersion": 3, "touchControls": True}
                return self.send_json(HTTPStatus.OK, body)
            except Exception as exc:
                return self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
        if not self.authorized(parsed):
            return self.send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "invalid token"})
        if parsed.path in {"/", "/index.html"}:
            return self.send_bytes(HTTPStatus.OK, "text/html; charset=utf-8", html_page(self.server.token))
        if parsed.path == "/frame.jpg":
            try:
                with self.server.lock:
                    body = capture_jpeg(self.server.hwnd(), self.server.max_width, self.server.quality)
                return self.send_bytes(HTTPStatus.OK, "image/jpeg", body)
            except Exception as exc:
                return self.send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"ok": False, "error": str(exc)})
        return self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        parsed = self.parsed()
        if parsed.path != "/input":
            return self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
        if not self.authorized(parsed):
            return self.send_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "invalid token"})
        try:
            length = min(8192, int(self.headers.get("content-length", "0") or "0"))
            data = json.loads(self.rfile.read(length) or b"{}")
            hwnd = self.server.hwnd()
            kind = str(data.get("type") or "")
            if kind == "key":
                key = str(data.get("key") or "")
                down = bool(data.get("down"))
                send_key(hwnd, key, down)
                if down:
                    self.server.keys_down.add(key)
                else:
                    self.server.keys_down.discard(key)
            elif kind == "click":
                send_click(hwnd, float(data.get("x", 0.5)), float(data.get("y", 0.5)), str(data.get("button") or "left"))
            elif kind == "button":
                button = str(data.get("button") or "left")
                down = bool(data.get("down"))
                x = data.get("x")
                y = data.get("y")
                send_mouse_button(hwnd, button, down, float(x) if x is not None else None, float(y) if y is not None else None)
                if down:
                    self.server.buttons_down.add(button)
                else:
                    self.server.buttons_down.discard(button)
            elif kind == "move":
                send_mouse_move(hwnd, int(data.get("dx", 0)), int(data.get("dy", 0)))
            elif kind == "wheel":
                send_wheel(hwnd, int(data.get("delta", 0)))
            elif kind == "release":
                self.server.release_inputs()
            else:
                return self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "unsupported input"})
            return self.send_json(HTTPStatus.OK, {"ok": True})
        except Exception as exc:
            return self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": str(exc)})


def main() -> int:
    parser = argparse.ArgumentParser(description="Flux Rec Room lightweight browser stream")
    parser.add_argument("--pid", type=int, required=True, help="Rec Room process id")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6081)
    parser.add_argument("--token", default="")
    parser.add_argument("--max-width", type=int, default=1280)
    parser.add_argument("--quality", type=int, default=72)
    args = parser.parse_args()
    token = args.token or secrets.token_urlsafe(32)
    server = Server((args.host, args.port), Handler, pid=args.pid, token=token, max_width=max(640, args.max_width), quality=min(92, max(35, args.quality)))
    print(json.dumps({"ok": True, "host": args.host, "port": args.port, "pid": args.pid, "inputVersion": 3, "touchControls": True}), flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.release_inputs()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

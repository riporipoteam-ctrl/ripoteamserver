from __future__ import annotations

import argparse
import ctypes
import io
import json
import os
import secrets
import sys
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
kernel32 = ctypes.windll.kernel32

SW_RESTORE = 9
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010

VK = {
    "Backspace": 0x08,
    "Tab": 0x09,
    "Enter": 0x0D,
    "Shift": 0x10,
    "Control": 0x11,
    "Alt": 0x12,
    "Escape": 0x1B,
    "Space": 0x20,
    "ArrowLeft": 0x25,
    "ArrowUp": 0x26,
    "ArrowRight": 0x27,
    "ArrowDown": 0x28,
    "Delete": 0x2E,
}
for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    VK[ch.lower()] = ord(ch)


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


def send_click(hwnd: int, x_norm: float, y_norm: float, button: str) -> None:
    left, top, right, bottom = window_rect(hwnd)
    x_norm = min(1.0, max(0.0, x_norm))
    y_norm = min(1.0, max(0.0, y_norm))
    x = int(left + (right - left) * x_norm)
    y = int(top + (bottom - top) * y_norm)
    focus_window(hwnd)
    user32.SetCursorPos(x, y)
    if button == "right":
        user32.mouse_event(MOUSEEVENTF_RIGHTDOWN, 0, 0, 0, 0)
        user32.mouse_event(MOUSEEVENTF_RIGHTUP, 0, 0, 0, 0)
    else:
        user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


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
<html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no\">
<title>Flux Rec Room Host</title>
<style>
html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#000;color:#fff;font-family:system-ui,sans-serif}}
#wrap{{position:fixed;inset:0;display:grid;place-items:center;background:#000}}
#frame{{max-width:100%;max-height:100%;width:100%;height:100%;object-fit:contain;outline:none;cursor:crosshair;user-select:none;-webkit-user-drag:none}}
#status{{position:fixed;left:10px;bottom:10px;padding:6px 9px;border-radius:999px;background:#000a;font-size:11px;pointer-events:none}}
</style></head><body>
<div id=\"wrap\"><img id=\"frame\" tabindex=\"0\" alt=\"Rec Room live game window\"></div><div id=\"status\">Flux remote control</div>
<script>
const token={safe}; const frame=document.getElementById('frame'); const status=document.getElementById('status');
let stopped=false; let last=0; const fps=12;
function refresh(){{if(stopped)return; const now=Date.now(); if(now-last>1000/fps){{last=now; frame.src=`frame.jpg?token=${{encodeURIComponent(token)}}&t=${{now}}`;}} requestAnimationFrame(refresh);}}
frame.onload=()=>{{status.textContent='Connected · click image to control';}};
frame.onerror=()=>{{status.textContent='Waiting for Rec Room window…';}};
async function input(payload){{try{{await fetch(`input?token=${{encodeURIComponent(token)}}`,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify(payload),cache:'no-store'}});}}catch{{}}}}
function norm(ev){{const r=frame.getBoundingClientRect(); const iw=frame.naturalWidth||r.width, ih=frame.naturalHeight||r.height; const scale=Math.min(r.width/iw,r.height/ih); const dw=iw*scale,dh=ih*scale; const ox=r.left+(r.width-dw)/2,oy=r.top+(r.height-dh)/2; return {{x:(ev.clientX-ox)/dw,y:(ev.clientY-oy)/dh}};}}
frame.addEventListener('pointerdown',ev=>{{ev.preventDefault(); frame.focus(); const p=norm(ev); input({{type:'click',x:p.x,y:p.y,button:ev.button===2?'right':'left'}});}});
frame.addEventListener('contextmenu',ev=>ev.preventDefault());
window.addEventListener('keydown',ev=>{{if(['F5','F11','F12'].includes(ev.key))return; ev.preventDefault(); input({{type:'key',key:ev.key,down:true}});}});
window.addEventListener('keyup',ev=>{{if(['F5','F11','F12'].includes(ev.key))return; ev.preventDefault(); input({{type:'key',key:ev.key,down:false}});}});
window.addEventListener('beforeunload',()=>{{stopped=true;}}); frame.focus(); refresh();
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

    def hwnd(self) -> int:
        hwnd = find_window(self.pid)
        if not hwnd:
            raise RuntimeError("Rec Room window is not visible yet.")
        return hwnd


class Handler(BaseHTTPRequestHandler):
    server_version = "FluxRecRoomStream/1.0"

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
                body = {"ok": True, "pid": self.server.pid, "window": hwnd}
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
                send_key(hwnd, str(data.get("key") or ""), bool(data.get("down")))
            elif kind == "click":
                send_click(hwnd, float(data.get("x", 0.5)), float(data.get("y", 0.5)), str(data.get("button") or "left"))
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
    print(json.dumps({"ok": True, "host": args.host, "port": args.port, "pid": args.pid}), flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

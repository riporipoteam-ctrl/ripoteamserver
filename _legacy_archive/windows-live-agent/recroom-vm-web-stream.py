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
    import numpy as np
    import soundcard as sc
    from PIL import Image, ImageGrab
except ImportError as exc:
    raise SystemExit("Pillow, numpy and soundcard are required for the RipoTeam VM streamer.") from exc

if os.name != "nt":
    raise SystemExit("recroom-vm-web-stream.py only runs inside the Windows guest.")

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
    "Backspace": 0x08, "Tab": 0x09, "Enter": 0x0D, "Shift": 0x10,
    "Control": 0x11, "Alt": 0x12, "Escape": 0x1B, "Space": 0x20,
    " ": 0x20, "ArrowLeft": 0x25, "ArrowUp": 0x26, "ArrowRight": 0x27,
    "ArrowDown": 0x28, "Delete": 0x2E,
}
for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    VK[ch] = ord(ch)
    VK[ch.lower()] = ord(ch)


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def process_id_for_window(hwnd: int) -> int:
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def find_window(pid: int) -> int | None:
    matches: list[int] = []
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    @enum_proc
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or process_id_for_window(hwnd) != pid:
            return True
        rect = RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)) and rect.right - rect.left >= 320 and rect.bottom - rect.top >= 240:
            matches.append(int(hwnd))
        return True

    user32.EnumWindows(callback, 0)
    return matches[0] if matches else None


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("Could not read the Rec Room game window.")
    return rect.left, rect.top, rect.right, rect.bottom


def focus(hwnd: int) -> None:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)


def send_key(hwnd: int, key: str, down: bool) -> None:
    code = VK.get(key)
    if not code and len(key) == 1:
        code = ord(key.upper())
    if not code:
        return
    focus(hwnd)
    user32.keybd_event(code, 0, 0 if down else KEYEVENTF_KEYUP, 0)


def button_flags(button: str) -> tuple[int, int]:
    if button == "right":
        return MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP
    if button == "middle":
        return MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP
    return MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP


def move_absolute(hwnd: int, x_norm: float, y_norm: float) -> None:
    left, top, right, bottom = window_rect(hwnd)
    x = int(left + (right - left) * min(1.0, max(0.0, x_norm)))
    y = int(top + (bottom - top) * min(1.0, max(0.0, y_norm)))
    focus(hwnd)
    user32.SetCursorPos(x, y)


def send_button(hwnd: int, button: str, down: bool, x: float | None = None, y: float | None = None) -> None:
    if x is not None and y is not None:
        move_absolute(hwnd, x, y)
    else:
        focus(hwnd)
    down_flag, up_flag = button_flags(button)
    user32.mouse_event(down_flag if down else up_flag, 0, 0, 0, 0)


def send_move(hwnd: int, dx: int, dy: int) -> None:
    focus(hwnd)
    user32.mouse_event(MOUSEEVENTF_MOVE, max(-5000, min(5000, dx)), max(-5000, min(5000, dy)), 0, 0)


def capture_jpeg(hwnd: int, max_width: int, quality: int) -> bytes:
    image = ImageGrab.grab(bbox=window_rect(hwnd), all_screens=True)
    if image.width > max_width:
        image = image.resize((max_width, max(1, int(image.height * max_width / image.width))), Image.Resampling.BILINEAR)
    if image.mode != "RGB":
        image = image.convert("RGB")
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=quality, optimize=True)
    return output.getvalue()


def html_page(token: str) -> bytes:
    safe = json.dumps(token)
    return f"""<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover\"><title>RipoTeam Rec Room VM</title><style>
html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#000;color:#fff;font-family:system-ui,sans-serif;touch-action:none;user-select:none}}#frame{{position:fixed;inset:0;width:100%;height:100%;object-fit:contain;background:#000;outline:none;cursor:crosshair}}#status{{position:fixed;left:12px;bottom:12px;z-index:10;padding:7px 10px;border-radius:999px;background:#000b;font:700 11px system-ui}}#touch{{display:none}}@media(pointer:coarse){{#touch{{display:block;position:fixed;inset:0;z-index:9;pointer-events:none}}#pad{{position:absolute;left:18px;bottom:38px;width:132px;height:132px;border-radius:50%;background:#0008;border:1px solid #fff4;pointer-events:auto}}#stick{{position:absolute;left:41px;top:41px;width:50px;height:50px;border-radius:50%;background:#fff5}}#look{{position:absolute;right:0;top:0;width:58%;height:100%;pointer-events:auto}}.act{{position:absolute;border:1px solid #fff5;background:#000a;color:#fff;border-radius:50%;font-weight:900;pointer-events:auto}}#jump{{right:22px;bottom:42px;width:74px;height:74px}}#use{{right:108px;bottom:94px;width:60px;height:60px}}}}
</style></head><body><img id=\"frame\" tabindex=\"0\"><div id=\"status\">Starting RipoTeam VM stream…</div><div id=\"touch\"><div id=\"look\"></div><div id=\"pad\"><div id=\"stick\"></div></div><button id=\"jump\" class=\"act\">JUMP</button><button id=\"use\" class=\"act\">ACT</button></div><script>
const token={safe},frame=document.getElementById('frame'),status=document.getElementById('status'),coarse=matchMedia('(pointer:coarse)').matches;let stopped=false,last=0,pdx=0,pdy=0,scheduled=false;const fps=20;
async function input(p){{try{{await fetch(`input?token=${{encodeURIComponent(token)}}`,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify(p),cache:'no-store'}})}}catch{{}}}}
function refresh(){{if(stopped)return;const n=Date.now();if(n-last>1000/fps){{last=n;frame.src=`frame.jpg?token=${{encodeURIComponent(token)}}&t=${{n}}`;}}requestAnimationFrame(refresh)}}frame.onload=()=>status.textContent='Rec Room · RipoTeamServer VM';frame.onerror=()=>status.textContent='Launching Rec Room…';
let ac=null,proc=null,audioStarted=false,aq=[],aoff=0;async function startAudio(){{if(audioStarted)return;audioStarted=true;try{{ac=new AudioContext({{sampleRate:48000}});await ac.resume();proc=ac.createScriptProcessor(2048,0,2);proc.onaudioprocess=e=>{{for(let ch=0;ch<2;ch++)e.outputBuffer.getChannelData(ch).fill(0);let need=2048,pos=0;while(need>0&&aq.length){{const b=aq[0],frames=(b.length-aoff)/2,take=Math.min(need,frames);for(let i=0;i<take;i++){{e.outputBuffer.getChannelData(0)[pos+i]=b[aoff+i*2];e.outputBuffer.getChannelData(1)[pos+i]=b[aoff+i*2+1];}}aoff+=take*2;pos+=take;need-=take;if(aoff>=b.length){{aq.shift();aoff=0}}}}}};proc.connect(ac.destination);const r=await fetch(`audio.pcm?token=${{encodeURIComponent(token)}}`,{{cache:'no-store'}});if(!r.ok||!r.body)throw new Error('audio unavailable');const rd=r.body.getReader();let carry=new Uint8Array(0);while(true){{const x=await rd.read();if(x.done)break;let all=new Uint8Array(carry.length+x.value.length);all.set(carry);all.set(x.value,carry.length);const usable=all.length-all.length%4,d=new Float32Array(usable/2);const v=new DataView(all.buffer,all.byteOffset,usable);for(let i=0;i<d.length;i++)d[i]=v.getInt16(i*2,true)/32768;aq.push(d);carry=all.slice(usable);if(aq.length>80)aq.splice(0,aq.length-40)}}}}catch(e){{audioStarted=false;status.textContent='Video connected · tap/click for audio'}}}}
function norm(ev){{const r=frame.getBoundingClientRect(),iw=frame.naturalWidth||r.width,ih=frame.naturalHeight||r.height,s=Math.min(r.width/iw,r.height/ih),dw=iw*s,dh=ih*s,ox=r.left+(r.width-dw)/2,oy=r.top+(r.height-dh)/2;return{{x:(ev.clientX-ox)/dw,y:(ev.clientY-oy)/dh}}}}function btn(b){{return b===2?'right':b===1?'middle':'left'}}function flush(){{scheduled=false;const x=Math.round(pdx),y=Math.round(pdy);pdx=pdy=0;if(x||y)input({{type:'move',dx:x,dy:y}})}}function qmove(x,y){{pdx+=x;pdy+=y;if(!scheduled){{scheduled=true;requestAnimationFrame(flush)}}}}
if(!coarse){{frame.addEventListener('pointerdown',e=>{{e.preventDefault();startAudio();frame.focus();if(document.pointerLockElement===frame)input({{type:'button',button:btn(e.button),down:true}});else{{const p=norm(e);input({{type:'button',button:btn(e.button),down:true,x:p.x,y:p.y}});if(e.button===0)frame.requestPointerLock?.()}}}});frame.addEventListener('pointerup',e=>input({{type:'button',button:btn(e.button),down:false}}));frame.addEventListener('mousemove',e=>{{if(document.pointerLockElement===frame)qmove(e.movementX||0,e.movementY||0)}});frame.addEventListener('wheel',e=>{{e.preventDefault();input({{type:'wheel',delta:e.deltaY<0?1:-1}})}},{{passive:false}});frame.addEventListener('contextmenu',e=>e.preventDefault());window.addEventListener('keydown',e=>{{if(['F5','F11','F12'].includes(e.key))return;e.preventDefault();input({{type:'key',key:e.key,down:true}})}});window.addEventListener('keyup',e=>{{if(['F5','F11','F12'].includes(e.key))return;e.preventDefault();input({{type:'key',key:e.key,down:false}})}})}}
const pad=document.getElementById('pad'),stick=document.getElementById('stick'),look=document.getElementById('look');let mp=null,cx=0,cy=0,keys=new Set(),lp=null,lx=0,ly=0;function setKeys(next){{const n=new Set(next);for(const k of keys)if(!n.has(k))input({{type:'key',key:k,down:false}});for(const k of n)if(!keys.has(k))input({{type:'key',key:k,down:true}});keys=n}}function stopMove(){{setKeys([]);stick.style.transform='translate(0,0)';mp=null}}pad.addEventListener('pointerdown',e=>{{if(!coarse)return;startAudio();e.preventDefault();mp=e.pointerId;pad.setPointerCapture(e.pointerId);const r=pad.getBoundingClientRect();cx=r.left+r.width/2;cy=r.top+r.height/2}});pad.addEventListener('pointermove',e=>{{if(e.pointerId!==mp)return;let dx=e.clientX-cx,dy=e.clientY-cy,m=Math.hypot(dx,dy)||1,s=Math.min(1,40/m);dx*=s;dy*=s;stick.style.transform=`translate(${{dx}}px,${{dy}}px)`;const n=[];if(dy<-12)n.push('w');if(dy>12)n.push('s');if(dx<-12)n.push('a');if(dx>12)n.push('d');setKeys(n)}});pad.addEventListener('pointerup',stopMove);pad.addEventListener('pointercancel',stopMove);look.addEventListener('pointerdown',e=>{{if(!coarse)return;startAudio();lp=e.pointerId;lx=e.clientX;ly=e.clientY;look.setPointerCapture(e.pointerId)}});look.addEventListener('pointermove',e=>{{if(e.pointerId!==lp)return;qmove((e.clientX-lx)*1.5,(e.clientY-ly)*1.5);lx=e.clientX;ly=e.clientY}});look.addEventListener('pointerup',e=>{{if(e.pointerId===lp)lp=null}});function hold(id,down,up){{const x=document.getElementById(id);x.addEventListener('pointerdown',e=>{{e.preventDefault();startAudio();input(down)}});x.addEventListener('pointerup',e=>{{e.preventDefault();input(up)}});x.addEventListener('pointercancel',()=>input(up))}}hold('jump',{{type:'key',key:'Space',down:true}},{{type:'key',key:'Space',down:false}});hold('use',{{type:'button',button:'left',down:true}},{{type:'button',button:'left',down:false}});window.addEventListener('blur',()=>{{stopMove();input({{type:'release'}})}});window.addEventListener('beforeunload',()=>{{stopped=true;stopMove();input({{type:'release'}})}});startAudio();refresh();
</script></body></html>""".encode("utf-8")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

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
            self.keys_down.clear(); self.buttons_down.clear(); return
        for key in list(self.keys_down):
            try: send_key(hwnd, key, False)
            except Exception: pass
        for button in list(self.buttons_down):
            try: send_button(hwnd, button, False)
            except Exception: pass
        self.keys_down.clear(); self.buttons_down.clear()


class Handler(BaseHTTPRequestHandler):
    server_version = "RipoTeamRecRoomVM/2.0"

    def log_message(self, _format: str, *_args) -> None: return
    def parsed(self): return urlparse(self.path)
    def authorized(self, parsed) -> bool: return parse_qs(parsed.query).get("token", [""])[0] == self.server.token

    def send_bytes(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(body)

    def send_json(self, status: int, body: dict) -> None: self.send_bytes(status, "application/json", json.dumps(body).encode())

    def do_GET(self) -> None:
        parsed = self.parsed()
        if parsed.path == "/health":
            try:
                return self.send_json(200, {"ok": True, "pid": self.server.pid, "window": self.server.hwnd(), "audio": True, "inputVersion": 4})
            except Exception as exc:
                return self.send_json(503, {"ok": False, "error": str(exc)})
        if not self.authorized(parsed): return self.send_json(401, {"ok": False, "error": "invalid token"})
        if parsed.path in {"/", "/index.html"}: return self.send_bytes(200, "text/html; charset=utf-8", html_page(self.server.token))
        if parsed.path == "/frame.jpg":
            try:
                with self.server.lock: body = capture_jpeg(self.server.hwnd(), self.server.max_width, self.server.quality)
                return self.send_bytes(200, "image/jpeg", body)
            except Exception as exc: return self.send_json(503, {"ok": False, "error": str(exc)})
        if parsed.path == "/audio.pcm":
            try:
                speaker = sc.default_speaker()
                if speaker is None: raise RuntimeError("Windows guest has no default audio output device.")
                loopback = sc.get_microphone(speaker.name, include_loopback=True)
                self.send_response(200); self.send_header("Content-Type", "application/octet-stream"); self.send_header("Cache-Control", "no-store"); self.send_header("X-Audio-Format", "s16le;rate=48000;channels=2"); self.end_headers()
                with loopback.recorder(samplerate=48000, channels=2, blocksize=2048) as recorder:
                    while True:
                        data = recorder.record(numframes=2048)
                        pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype("<i2", copy=False).tobytes()
                        self.wfile.write(pcm); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError): pass
            except Exception as exc:
                try: self.send_json(503, {"ok": False, "error": str(exc)})
                except Exception: pass
            return
        return self.send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        parsed = self.parsed()
        if parsed.path != "/input": return self.send_json(404, {"ok": False, "error": "not found"})
        if not self.authorized(parsed): return self.send_json(401, {"ok": False, "error": "invalid token"})
        try:
            length = min(8192, int(self.headers.get("content-length", "0") or 0)); data = json.loads(self.rfile.read(length) or b"{}"); hwnd = self.server.hwnd(); kind = str(data.get("type") or "")
            if kind == "key":
                key, down = str(data.get("key") or ""), bool(data.get("down")); send_key(hwnd, key, down); self.server.keys_down.add(key) if down else self.server.keys_down.discard(key)
            elif kind == "button":
                button, down = str(data.get("button") or "left"), bool(data.get("down")); x, y = data.get("x"), data.get("y"); send_button(hwnd, button, down, float(x) if x is not None else None, float(y) if y is not None else None); self.server.buttons_down.add(button) if down else self.server.buttons_down.discard(button)
            elif kind == "move": send_move(hwnd, int(data.get("dx", 0)), int(data.get("dy", 0)))
            elif kind == "wheel": focus(hwnd); user32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, max(-10, min(10, int(data.get("delta", 0)))) * WHEEL_DELTA, 0)
            elif kind == "release": self.server.release_inputs()
            else: return self.send_json(400, {"ok": False, "error": "unsupported input"})
            return self.send_json(200, {"ok": True})
        except Exception as exc: return self.send_json(400, {"ok": False, "error": str(exc)})


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--pid", type=int, required=True); parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, default=6081); parser.add_argument("--token", default=""); parser.add_argument("--max-width", type=int, default=1280); parser.add_argument("--quality", type=int, default=75); args = parser.parse_args()
    token = args.token or secrets.token_urlsafe(32)
    server = Server((args.host, args.port), Handler, pid=args.pid, token=token, max_width=max(640, args.max_width), quality=min(92, max(35, args.quality)))
    print(json.dumps({"ok": True, "host": args.host, "port": args.port, "pid": args.pid, "audio": True, "inputVersion": 4}), flush=True)
    try: server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt: pass
    finally: server.release_inputs(); server.server_close()
    return 0


if __name__ == "__main__": raise SystemExit(main())

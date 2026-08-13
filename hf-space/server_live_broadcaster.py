from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response


class ServerLiveBroadcaster:
    """Drive TikTok LIVE Producer in the server Firefox and stream with FFmpeg.

    TikTok credentials are captured only in memory from the already-authenticated
    server browser session. They are never returned to the dashboard or written
    to GitHub/disk.
    """

    def __init__(self, ai: Any, connector: Any, data_dir: Path, authorize: Callable[[str | None], None], display: str) -> None:
        self.ai = ai
        self.connector = connector
        self.data_dir = data_dir
        self.authorize = authorize
        self.display = display
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.phase = "stopped"
        self.last_error = ""
        self.last_browser_status = ""
        self.started_at: float | None = None
        self.nonce = ""
        self.capture: dict[str, str] = {}
        self.ffmpeg: subprocess.Popen[Any] | None = None
        self.worker: threading.Thread | None = None
        self.audio_worker: threading.Thread | None = None
        self.stop_flag = threading.Event()
        self.fifo = self.data_dir / "ripo-live-audio.pcm"
        self.space_origin = os.environ.get("RIPO_SPACE_ORIGIN", "https://echoxr-ripoteam-cloud-pc.hf.space").rstrip("/")

    def _control_auth(self, token: str | None) -> None:
        if self.ai.session_valid(token):
            return
        self.authorize(token)

    def status(self) -> dict[str, Any]:
        proc_running = bool(self.ffmpeg and self.ffmpeg.poll() is None)
        return {
            "ok": True,
            "phase": self.phase,
            "running": proc_running,
            "started_at": self.started_at,
            "last_error": self.last_error,
            "browser_status": self.last_browser_status,
            "server_browser": self.connector.status(),
            "ai_running": bool(self.ai.running),
            "ai_connected": bool(self.ai.connected),
            "room_id": self.ai.room_id,
            "like_total": self.ai.like_total,
        }

    def _desktop_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["DISPLAY"] = self.display
        env.setdefault("HOME", str(Path.home()))
        env.setdefault("USER", Path.home().name)
        runtime_dir = Path(f"/tmp/ripo-runtime-{os.getuid()}")
        runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        env.setdefault("XDG_RUNTIME_DIR", str(runtime_dir))
        return env

    def _window(self) -> str:
        xdotool = shutil.which("xdotool")
        if not xdotool:
            raise RuntimeError("xdotool is missing from the server computer.")
        candidates: list[str] = []
        for query in (("--class", "firefox"), ("--name", "TikTok"), ("--name", "Firefox")):
            try:
                out = subprocess.check_output([xdotool, "search", "--onlyvisible", *query], env=self._desktop_env(), timeout=5, text=True)
                candidates.extend(line.strip() for line in out.splitlines() if line.strip())
            except Exception:
                pass
        if not candidates:
            raise RuntimeError("The TikTok server Firefox window is not open. Press Connect TikTok first.")
        return candidates[-1]

    def _key(self, window: str, key: str) -> None:
        subprocess.run(["xdotool", "key", "--window", window, "--clearmodifiers", key], env=self._desktop_env(), check=True, timeout=8)

    def _type(self, window: str, text: str) -> None:
        subprocess.run(["xdotool", "type", "--window", window, "--clearmodifiers", "--delay", "0", "--", text], env=self._desktop_env(), check=True, timeout=25)

    def _navigate(self, url: str) -> None:
        window = self._window()
        subprocess.run(["xdotool", "windowactivate", "--sync", window], env=self._desktop_env(), check=True, timeout=8)
        self._key(window, "ctrl+l")
        self._type(window, url)
        self._key(window, "Return")

    def _bookmarklet(self) -> str:
        endpoint = self.space_origin + "/api/tiktok/server-live/capture"
        nonce = self.nonce
        # Re-injected repeatedly so it survives SPA/full-page transitions.
        js = r'''javascript:(()=>{try{const N='__NONCE__',U='__ENDPOINT__',n=s=>(s||'').replace(/\s+/g,' ').trim(),vis=e=>!!(e&&e.getClientRects().length),txt=e=>n(e.innerText||e.textContent||e.getAttribute?.('aria-label')||''),setv=(e,v)=>{try{const p=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(e),'value')||Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value');p&&p.set?p.set.call(e,v):e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}))}catch(_){e.value=v}};const els=[...document.querySelectorAll('input,textarea')],btn=[...document.querySelectorAll('button,[role=button]')].filter(vis);let actions=[];for(const e of els){const c=n((e.getAttribute('placeholder')||'')+' '+(e.getAttribute('aria-label')||'')+' '+(e.name||'')+' '+(e.closest('label')?.innerText||'')+' '+(e.parentElement?.innerText||'')).toLowerCase();if((/title|live name|topic/.test(c))&&!e.value){setv(e,'Ripo Bot LIVE');actions.push('title')}}const exactGo=btn.find(e=>/^go live$/i.test(txt(e)));if(exactGo){exactGo.click();actions.push('go-live')}const cat=btn.find(e=>/select category|choose category|category/i.test(txt(e)));if(cat){cat.click();actions.push('category');setTimeout(()=>{const o=[...document.querySelectorAll('[role=option],li')].find(vis);if(o)o.click()},350)}const save=btn.find(e=>/save\s*(?:&|and)?\s*go live|save.*live/i.test(txt(e)));if(save&&!save.disabled){save.click();actions.push('save-go-live')}let server='',key='';for(const e of els){const v=n(e.value||'');const c=n((e.getAttribute('aria-label')||'')+' '+(e.getAttribute('placeholder')||'')+' '+(e.parentElement?.innerText||'')).toLowerCase();if(/^rtmps?:\/\//i.test(v))server=v;if(!key&&v.length>12&&(/stream key|streamkey/.test(c)||e.type==='password'))key=v}if(!server){const m=(document.body?.innerText||'').match(/rtmps?:\/\/[^\s]+/i);if(m)server=m[0]}let username='';for(const a of document.querySelectorAll('a[href*="/@"]')){const m=(a.getAttribute('href')||'').match(/\/@([A-Za-z0-9._]{2,32})/);if(m){username=m[1];break}}const buttons=btn.slice(0,60).map(txt).filter(Boolean).slice(0,30);fetch(U,{method:'POST',mode:'no-cors',headers:{'Content-Type':'text/plain'},body:JSON.stringify({nonce:N,server,key,username,url:location.href,buttons,actions})}).catch(()=>{})}catch(e){fetch('__ENDPOINT__',{method:'POST',mode:'no-cors',headers:{'Content-Type':'text/plain'},body:JSON.stringify({nonce:'__NONCE__',error:String(e)})}).catch(()=>{})}})()'''
        return js.replace("__NONCE__", nonce).replace("__ENDPOINT__", endpoint)

    def _inject(self) -> None:
        window = self._window()
        subprocess.run(["xdotool", "windowactivate", "--sync", window], env=self._desktop_env(), check=True, timeout=8)
        self._key(window, "ctrl+l")
        self._type(window, self._bookmarklet())
        self._key(window, "Return")

    def start(self) -> dict[str, Any]:
        with self.lock:
            if self.worker and self.worker.is_alive():
                return {"ok": True, "message": "Server LIVE start is already running.", **self.status()}
            if self.ffmpeg and self.ffmpeg.poll() is None:
                return {"ok": True, "message": "TikTok LIVE is already streaming from the Ripo server.", **self.status()}
            self.stop_flag.clear()
            self.phase = "opening-live-producer"
            self.last_error = ""
            self.last_browser_status = ""
            self.started_at = time.time()
            self.capture = {}
            self.nonce = secrets.token_urlsafe(24)
            self.worker = threading.Thread(target=self._start_worker, daemon=True, name="ripo-server-live-start")
            self.worker.start()
        return {"ok": True, "message": "Opening TikTok LIVE Producer on the Ripo server computer.", **self.status()}

    def _start_worker(self) -> None:
        try:
            if not self.connector.status().get("browser_running"):
                raise RuntimeError("TikTok is not connected to the server browser. Press Connect TikTok first.")
            self._navigate("https://livecenter.tiktok.com/producer")
            deadline = time.time() + 95
            self.phase = "automating-live-producer"
            time.sleep(4)
            while time.time() < deadline and not self.stop_flag.is_set():
                try:
                    self._inject()
                except Exception as exc:
                    self.last_browser_status = f"Browser automation retry: {exc}"
                with self.lock:
                    server = self.capture.get("server", "")
                    key = self.capture.get("key", "")
                    username = self.capture.get("username", "")
                if username:
                    try:
                        self.ai.settings["unique_id"] = username
                        self.ai.save()
                    except Exception:
                        pass
                if server and key:
                    self.phase = "starting-encoder"
                    self._start_ffmpeg(server, key)
                    self.phase = "live"
                    if not self.ai.running and self.ai.settings.get("unique_id"):
                        try:
                            self.ai.start(0)
                        except Exception as exc:
                            self.last_error = f"LIVE started, but AI event connection failed to start: {exc}"
                    return
                time.sleep(2.2)
            if not self.stop_flag.is_set():
                raise RuntimeError("TikTok LIVE Producer did not expose streaming credentials. The account may not have browser/RTMP LIVE Producer access, or TikTok changed the page flow.")
        except Exception as exc:
            self.last_error = str(exc)
            self.phase = "error"

    def capture_from_browser(self, payload: dict[str, Any]) -> None:
        if str(payload.get("nonce") or "") != self.nonce or not self.nonce:
            return
        server = str(payload.get("server") or "").strip()
        key = str(payload.get("key") or "").strip()
        username = str(payload.get("username") or "").strip().lstrip("@")
        buttons = payload.get("buttons") if isinstance(payload.get("buttons"), list) else []
        actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
        with self.lock:
            if server.startswith(("rtmp://", "rtmps://")):
                self.capture["server"] = server
            if key and len(key) > 12:
                self.capture["key"] = key
            if username and len(username) <= 32:
                self.capture["username"] = username
            self.last_browser_status = " · ".join([*(str(x) for x in actions[-5:]), *(str(x) for x in buttons[:6])])[:500]

    def _start_ffmpeg(self, server: str, key: str) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("FFmpeg is missing from the Ripo server.")
        try:
            if self.fifo.exists() or self.fifo.is_symlink():
                self.fifo.unlink()
            os.mkfifo(self.fifo, 0o600)
        except FileExistsError:
            pass
        target = server.rstrip("/") + "/" + key.lstrip("/")
        log = (self.data_dir / "ffmpeg-live.log").open("ab", buffering=0)
        self.ffmpeg = subprocess.Popen(
            [
                ffmpeg, "-hide_banner", "-loglevel", "warning",
                "-re", "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30",
                "-f", "s16le", "-ar", "22050", "-ac", "1", "-i", str(self.fifo),
                "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                "-pix_fmt", "yuv420p", "-g", "60", "-b:v", "2400k", "-maxrate", "2800k", "-bufsize", "4800k",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                "-f", "flv", target,
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.audio_worker = threading.Thread(target=self._audio_loop, daemon=True, name="ripo-live-audio")
        self.audio_worker.start()
        time.sleep(2)
        if self.ffmpeg.poll() is not None:
            raise RuntimeError("FFmpeg exited while starting the TikTok broadcast. Check TikTok LIVE access and encoder credentials.")

    def _audio_loop(self) -> None:
        chunk_frames = 441  # 20 ms at 22050 Hz
        silence = b"\x00\x00" * chunk_frames
        try:
            with self.fifo.open("wb", buffering=0) as out:
                while not self.stop_flag.is_set() and self.ffmpeg and self.ffmpeg.poll() is None:
                    row = self.ai.pop_audio()
                    if row and Path(str(row.get("path") or "")).exists():
                        try:
                            converted = subprocess.run(
                                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(row["path"]), "-f", "s16le", "-ar", "22050", "-ac", "1", "pipe:1"],
                                capture_output=True, timeout=30, check=True,
                            ).stdout
                            step = chunk_frames * 2
                            for i in range(0, len(converted), step):
                                if self.stop_flag.is_set():
                                    break
                                part = converted[i:i + step]
                                if len(part) < step:
                                    part += b"\x00" * (step - len(part))
                                out.write(part)
                                time.sleep(0.02)
                        except Exception:
                            pass
                    else:
                        out.write(silence)
                        time.sleep(0.02)
        except Exception as exc:
            if not self.stop_flag.is_set():
                self.last_error = f"Audio output stopped: {exc}"

    def stop(self) -> dict[str, Any]:
        self.stop_flag.set()
        if self.ffmpeg and self.ffmpeg.poll() is None:
            try:
                self.ffmpeg.terminate()
                self.ffmpeg.wait(timeout=8)
            except Exception:
                try:
                    self.ffmpeg.kill()
                except Exception:
                    pass
        self.ffmpeg = None
        try:
            self.ai.stop()
        except Exception:
            pass
        try:
            if self.connector.status().get("browser_running"):
                window = self._window()
                self._key(window, "ctrl+l")
                end_js = "javascript:(()=>{const b=[...document.querySelectorAll('button,[role=button]')].find(e=>/end live|stop live/i.test((e.innerText||e.textContent||'')));if(b)b.click()})()"
                self._type(window, end_js)
                self._key(window, "Return")
        except Exception:
            pass
        self.phase = "stopped"
        self.started_at = None
        self.capture = {}
        self.nonce = ""
        try:
            self.fifo.unlink(missing_ok=True)
        except Exception:
            pass
        return {"ok": True, "message": "Server TikTok LIVE stopped.", **self.status()}


def install_server_live_routes(app: Any, broadcaster: ServerLiveBroadcaster) -> None:
    @app.get("/api/tiktok/server-live/status")
    async def server_live_status() -> JSONResponse:
        return JSONResponse(broadcaster.status())

    @app.post("/api/tiktok/server-live/start")
    async def server_live_start(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        broadcaster._control_auth(x_admin_token)
        return JSONResponse(broadcaster.start())

    @app.post("/api/tiktok/server-live/stop")
    async def server_live_stop(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        broadcaster._control_auth(x_admin_token)
        return JSONResponse(broadcaster.stop())

    @app.post("/api/tiktok/server-live/capture")
    async def server_live_capture(request: Request) -> Response:
        raw = await request.body()
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except Exception:
            return Response(status_code=204)
        if isinstance(payload, dict):
            broadcaster.capture_from_browser(payload)
        return Response(status_code=204)

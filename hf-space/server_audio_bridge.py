from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse


class ServerAudioBridge:
    """Mirror Ripo Bot speech into a PulseAudio null sink.

    LIVE Studio sees the null sink monitor as the default input device, so TTS
    can stay audible even when the user's phone/browser is closed.
    """

    SINK = "RipoBotMic"

    def __init__(self, ai: Any) -> None:
        self.ai = ai
        self.paths: queue.Queue[str] = queue.Queue(maxsize=100)
        self.started = False
        self.ready = False
        self.last_error = ""
        self.last_played_at: float | None = None
        self.played = 0
        self.thread = threading.Thread(target=self._worker, name="ripo-server-audio", daemon=True)
        self._wrap_speech()
        self.thread.start()

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        runtime = Path(f"/tmp/ripo-runtime-{os.getuid()}")
        runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        env.setdefault("XDG_RUNTIME_DIR", str(runtime))
        env.setdefault("HOME", str(Path.home()))
        env.setdefault("USER", Path.home().name)
        return env

    def _run(self, cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(cmd, env=self._env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)

    def _ensure_pulse(self) -> None:
        if not shutil.which("pulseaudio") or not shutil.which("pactl") or not shutil.which("paplay"):
            raise RuntimeError("PulseAudio tools are not installed yet.")

        self._run(["pulseaudio", "--start", "--exit-idle-time=-1"], 20)
        deadline = time.time() + 15
        info = b""
        while time.time() < deadline:
            result = self._run(["pactl", "info"], 5)
            if result.returncode == 0:
                info = result.stdout
                break
            time.sleep(0.5)
        if not info:
            raise RuntimeError("PulseAudio server did not become ready.")

        sinks = self._run(["pactl", "list", "short", "sinks"], 8)
        sink_text = sinks.stdout.decode("utf-8", errors="ignore")
        if self.SINK not in sink_text:
            loaded = self._run(
                [
                    "pactl", "load-module", "module-null-sink",
                    f"sink_name={self.SINK}",
                    "sink_properties=device.description=RipoBot_Virtual_Microphone",
                ],
                10,
            )
            if loaded.returncode != 0:
                raise RuntimeError("Could not create Ripo Bot virtual audio sink: " + loaded.stderr.decode(errors="ignore")[-500:])

        source = f"{self.SINK}.monitor"
        default = self._run(["pactl", "set-default-source", source], 8)
        if default.returncode != 0:
            raise RuntimeError("Could not set Ripo Bot monitor as the default microphone source.")
        self.ready = True
        self.last_error = ""

    def _wrap_speech(self) -> None:
        original = self.ai.speak
        bridge = self

        def wrapped(text: str, kind: str = "speech", priority: int = 5, user: str = "", force: bool = False) -> bool:
            before = {str(row.get("id")) for row in list(bridge.ai.audio)}
            ok = original(text, kind, priority, user, force)
            if not ok:
                return ok
            newest = None
            for row in reversed(list(bridge.ai.audio)):
                if str(row.get("id")) not in before:
                    newest = row
                    break
            path = str((newest or {}).get("path") or "")
            if path:
                try:
                    bridge.paths.put_nowait(path)
                except queue.Full:
                    try:
                        bridge.paths.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        bridge.paths.put_nowait(path)
                    except queue.Full:
                        pass
            return ok

        self.ai.speak = wrapped

    def _worker(self) -> None:
        self.started = True
        while True:
            try:
                if not self.ready:
                    self._ensure_pulse()
            except Exception as exc:
                self.ready = False
                self.last_error = str(exc)[:1200]
                time.sleep(5)
                continue

            try:
                path = self.paths.get(timeout=2)
            except queue.Empty:
                continue
            try:
                file = Path(path)
                if not file.exists():
                    continue
                result = self._run(["paplay", f"--device={self.SINK}", str(file)], 90)
                if result.returncode != 0:
                    raise RuntimeError(result.stderr.decode("utf-8", errors="ignore")[-600:] or "paplay failed")
                self.played += 1
                self.last_played_at = time.time()
                self.last_error = ""
            except Exception as exc:
                self.last_error = f"Server TTS playback: {exc}"[:1200]
                self.ready = False

    def status(self) -> dict[str, Any]:
        source = f"{self.SINK}.monitor"
        return {
            "ok": True,
            "started": self.started,
            "ready": self.ready,
            "sink": self.SINK,
            "virtual_microphone": source,
            "queued": self.paths.qsize(),
            "played": self.played,
            "last_played_at": self.last_played_at,
            "last_error": self.last_error,
            "pulseaudio_installed": bool(shutil.which("pulseaudio")),
            "paplay_installed": bool(shutil.which("paplay")),
        }


def install_server_audio_routes(app: Any, bridge: ServerAudioBridge) -> None:
    @app.get("/api/tiktok/server-audio/status")
    async def server_audio_status() -> JSONResponse:
        return JSONResponse(bridge.status())

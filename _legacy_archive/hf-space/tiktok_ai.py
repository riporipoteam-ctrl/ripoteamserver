from __future__ import annotations

import asyncio
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

import httpx
from fastapi import Body, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

OLLAMA_API = os.environ.get("OLLAMA_API", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.environ.get("RIPO_AI_MODEL", "qwen3:4b-instruct")
USERNAME = re.compile(r"^[A-Za-z0-9._]{2,32}$")

# Client keys and redirect URIs are public OAuth configuration. The client
# secret is intentionally read only from the server environment.
DEFAULT_TIKTOK_CLIENT_KEY = "awx4azk8o5ac8zow"
DEFAULT_TIKTOK_REDIRECT_URI = "https://echoxr-ripoteam-cloud-pc.hf.space/api/tiktok/oauth/callback"
DEFAULT_TIKTOK_SCOPES = "user.info.basic,user.info.profile"
DEFAULT_PUBLIC_ORIGIN = "https://riporipoteam-ctrl.github.io"

VOICES = {
    "robot": {"label": "Ripo Robot", "voice": "en-us", "speed": 150, "pitch": 36},
    "deep": {"label": "Ripo Robot Deep", "voice": "en-us", "speed": 132, "pitch": 24},
    "cyber": {"label": "Ripo Cyber", "voice": "en-us", "speed": 180, "pitch": 44},
    "announcer": {"label": "Ripo Announcer", "voice": "en-gb", "speed": 148, "pitch": 38},
    "bright": {"label": "Ripo Bright", "voice": "en-us", "speed": 160, "pitch": 58},
}

DEFAULTS = {
    "unique_id": "",
    "voice": "deep",
    "duration_minutes": 60,
    "welcome_enabled": True,
    "likes_enabled": True,
    "like_milestone": 50,
    "gifts_enabled": True,
    "shares_enabled": True,
    "follows_enabled": True,
    "questions_enabled": True,
    "random_enabled": True,
    "random_interval": 180,
    "guest_audio_enabled": True,
    "max_lines_per_minute": 12,
    "user_cooldown": 8,
    "personality": "You are Ripo Bot, a fun robot TikTok LIVE co-host. Keep spoken replies friendly, safe, playful and under two short sentences.",
}


def clean(value: Any, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def normalize_user(value: str) -> str:
    value = value.strip()
    if "/@" in value:
        value = value.split("/@", 1)[1].split("/", 1)[0].split("?", 1)[0]
    value = value.removeprefix("@").strip()
    if not USERNAME.fullmatch(value):
        raise ValueError("Enter a valid TikTok @username.")
    return value


class TikTokAI:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.audio_dir = data_dir / "speech"
        self.settings_file = data_dir / "settings.json"
        self.oauth_file = data_dir / "oauth.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.audio_dir.mkdir(parents=True, exist_ok=True)

        self.settings = dict(DEFAULTS)
        try:
            saved = json.loads(self.settings_file.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                self.settings.update({key: saved[key] for key in DEFAULTS if key in saved})
        except Exception:
            pass

        self.oauth_profile: dict[str, Any] = {}
        try:
            saved_oauth = json.loads(self.oauth_file.read_text(encoding="utf-8"))
            if isinstance(saved_oauth, dict):
                self.oauth_profile = {
                    key: saved_oauth.get(key)
                    for key in ("open_id", "display_name", "username", "avatar_url", "profile_deep_link")
                }
        except Exception:
            pass

        self.running = False
        self.connected = False
        self.started_at: float | None = None
        self.ends_at: float | None = None
        self.room_id = ""
        self.last_error = ""
        self.like_total = 0
        self.next_like = int(self.settings["like_milestone"])
        self.events: deque[dict[str, Any]] = deque(maxlen=300)
        self.event_id = 0
        self.audio: deque[dict[str, Any]] = deque(maxlen=80)
        self.speech_times: deque[float] = deque(maxlen=200)
        self.user_times: dict[str, float] = {}
        self.last_welcome = 0.0
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.client: Any = None
        self.stop_flag = threading.Event()
        self.whisper: Any = None
        self.whisper_lock = threading.Lock()

        self.oauth_states: dict[str, dict[str, Any]] = {}
        self.oauth_sessions: dict[str, dict[str, Any]] = {}
        self.client_key = os.environ.get("TIKTOK_CLIENT_KEY", DEFAULT_TIKTOK_CLIENT_KEY).strip()
        self.client_secret = os.environ.get("TIKTOK_CLIENT_SECRET", "").strip()
        self.redirect_uri = os.environ.get("TIKTOK_REDIRECT_URI", DEFAULT_TIKTOK_REDIRECT_URI).strip()
        self.scopes = os.environ.get("TIKTOK_SCOPES", DEFAULT_TIKTOK_SCOPES).strip()
        self.public_origin = os.environ.get("RIPO_PUBLIC_ORIGIN", DEFAULT_PUBLIC_ORIGIN).rstrip("/")

    def save(self) -> None:
        self.settings_file.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")

    def record(self, kind: str, message: str, **extra: Any) -> None:
        self.event_id += 1
        self.events.append(
            {
                "id": self.event_id,
                "time": time.time(),
                "kind": kind,
                "message": clean(message),
                **extra,
            }
        )

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "running": self.running,
            "connected": self.connected,
            "started_at": self.started_at,
            "ends_at": self.ends_at,
            "remaining_seconds": max(0, int(self.ends_at - time.time())) if self.running and self.ends_at else None,
            "room_id": self.room_id,
            "last_error": self.last_error,
            "unique_id": self.settings.get("unique_id", ""),
            "oauth_configured": bool(self.client_key and self.client_secret and self.redirect_uri),
            "oauth_account": {
                key: value
                for key, value in self.oauth_profile.items()
                if value and key != "open_id"
            },
            "audio_queue": len(self.audio),
            "like_total": self.like_total,
            "tts_ready": bool(shutil.which("espeak-ng")),
        }

    def public_settings(self) -> dict[str, Any]:
        return {
            "ok": True,
            "settings": self.settings,
            "voices": [{"id": key, "label": value["label"]} for key, value in VOICES.items()],
        }

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        for key in DEFAULTS:
            if key not in payload:
                continue
            value = payload[key]
            if key == "unique_id":
                value = normalize_user(str(value)) if str(value).strip() else ""
            elif key == "voice":
                if value not in VOICES:
                    raise ValueError("Unknown voice.")
            elif key in {"duration_minutes", "like_milestone", "random_interval", "max_lines_per_minute", "user_cooldown"}:
                value = int(value)
            elif key.endswith("_enabled") or key == "random_enabled":
                value = bool(value)
            else:
                value = clean(value, 1600)
            self.settings[key] = value

        self.settings["duration_minutes"] = max(0, min(1440, int(self.settings["duration_minutes"])))
        self.settings["like_milestone"] = max(10, min(100000, int(self.settings["like_milestone"])))
        self.settings["random_interval"] = max(30, min(3600, int(self.settings["random_interval"])))
        self.settings["max_lines_per_minute"] = max(1, min(60, int(self.settings["max_lines_per_minute"])))
        self.settings["user_cooldown"] = max(0, min(300, int(self.settings["user_cooldown"])))
        self.next_like = ((self.like_total // self.settings["like_milestone"]) + 1) * self.settings["like_milestone"]
        self.save()
        return self.public_settings()

    def allowed(self, user: str = "") -> bool:
        now = time.time()
        while self.speech_times and now - self.speech_times[0] > 60:
            self.speech_times.popleft()
        if len(self.speech_times) >= self.settings["max_lines_per_minute"]:
            return False
        if user and now - self.user_times.get(user, 0) < self.settings["user_cooldown"]:
            return False
        if user:
            self.user_times[user] = now
        self.speech_times.append(now)
        return True

    def synth(self, text: str, voice_id: str | None = None) -> Path:
        executable = shutil.which("espeak-ng")
        if not executable:
            raise RuntimeError("espeak-ng is not installed.")
        voice = VOICES.get(voice_id or self.settings["voice"], VOICES["deep"])
        path = self.audio_dir / f"{uuid.uuid4().hex}.wav"
        completed = subprocess.run(
            [
                executable,
                "-v", voice["voice"],
                "-s", str(voice["speed"]),
                "-p", str(voice["pitch"]),
                "-a", "175",
                "-w", str(path),
                clean(text, 450),
            ],
            capture_output=True,
            timeout=30,
        )
        if completed.returncode != 0 or not path.exists():
            raise RuntimeError(clean(completed.stderr.decode(errors="replace"), 300) or "Speech failed.")
        return path

    def speak(self, text: str, kind: str = "speech", priority: int = 5, user: str = "", force: bool = False) -> bool:
        text = clean(text, 450)
        if not text or (not force and not self.allowed(user)):
            return False
        try:
            path = self.synth(text)
        except Exception as exc:
            self.last_error = f"TTS: {exc}"
            self.record("error", self.last_error)
            return False
        row = {
            "id": uuid.uuid4().hex,
            "path": str(path),
            "text": text,
            "kind": kind,
            "priority": priority,
            "time": time.time(),
        }
        rows = list(self.audio) + [row]
        rows.sort(key=lambda item: (item["priority"], item["time"]))
        self.audio = deque(rows[:80], maxlen=80)
        self.record(kind, text)
        return True

    def pop_audio(self) -> dict[str, Any] | None:
        return self.audio.popleft() if self.audio else None

    def ai(self, prompt: str, words: int = 55) -> str:
        body = {
            "model": MODEL,
            "stream": False,
            "messages": [
                {"role": "system", "content": self.settings["personality"]},
                {"role": "user", "content": f"{prompt}\nReply for spoken TikTok LIVE audio in under {words} words."},
            ],
            "options": {"temperature": 0.8, "num_predict": 120},
        }
        try:
            response = httpx.post(f"{OLLAMA_API}/api/chat", json=body, timeout=45)
            response.raise_for_status()
            return clean((response.json().get("message") or {}).get("content"), 500)
        except Exception as exc:
            self.last_error = f"AI: {exc}"
            self.record("error", self.last_error)
            return ""

    def preview(self, voice: str) -> Path:
        if voice not in VOICES:
            raise ValueError("Unknown voice.")
        return self.synth("Hello. I am Ripo Bot, your TikTok live co-host.", voice)

    def session_valid(self, token: str | None) -> bool:
        value = clean(token, 300)
        if not value:
            return False
        now = time.time()
        for key, row in list(self.oauth_sessions.items()):
            if float(row.get("expires", 0)) < now:
                self.oauth_sessions.pop(key, None)
        row = self.oauth_sessions.get(value)
        if not row or float(row.get("expires", 0)) < now:
            return False
        owner = clean(self.oauth_profile.get("open_id"), 200)
        return not owner or clean(row.get("open_id"), 200) == owner

    def oauth_start(self, basic: bool = False) -> dict[str, Any]:
        if not self.client_secret:
            raise RuntimeError("TIKTOK_CLIENT_SECRET is not configured in the Hugging Face Space secrets.")
        state = secrets.token_urlsafe(30)
        session_token = secrets.token_urlsafe(42)
        self.oauth_states[state] = {
            "expires": time.time() + 600,
            "session_token": session_token,
        }
        requested_scopes = "user.info.basic" if basic else self.scopes
        url = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(
            {
                "client_key": self.client_key,
                "scope": requested_scopes,
                "response_type": "code",
                "redirect_uri": self.redirect_uri,
                "state": state,
                "disable_auto_auth": 1,
            }
        )
        return {"ok": True, "url": url, "requested_scopes": requested_scopes}

    async def oauth_callback(self, code: str, state: str) -> dict[str, Any]:
        pending = self.oauth_states.pop(state, {})
        if float(pending.get("expires", 0)) < time.time():
            raise ValueError("TikTok login expired. Try again.")
        if not code:
            raise ValueError("TikTok did not return an authorization code.")

        async with httpx.AsyncClient(timeout=25) as client:
            token_response = await client.post(
                "https://open.tiktokapis.com/v2/oauth/token/",
                data={
                    "client_key": self.client_key,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if token_response.is_error:
                raise ValueError(f"TikTok token exchange failed: {clean(token_response.text, 500)}")
            token = token_response.json()
            access = clean(token.get("access_token"), 2400)
            if not access:
                raise ValueError("TikTok did not return an access token.")

            granted = {
                item.strip()
                for item in str(token.get("scope") or "").split(",")
                if item.strip()
            }
            fields = ["open_id", "avatar_url", "display_name"]
            if "user.info.profile" in granted:
                fields.extend(["username", "profile_deep_link"])

            user_response = await client.get(
                "https://open.tiktokapis.com/v2/user/info/",
                params={"fields": ",".join(fields)},
                headers={"Authorization": f"Bearer {access}"},
            )
            if user_response.is_error:
                raise ValueError(f"TikTok profile lookup failed: {clean(user_response.text, 500)}")
            user = ((user_response.json().get("data") or {}).get("user") or {})

        open_id = clean(user.get("open_id") or token.get("open_id"), 200)
        existing_open_id = clean(self.oauth_profile.get("open_id"), 200)
        if existing_open_id and open_id and existing_open_id != open_id:
            raise ValueError("This Ripo server is already linked to a different TikTok account.")

        stored = {
            "open_id": open_id,
            "display_name": clean(user.get("display_name"), 80),
            "username": clean(user.get("username"), 40),
            "avatar_url": clean(user.get("avatar_url"), 500),
            "profile_deep_link": clean(user.get("profile_deep_link"), 500),
            "access_token": access,
            "refresh_token": clean(token.get("refresh_token"), 2400),
            "scope": clean(token.get("scope"), 400),
            "saved_at": time.time(),
        }
        self.oauth_file.write_text(json.dumps(stored), encoding="utf-8")
        self.oauth_profile = {
            key: stored.get(key)
            for key in ("open_id", "display_name", "username", "avatar_url", "profile_deep_link")
        }

        if USERNAME.fullmatch(stored["username"]):
            self.settings["unique_id"] = stored["username"]
            self.save()

        browser_session = clean(pending.get("session_token"), 300)
        if not browser_session:
            raise ValueError("TikTok browser session was lost. Try connecting again.")
        self.oauth_sessions[browser_session] = {
            "expires": time.time() + 86400,
            "open_id": open_id,
        }
        self.record("oauth", f"Connected TikTok account {stored['display_name'] or stored['username'] or 'successfully'}.")
        return {
            "ok": True,
            "display_name": stored["display_name"],
            "username": stored["username"],
            "avatar_url": stored["avatar_url"],
            "unique_id": self.settings.get("unique_id", ""),
            "session_token": browser_session,
            "granted_scopes": sorted(granted),
        }

    def start(self, duration: int | None = None) -> dict[str, Any]:
        if self.running:
            return {"ok": True, "message": "Already running.", **self.status()}
        uid = normalize_user(self.settings.get("unique_id", ""))
        minutes = int(self.settings["duration_minutes"] if duration is None else duration)
        minutes = max(0, min(1440, minutes))
        self.settings["duration_minutes"] = minutes
        self.save()
        self.running = True
        self.connected = False
        self.started_at = time.time()
        self.ends_at = self.started_at + minutes * 60 if minutes else None
        self.last_error = ""
        self.stop_flag.clear()
        self.thread = threading.Thread(target=self._thread_main, daemon=True, name="ripo-tiktok-ai")
        self.thread.start()
        self.record("session", f"Starting AI host for @{uid}.")
        return {"ok": True, "message": "TikTok AI host is starting.", **self.status()}

    def stop(self) -> dict[str, Any]:
        self.stop_flag.set()
        if self.loop and self.client:
            try:
                asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.loop).result(8)
            except Exception:
                pass
        self.running = False
        self.connected = False
        self.ends_at = None
        self.record("session", "TikTok AI host stopped.")
        return {"ok": True, "message": "Stopped."}

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._live())
        except Exception as exc:
            self.last_error = f"TikTok LIVE: {exc}"
            self.record("error", self.last_error)
        finally:
            self.running = False
            self.connected = False
            self.client = None
            self.loop = None
            self.ends_at = None

    async def _live(self) -> None:
        try:
            from TikTokLive import TikTokLiveClient
            from TikTokLive.events import CommentEvent, ConnectEvent, DisconnectEvent, FollowEvent, GiftEvent, JoinEvent, LikeEvent, LiveEndEvent, ShareEvent
        except ImportError as exc:
            raise RuntimeError("TikTokLive is not installed.") from exc

        uid = normalize_user(self.settings["unique_id"])
        client = TikTokLiveClient(unique_id=f"@{uid}")
        self.client = client
        self.loop = asyncio.get_running_loop()

        async def connected(_event: Any) -> None:
            self.connected = True
            self.room_id = str(getattr(client, "room_id", "") or "")
            self.record("connect", f"Connected to @{uid} LIVE.")
            self.speak("Ripo Bot is online. Chat with me and ask me questions!", "startup", 2, force=True)

        async def disconnected(_event: Any) -> None:
            self.connected = False
            self.record("disconnect", "Disconnected from LIVE.")

        async def ended(_event: Any) -> None:
            self.stop_flag.set()
            self.record("live-end", "The TikTok LIVE ended.")

        async def comment(event: Any) -> None:
            user = getattr(event, "user", None)
            username = clean(getattr(user, "unique_id", ""), 40)
            name = clean(getattr(user, "nickname", ""), 80) or username
            text = clean(getattr(event, "comment", ""), 450)
            if not text:
                return
            self.record("comment", f"{name}: {text}")
            if self.settings["questions_enabled"] and ("?" in text or "ripo bot" in text.lower() or text.lower().startswith("ai ")) and self.allowed(username):
                answer = await asyncio.to_thread(self.ai, f"Viewer {name} said: {text}. Answer them naturally.")
                if answer:
                    self.speak(f"{name}, {answer}", "answer", 1, force=True)

        async def join(event: Any) -> None:
            if not self.settings["welcome_enabled"] or time.time() - self.last_welcome < 18:
                return
            user = getattr(event, "user", None)
            name = clean(getattr(user, "nickname", ""), 80) or clean(getattr(user, "unique_id", ""), 40)
            if name:
                self.last_welcome = time.time()
                self.speak(f"Welcome {name}! Glad you joined.", "welcome", 7)

        async def like(event: Any) -> None:
            try:
                self.like_total += max(1, int(getattr(event, "count", 1) or 1))
            except Exception:
                self.like_total += 1
            if self.settings["likes_enabled"] and self.like_total >= self.next_like:
                reached = self.next_like
                self.next_like += self.settings["like_milestone"]
                self.speak(f"Thank you chat! We just passed {reached} likes!", "likes", 6)

        async def gift(event: Any) -> None:
            if not self.settings["gifts_enabled"] or bool(getattr(event, "streaking", False)):
                return
            user = getattr(event, "user", None)
            name = clean(getattr(user, "nickname", ""), 80) or clean(getattr(user, "unique_id", ""), 40)
            gift_obj = getattr(event, "gift", None)
            gift_name = clean(getattr(gift_obj, "name", "") or "gift", 80)
            self.speak(f"Thank you {name} for the {gift_name}! That's awesome!", "gift", 0, force=True)

        async def share(event: Any) -> None:
            if self.settings["shares_enabled"]:
                user = getattr(event, "user", None)
                name = clean(getattr(user, "nickname", ""), 80) or clean(getattr(user, "unique_id", ""), 40)
                self.speak(f"Thank you {name} for sharing the live!", "share", 3)

        async def follow(event: Any) -> None:
            if self.settings["follows_enabled"]:
                user = getattr(event, "user", None)
                name = clean(getattr(user, "nickname", ""), 80) or clean(getattr(user, "unique_id", ""), 40)
                self.speak(f"Welcome to the team, {name}! Thanks for the follow!", "follow", 2)

        for event_type, handler in [
            (ConnectEvent, connected),
            (DisconnectEvent, disconnected),
            (LiveEndEvent, ended),
            (CommentEvent, comment),
            (JoinEvent, join),
            (LikeEvent, like),
            (GiftEvent, gift),
            (ShareEvent, share),
            (FollowEvent, follow),
        ]:
            client.add_listener(event_type, handler)

        async def chatter() -> None:
            while not self.stop_flag.is_set():
                await asyncio.sleep(max(30, self.settings["random_interval"]))
                if self.settings["random_enabled"] and self.allowed():
                    line = await asyncio.to_thread(self.ai, "Say one spontaneous fun line to keep the livestream lively. Do not pretend a gift or like happened.", 40)
                    if line:
                        self.speak(line, "random", 9, force=True)

        async def timer() -> None:
            while not self.stop_flag.is_set():
                if self.ends_at and time.time() >= self.ends_at:
                    self.record("session", "AI host duration finished.")
                    await client.disconnect()
                    return
                await asyncio.sleep(2)

        tasks = [asyncio.create_task(chatter()), asyncio.create_task(timer())]
        try:
            await client.connect(fetch_room_info=True, fetch_gift_info=False)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _load_whisper(self) -> Any:
        with self.whisper_lock:
            if self.whisper is None:
                from faster_whisper import WhisperModel
                self.whisper = WhisperModel(
                    os.environ.get("RIPO_STT_MODEL", "tiny.en"),
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=max(1, min(4, os.cpu_count() or 2)),
                )
            return self.whisper

    def transcribe_guest(self, path: Path) -> dict[str, Any]:
        if not self.settings["guest_audio_enabled"]:
            return {"ok": True, "transcript": "", "queued": False}
        segments, _ = self._load_whisper().transcribe(
            str(path),
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        transcript = clean(" ".join(segment.text for segment in segments), 1000)
        if not transcript:
            return {"ok": True, "transcript": "", "queued": False}
        self.record("guest", transcript)
        reply = self.ai(f"A LIVE guest said aloud: {transcript}. Reply directly to the guest.")
        queued = bool(reply) and self.speak(reply, "guest-reply", 1)
        return {"ok": True, "transcript": transcript, "reply": reply, "queued": queued}


def install_tiktok_routes(app: Any, ai: TikTokAI, authorize: Callable[[str | None], None]) -> None:
    def control_auth(browser_or_admin_token: str | None) -> None:
        if ai.session_valid(browser_or_admin_token):
            return
        try:
            authorize(browser_or_admin_token)
        except HTTPException as exc:
            raise HTTPException(401, "Connect your TikTok account first.") from exc

    @app.get("/api/tiktok/status")
    async def status() -> JSONResponse:
        return JSONResponse(ai.status())

    @app.get("/api/tiktok/settings")
    async def settings() -> JSONResponse:
        return JSONResponse(ai.public_settings())

    @app.post("/api/tiktok/settings")
    async def update(payload: dict[str, Any] = Body(default_factory=dict), x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        control_auth(x_admin_token)
        try:
            return JSONResponse(ai.update_settings(payload))
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/tiktok/account")
    async def account(payload: dict[str, Any] = Body(default_factory=dict), x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        control_auth(x_admin_token)
        uid = normalize_user(str(payload.get("unique_id", "")))
        ai.settings["unique_id"] = uid
        ai.save()
        ai.record("account", f"Selected @{uid}.")
        return JSONResponse({"ok": True, "unique_id": uid})

    @app.get("/api/tiktok/oauth/start")
    async def oauth_start(basic: int = 0) -> JSONResponse:
        try:
            return JSONResponse(ai.oauth_start(bool(basic)))
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/tiktok/oauth/callback")
    async def oauth_callback(code: str = "", state: str = "", error: str = "", error_description: str = "") -> HTMLResponse:
        try:
            result = {"ok": False, "message": clean(error_description or error, 300)} if error else await ai.oauth_callback(code, state)
        except Exception as exc:
            result = {"ok": False, "message": clean(exc, 600)}
        message = html.escape(clean(result.get("message") or ("TikTok connected." if result.get("ok") else "TikTok connection failed."), 600))
        payload = json.dumps({"type": "ripo-tiktok-oauth", **result}).replace("</", "<\\/")
        target = json.dumps(ai.public_origin)
        return HTMLResponse(
            f"<!doctype html><body style='font-family:system-ui;background:#080a14;color:white;padding:32px'>"
            f"<h2>TikTok connection</h2><p>{message}</p>"
            f"<script>if(window.opener)window.opener.postMessage({payload},{target});setTimeout(()=>window.close(),1200)</script></body>"
        )

    @app.post("/api/tiktok/session/start")
    async def session_start(payload: dict[str, Any] = Body(default_factory=dict), x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        control_auth(x_admin_token)
        try:
            duration = payload.get("duration_minutes")
            return JSONResponse(ai.start(int(duration) if duration is not None else None))
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/tiktok/session/stop")
    async def session_stop(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        control_auth(x_admin_token)
        return JSONResponse(ai.stop())

    @app.get("/api/tiktok/events")
    async def events(after: int = 0, x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        control_auth(x_admin_token)
        rows = [event for event in ai.events if int(event["id"]) > after]
        return JSONResponse({"ok": True, "events": rows[-100:], "last_id": ai.event_id})

    @app.get("/api/tiktok/audio/next")
    async def audio_next(x_admin_token: str | None = Header(default=None)) -> Response:
        control_auth(x_admin_token)
        row = ai.pop_audio()
        if not row or not Path(row["path"]).exists():
            return Response(status_code=204)
        return FileResponse(Path(row["path"]), media_type="audio/wav", filename=f"ripo-{row['id']}.wav")

    @app.post("/api/tiktok/voice/preview")
    async def voice_preview(payload: dict[str, Any] = Body(default_factory=dict), x_admin_token: str | None = Header(default=None)) -> Response:
        control_auth(x_admin_token)
        try:
            path = await asyncio.to_thread(ai.preview, str(payload.get("voice") or ai.settings["voice"]))
            return FileResponse(path, media_type="audio/wav", filename="preview.wav")
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/tiktok/say")
    async def say(payload: dict[str, Any] = Body(default_factory=dict), x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        control_auth(x_admin_token)
        text = clean(payload.get("text"), 450)
        if not text:
            raise HTTPException(400, "Enter something to say.")
        queued = await asyncio.to_thread(ai.speak, text, "manual", 0, "", True)
        return JSONResponse({"ok": queued})

    @app.post("/api/tiktok/transcribe")
    async def transcribe(file: UploadFile, x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        control_auth(x_admin_token)
        suffix = Path(file.filename or "audio.webm").suffix or ".webm"
        temp: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=ai.data_dir) as handle:
                temp = Path(handle.name)
                while chunk := await file.read(1024 * 1024):
                    handle.write(chunk)
                    if handle.tell() > 15 * 1024 * 1024:
                        raise HTTPException(413, "Audio chunk too large.")
            return JSONResponse(await asyncio.to_thread(ai.transcribe_guest, temp))
        finally:
            if temp:
                temp.unlink(missing_ok=True)

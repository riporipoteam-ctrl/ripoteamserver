from __future__ import annotations

"""Runtime resilience patch for the Ripo TikTok LIVE AI host.

Keeps the AI host alive while the creator is offline, retries automatically,
reconnects after a LIVE ends/drops, and preserves the requested session across
ordinary Space process restarts when the local data directory survives.
"""

import asyncio
import json
import math
import threading
import time
from pathlib import Path
from typing import Any

import tiktok_ai as _tt


_PATCH_FLAG = "_ripo_resilience_v3"


def _intent_path(ai: Any) -> Path:
    return ai.data_dir / "background-session.json"


def _write_intent(ai: Any, active: bool) -> None:
    path = _intent_path(ai)
    if not active:
        path.unlink(missing_ok=True)
        return
    payload = {
        "active": True,
        "unique_id": ai.settings.get("unique_id", ""),
        "duration_minutes": int(ai.settings.get("duration_minutes", 60)),
        "ends_at": ai.ends_at,
        "saved_at": time.time(),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


async def _interruptible_sleep(ai: Any, seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        if getattr(ai, "_ripo_manual_stop", False) or ai.stop_flag.is_set():
            return
        await asyncio.sleep(min(0.5, max(0.05, deadline - time.monotonic())))


def install() -> None:
    cls = _tt.TikTokAI
    if getattr(cls, _PATCH_FLAG, False):
        return

    original_init = cls.__init__
    original_status = cls.status
    original_start = cls.start
    original_stop = cls.stop
    original_live = cls._live

    def patched_init(self: Any, data_dir: Path) -> None:
        original_init(self, data_dir)
        self.phase = "stopped"
        self.retry_count = 0
        self.next_retry_at: float | None = None
        self.last_connected_at: float | None = None
        self.last_disconnected_at: float | None = None
        self._ripo_manual_stop = False
        self._ripo_wait_notice_at = 0.0
        self._ripo_error_notice_at = 0.0

        # Best-effort resume after a normal process restart. The free Space is
        # still allowed to sleep/rebuild, so this is resilience rather than a
        # promise of permanent hosting.
        try:
            saved = json.loads(_intent_path(self).read_text(encoding="utf-8"))
            if not isinstance(saved, dict) or not saved.get("active"):
                return
            uid = str(saved.get("unique_id") or "").strip()
            if uid:
                self.settings["unique_id"] = uid
                self.save()
            ends_at = saved.get("ends_at")
            duration = int(saved.get("duration_minutes", 60))
            if ends_at is not None:
                remaining = float(ends_at) - time.time()
                if remaining <= 0:
                    _write_intent(self, False)
                    return
                duration = max(1, int(math.ceil(remaining / 60.0)))

            def resume() -> None:
                try:
                    if not self.running and self.settings.get("unique_id"):
                        self.record("resume", "Resuming the TikTok AI watcher after a server restart.")
                        self.start(duration)
                except Exception as exc:
                    self.last_error = f"Auto-resume: {_tt.clean(exc, 300)}"
                    self.record("error", self.last_error)

            threading.Timer(4.0, resume).start()
        except Exception:
            pass

    def patched_status(self: Any) -> dict[str, Any]:
        data = original_status(self)
        now = time.time()
        data.update(
            {
                "phase": getattr(self, "phase", "stopped"),
                "watching": bool(self.running and not self.connected),
                "retry_count": int(getattr(self, "retry_count", 0)),
                "next_retry_seconds": (
                    max(0, int(getattr(self, "next_retry_at", 0) - now))
                    if getattr(self, "next_retry_at", None)
                    else None
                ),
                "last_connected_at": getattr(self, "last_connected_at", None),
                "last_disconnected_at": getattr(self, "last_disconnected_at", None),
                "background_worker": True,
                "auto_reconnect": True,
                "starts_tiktok_broadcast": False,
                "audio_delivery": "browser",
            }
        )
        return data

    def patched_start(self: Any, duration: int | None = None) -> dict[str, Any]:
        self._ripo_manual_stop = False
        self.stop_flag.clear()
        self.retry_count = 0
        self.next_retry_at = None
        self.last_error = ""
        self.phase = "starting"
        result = original_start(self, duration)
        if self.running:
            self.phase = "checking-live"
            _write_intent(self, True)
            self.record(
                "watcher",
                "Background LIVE watcher started. It will stay running and connect automatically when the account is LIVE.",
            )
        return {**result, **patched_status(self)}

    def patched_stop(self: Any) -> dict[str, Any]:
        self._ripo_manual_stop = True
        self.phase = "stopping"
        _write_intent(self, False)
        result = original_stop(self)
        self.phase = "stopped"
        self.next_retry_at = None
        return {**result, **patched_status(self)}

    async def watcher(self: Any) -> None:
        from TikTokLive import TikTokLiveClient

        uid = _tt.normalize_user(str(self.settings.get("unique_id", "")))
        self.loop = asyncio.get_running_loop()
        retry_delay = 12.0

        while not getattr(self, "_ripo_manual_stop", False):
            if self.ends_at and time.time() >= self.ends_at:
                self.phase = "finished"
                self.record("session", "AI watcher duration finished.")
                _write_intent(self, False)
                break

            if self.stop_flag.is_set():
                if getattr(self, "_ripo_manual_stop", False):
                    break
                # LiveEndEvent in the original listener uses this flag. In
                # watcher mode a LIVE ending should mean 'wait for the next one'.
                self.stop_flag.clear()

            self.running = True
            self.connected = False
            self.phase = "checking-live"
            self.next_retry_at = None

            try:
                probe = TikTokLiveClient(unique_id=f"@{uid}")
                is_live = await asyncio.wait_for(probe.is_live(), timeout=25)
            except Exception as exc:
                self.retry_count += 1
                self.phase = "retrying"
                self.last_error = f"LIVE check: {_tt.clean(exc, 360)}"
                now = time.time()
                if now - self._ripo_error_notice_at > 45:
                    self._ripo_error_notice_at = now
                    self.record("retry", f"Could not check TikTok LIVE yet. Retrying automatically: {_tt.clean(exc, 220)}")
                self.next_retry_at = time.time() + retry_delay
                await _interruptible_sleep(self, retry_delay)
                continue

            if getattr(self, "_ripo_manual_stop", False) or self.stop_flag.is_set():
                break

            if not is_live:
                self.phase = "waiting-for-live"
                self.last_error = ""
                now = time.time()
                if now - self._ripo_wait_notice_at > 60:
                    self._ripo_wait_notice_at = now
                    self.record("waiting", f"@{uid} is not LIVE yet. The AI is staying on and will keep checking.")
                self.next_retry_at = time.time() + retry_delay
                await _interruptible_sleep(self, retry_delay)
                continue

            self.phase = "connecting"
            self.next_retry_at = None
            try:
                self.record("connect", f"LIVE detected for @{uid}. Connecting AI co-host now.")
                await original_live(self)
                self.last_disconnected_at = time.time()
                if getattr(self, "_ripo_manual_stop", False):
                    break
                if self.stop_flag.is_set():
                    self.stop_flag.clear()
                self.retry_count += 1
                self.phase = "reconnecting"
                self.record("reconnect", "LIVE ended or the connection closed. The AI will keep watching and reconnect automatically.")
            except Exception as exc:
                self.connected = False
                self.retry_count += 1
                self.phase = "retrying"
                self.last_error = f"TikTok LIVE connection: {_tt.clean(exc, 420)}"
                self.record("error", self.last_error)
            finally:
                if self.connected:
                    self.last_connected_at = time.time()
                self.connected = False
                self.client = None

            if self.ends_at and time.time() >= self.ends_at:
                _write_intent(self, False)
                break
            self.next_retry_at = time.time() + retry_delay
            await _interruptible_sleep(self, retry_delay)

    def patched_thread_main(self: Any) -> None:
        try:
            asyncio.run(watcher(self))
        except Exception as exc:
            self.last_error = f"TikTok watcher: {_tt.clean(exc, 420)}"
            self.record("error", self.last_error)
            self.phase = "error"
        finally:
            self.running = False
            self.connected = False
            self.client = None
            self.loop = None
            self.ends_at = None
            self.next_retry_at = None
            if getattr(self, "_ripo_manual_stop", False):
                self.phase = "stopped"
            elif self.phase not in {"finished", "error"}:
                self.phase = "stopped"

    cls.__init__ = patched_init
    cls.status = patched_status
    cls.start = patched_start
    cls.stop = patched_stop
    cls._thread_main = patched_thread_main
    setattr(cls, _PATCH_FLAG, True)


install()

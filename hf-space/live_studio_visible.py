from __future__ import annotations

import csv
import io
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mss
from PIL import Image

from live_studio_cdp import LiveStudioCDP

_SCREEN_W = 1366
_SCREEN_H = 768
_LOCK = threading.RLock()


@dataclass
class Hit:
    text: str
    left: int
    top: int
    width: int
    height: int
    confidence: float

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + max(1, self.width) // 2, self.top + max(1, self.height) // 2)


def _env(bridge: LiveStudioCDP) -> dict[str, str]:
    env = os.environ.copy()
    env["DISPLAY"] = str(getattr(bridge.wine_runner, "display", ":99"))
    env.setdefault("HOME", str(Path.home()))
    env.setdefault("USER", Path.home().name)
    runtime = Path(f"/tmp/ripo-runtime-{os.getuid()}")
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    env.setdefault("XDG_RUNTIME_DIR", str(runtime))
    return env


def _wine_window(bridge: LiveStudioCDP) -> str:
    xdotool = shutil.which("xdotool")
    if not xdotool:
        raise RuntimeError("xdotool is not installed on the server.")
    env = _env(bridge)
    for pattern in ("RipoTikTok - Wine Desktop", "RipoTikTok", "Wine Desktop"):
        try:
            out = subprocess.check_output(
                [xdotool, "search", "--onlyvisible", "--name", pattern],
                env=env,
                text=True,
                timeout=4,
            )
            ids = [line.strip() for line in out.splitlines() if line.strip()]
            if ids:
                return ids[-1]
        except Exception:
            pass
    raise RuntimeError("The TikTok LIVE Studio Wine desktop is not visible yet.")


def _geometry(bridge: LiveStudioCDP, window: str) -> tuple[int, int, int, int]:
    xdotool = shutil.which("xdotool")
    if not xdotool:
        return (0, 0, _SCREEN_W, _SCREEN_H)
    try:
        text = subprocess.check_output(
            [xdotool, "getwindowgeometry", "--shell", window],
            env=_env(bridge), text=True, timeout=4,
        )
        values: dict[str, int] = {}
        for line in text.splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key in {"X", "Y", "WIDTH", "HEIGHT"}:
                try:
                    values[key] = int(value)
                except ValueError:
                    pass
        x = max(0, values.get("X", 0))
        y = max(0, values.get("Y", 0))
        w = max(320, min(_SCREEN_W - x, values.get("WIDTH", _SCREEN_W)))
        h = max(240, min(_SCREEN_H - y, values.get("HEIGHT", _SCREEN_H)))
        return x, y, w, h
    except Exception:
        return (0, 0, _SCREEN_W, _SCREEN_H)


def _activate(bridge: LiveStudioCDP, window: str) -> None:
    subprocess.run(
        ["xdotool", "windowactivate", "--sync", window],
        env=_env(bridge), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=6, check=False,
    )
    time.sleep(0.35)


def _capture(bridge: LiveStudioCDP) -> tuple[Image.Image, tuple[int, int, int, int]]:
    window = _wine_window(bridge)
    _activate(bridge, window)
    x, y, w, h = _geometry(bridge, window)
    old = os.environ.get("DISPLAY")
    os.environ["DISPLAY"] = _env(bridge)["DISPLAY"]
    try:
        with mss.mss() as grabber:
            shot = grabber.grab({"left": x, "top": y, "width": w, "height": h})
            image = Image.frombytes("RGB", shot.size, shot.rgb)
    finally:
        if old is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = old
    return image, (x, y, w, h)


def _ocr_lines(bridge: LiveStudioCDP) -> list[Hit]:
    tesseract = shutil.which("tesseract")
    if not tesseract:
        raise RuntimeError("tesseract-ocr is not installed on the server yet.")
    image, (offset_x, offset_y, _, _) = _capture(bridge)
    with tempfile.NamedTemporaryFile(prefix="ripo-live-ui-", suffix=".png", delete=False) as handle:
        temp = Path(handle.name)
    try:
        image.save(temp, format="PNG")
        proc = subprocess.run(
            [tesseract, str(temp), "stdout", "--psm", "11", "tsv"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=25, check=False, text=True,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            raise RuntimeError("LIVE Studio screen OCR failed.")
        groups: dict[tuple[str, str, str, str], list[tuple[str, int, int, int, int, float]]] = {}
        reader = csv.DictReader(io.StringIO(proc.stdout), delimiter="\t")
        for row in reader:
            text = re.sub(r"\s+", " ", str(row.get("text") or "")).strip()
            if not text:
                continue
            try:
                conf = float(row.get("conf") or -1)
                left = int(row.get("left") or 0)
                top = int(row.get("top") or 0)
                width = int(row.get("width") or 0)
                height = int(row.get("height") or 0)
            except ValueError:
                continue
            if conf < 18 or width < 1 or height < 1:
                continue
            key = (
                str(row.get("block_num") or ""),
                str(row.get("par_num") or ""),
                str(row.get("line_num") or ""),
                str(row.get("page_num") or ""),
            )
            groups.setdefault(key, []).append((text, left, top, width, height, conf))

        hits: list[Hit] = []
        for words in groups.values():
            words.sort(key=lambda item: item[1])
            text = " ".join(word[0] for word in words)
            left = min(word[1] for word in words)
            top = min(word[2] for word in words)
            right = max(word[1] + word[3] for word in words)
            bottom = max(word[2] + word[4] for word in words)
            conf = sum(word[5] for word in words) / len(words)
            hits.append(Hit(text=text[:180], left=offset_x + left, top=offset_y + top, width=right-left, height=bottom-top, confidence=conf))
        return hits
    finally:
        temp.unlink(missing_ok=True)


def _classify(hits: list[Hit]) -> dict[str, list[Hit]]:
    buckets: dict[str, list[Hit]] = {"go_live": [], "login": [], "confirm": [], "continue": [], "guest": [], "mic": []}
    patterns = {
        "go_live": re.compile(r"\b(go\s*live|start\s*(live|stream(?:ing)?))\b", re.I),
        "login": re.compile(r"\b(log\s*in|sign\s*in)\b", re.I),
        "confirm": re.compile(r"\b(confirm|yes,?\s*go\s*live|go\s*live\s*now)\b", re.I),
        "continue": re.compile(r"\b(continue|authorize|allow|open\s*tiktok)\b", re.I),
        "guest": re.compile(r"\b(guest|co-?host|multi-?guest)\b", re.I),
        "mic": re.compile(r"\b(mic|microphone|audio)\b", re.I),
    }
    for hit in hits:
        for key, pattern in patterns.items():
            if pattern.search(hit.text):
                buckets[key].append(hit)
    for rows in buckets.values():
        rows.sort(key=lambda hit: (-hit.confidence, len(hit.text)))
    return buckets


def _click(bridge: LiveStudioCDP, hit: Hit) -> None:
    x, y = hit.center
    x = max(1, min(_SCREEN_W - 2, x))
    y = max(1, min(_SCREEN_H - 2, y))
    subprocess.run(
        ["xdotool", "mousemove", "--sync", str(x), str(y), "click", "1"],
        env=_env(bridge), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=6, check=True,
    )


def visible_capabilities(bridge: LiveStudioCDP) -> dict[str, Any]:
    try:
        hits = _ocr_lines(bridge)
        buckets = _classify(hits)
        return {
            "ok": True,
            "visible_ui_ready": True,
            "ocr_ready": True,
            "go_live_available": bool(buckets["go_live"]),
            "login_required": bool(buckets["login"]) and not bool(buckets["go_live"]),
            "confirm_available": bool(buckets["confirm"]),
            "continue_available": bool(buckets["continue"]),
            "guest_controls_visible": bool(buckets["guest"]),
            "microphone_controls_visible": bool(buckets["mic"]),
            "safe_action_labels": [
                *( ["Go LIVE"] if buckets["go_live"] else [] ),
                *( ["Login"] if buckets["login"] else [] ),
                *( ["Confirm"] if buckets["confirm"] else [] ),
                *( ["Continue"] if buckets["continue"] else [] ),
                *( ["Guest"] if buckets["guest"] else [] ),
                *( ["Microphone"] if buckets["mic"] else [] ),
            ],
        }
    except Exception as exc:
        return {
            "ok": True,
            "visible_ui_ready": False,
            "ocr_ready": bool(shutil.which("tesseract")),
            "go_live_available": False,
            "login_required": False,
            "confirm_available": False,
            "continue_available": False,
            "guest_controls_visible": False,
            "microphone_controls_visible": False,
            "safe_action_labels": [],
            "visible_ui_error": str(exc)[:500],
        }


def _wait_for_app(bridge: LiveStudioCDP, timeout: float = 190.0) -> None:
    state = bridge.wine_runner.status()
    if state.get("live_studio_running"):
        return
    bridge.wine_runner.try_start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(2)
        state = bridge.wine_runner.status()
        if state.get("live_studio_running"):
            return
        if state.get("phase") == "wine-failed":
            raise RuntimeError(str(state.get("last_error") or "LIVE Studio failed to start."))
    raise RuntimeError("TikTok LIVE Studio did not become ready in time.")


def _go_live(self: LiveStudioCDP) -> dict[str, Any]:
    with self.lock, _LOCK:
        _wait_for_app(self)
        clicked: list[str] = []
        deadline = time.time() + 85
        login_clicked = False
        while time.time() < deadline:
            hits = _ocr_lines(self)
            buckets = _classify(hits)

            if buckets["go_live"]:
                hit = buckets["go_live"][0]
                _click(self, hit)
                clicked.append("Go LIVE")
                time.sleep(3)
                # A confirmation screen/dialog is common. Click only an explicit
                # confirmation/Go LIVE label; never click arbitrary screen areas.
                second = _classify(_ocr_lines(self))
                if second["confirm"]:
                    _click(self, second["confirm"][0])
                    clicked.append("Confirm")
                elif second["go_live"]:
                    _click(self, second["go_live"][0])
                    clicked.append("Go LIVE confirmation")
                try:
                    self.ai.start()
                except Exception:
                    pass
                self.last_actions.extend(clicked)
                self.last_error = ""
                return {
                    "ok": True,
                    "clicked": clicked,
                    "control": "visible-window-ocr",
                    "message": "LIVE Studio Go LIVE control was pressed; Ripo Bot AI host is starting.",
                }

            if buckets["login"] and not login_clicked:
                _click(self, buckets["login"][0])
                clicked.append("Login")
                login_clicked = True
                time.sleep(5)
                continue

            # After a Login click, only approve clearly named continuation actions.
            if login_clicked and buckets["continue"]:
                _click(self, buckets["continue"][0])
                clicked.append("Continue")
                time.sleep(5)
                continue

            time.sleep(2)

        caps = visible_capabilities(self)
        self.last_error = "LIVE Studio stayed open, but the server could not reach an explicit Go LIVE control."
        if caps.get("login_required"):
            self.last_error += " LIVE Studio is still asking for its own TikTok login."
        raise RuntimeError(self.last_error)


def _sync_session(self: LiveStudioCDP) -> dict[str, Any]:
    # TikTok's custom Electron build does not expose Chromium DevTools even when
    # passed a localhost debugging port. Keep the endpoint useful and truthful.
    caps = visible_capabilities(self)
    return {
        "ok": True,
        "synced": 0,
        "control": "visible-window-ocr",
        "message": "LIVE Studio uses visible-window control; browser session restoration remains handled by the Ripo server Firefox profile.",
        **caps,
    }


def _status(self: LiveStudioCDP) -> dict[str, Any]:
    caps = visible_capabilities(self) if self.wine_runner.status().get("live_studio_running") else {
        "visible_ui_ready": False,
        "ocr_ready": bool(shutil.which("tesseract")),
        "go_live_available": False,
        "login_required": False,
        "confirm_available": False,
        "continue_available": False,
        "guest_controls_visible": False,
        "microphone_controls_visible": False,
        "safe_action_labels": [],
    }
    return {
        "ok": True,
        "cdp_ready": False,
        "target_count": 0,
        "control": "visible-window-ocr",
        "last_actions": self.last_actions[-8:],
        "last_error": self.last_error,
        **caps,
    }


LiveStudioCDP.go_live = _go_live
LiveStudioCDP.sync_session = _sync_session
LiveStudioCDP.status = _status

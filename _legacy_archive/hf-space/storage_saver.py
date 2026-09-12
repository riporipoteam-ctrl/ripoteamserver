from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any


class StorageSaver:
    """Conservative storage maintenance for an ephemeral Hugging Face Space.

    Never deletes the active Ollama model store, Hermes config/skills/plugins,
    the Hermes installation, or installed browser binaries. It focuses on
    disposable caches, stale sessions, temporary browser profiles and log growth.
    """

    def __init__(self, *, home: Path, data_dir: Path, log_dir: Path, hermes_home: Path) -> None:
        self.home = home
        self.data_dir = data_dir
        self.log_dir = log_dir
        self.hermes_home = hermes_home
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self.state: dict[str, Any] = {
            "running": False,
            "last_run": None,
            "last_saved_bytes": 0,
            "total_saved_bytes": 0,
            "deleted_files": 0,
            "pressure": "unknown",
            "last_error": None,
        }

    @staticmethod
    def _age_seconds(path: Path) -> float:
        try:
            return max(0.0, time.time() - path.stat().st_mtime)
        except OSError:
            return 0.0

    @staticmethod
    def _safe_size(path: Path) -> int:
        try:
            if path.is_symlink():
                return 0
            if path.is_file():
                return path.stat().st_size
            total = 0
            for child in path.rglob("*"):
                try:
                    if child.is_file() and not child.is_symlink():
                        total += child.stat().st_size
                except OSError:
                    pass
            return total
        except OSError:
            return 0

    def _remove(self, path: Path) -> tuple[int, int]:
        if not path.exists() and not path.is_symlink():
            return 0, 0
        size = self._safe_size(path)
        count = 1
        try:
            if path.is_dir() and not path.is_symlink():
                count = sum(1 for item in path.rglob("*") if item.is_file()) or 1
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            return size, count
        except OSError:
            return 0, 0

    def _trim_log(self, path: Path, max_bytes: int) -> int:
        try:
            size = path.stat().st_size
            if size <= max_bytes:
                return 0
            keep = max_bytes // 2
            with path.open("rb") as handle:
                handle.seek(max(0, size - keep))
                tail = handle.read()
            path.write_bytes(tail)
            return max(0, size - len(tail))
        except OSError:
            return 0

    def _delete_old_children(self, root: Path, max_age_seconds: int) -> tuple[int, int]:
        if not root.exists():
            return 0, 0
        saved = 0
        count = 0
        try:
            children = list(root.iterdir())
        except OSError:
            return 0, 0
        for child in children:
            if self._age_seconds(child) < max_age_seconds:
                continue
            freed, removed = self._remove(child)
            saved += freed
            count += removed
        return saved, count

    def _disk_pressure(self) -> tuple[str, dict[str, int | float]]:
        try:
            usage = shutil.disk_usage(self.data_dir)
            ratio = usage.used / usage.total if usage.total else 0.0
            if ratio >= 0.90:
                level = "critical"
            elif ratio >= 0.80:
                level = "high"
            elif ratio >= 0.70:
                level = "elevated"
            else:
                level = "normal"
            return level, {
                "filesystem_total": usage.total,
                "filesystem_used": usage.used,
                "filesystem_free": usage.free,
                "filesystem_used_ratio": round(ratio, 4),
            }
        except OSError:
            return "unknown", {}

    def cleanup(self) -> dict[str, Any]:
        with self._lock:
            if self.state["running"]:
                return dict(self.state)
            self.state["running"] = True
            self.state["last_error"] = None

        saved = 0
        deleted = 0
        try:
            pressure, disk = self._disk_pressure()
            # Normal retention: five days of sessions. Under pressure, shorten it.
            session_days = 5 if pressure in {"normal", "elevated", "unknown"} else (2 if pressure == "high" else 1)
            temp_hours = 12 if pressure in {"normal", "elevated", "unknown"} else 3
            log_limit = 4 * 1024 * 1024 if pressure != "critical" else 1 * 1024 * 1024

            for root in (self.log_dir, self.hermes_home / "logs"):
                if root.exists():
                    for log in root.glob("*.log"):
                        freed = self._trim_log(log, log_limit)
                        saved += freed
                        if freed:
                            deleted += 1

            # Old Hermes conversations are disposable on this public bot. Preserve
            # memories/config/skills/plugins while removing stale session payloads.
            freed, removed = self._delete_old_children(
                self.hermes_home / "sessions", session_days * 86400
            )
            saved += freed
            deleted += removed

            # Clear package-manager caches. These are never runtime state.
            cache_targets = [
                self.home / ".cache" / "pip",
                self.home / ".cache" / "uv",
                self.home / ".npm" / "_cacache",
            ]
            for target in cache_targets:
                if target.exists() and (pressure in {"high", "critical"} or self._age_seconds(target) > 86400):
                    freed, removed = self._remove(target)
                    saved += freed
                    deleted += removed

            # Clean stale temporary browser/Hermes working directories, but never
            # the installed Playwright/Chromium browser cache itself.
            tmp = Path("/tmp")
            if tmp.exists():
                for pattern in ("hermes-*", "agent-browser-*", "playwright-*", "tmp-hermes-*"):
                    for target in tmp.glob(pattern):
                        if self._age_seconds(target) >= temp_hours * 3600:
                            freed, removed = self._remove(target)
                            saved += freed
                            deleted += removed

            # Interrupted downloads and temporary archives are safe to remove.
            for pattern in ("*.part", "*.partial", "*.tmp", "*.tar", "*.tar.gz", "*.tar.zst"):
                for target in self.data_dir.rglob(pattern):
                    if self._age_seconds(target) >= 6 * 3600:
                        freed, removed = self._remove(target)
                        saved += freed
                        deleted += removed

            with self._lock:
                self.state.update(
                    running=False,
                    last_run=time.time(),
                    last_saved_bytes=saved,
                    total_saved_bytes=int(self.state.get("total_saved_bytes", 0)) + saved,
                    deleted_files=deleted,
                    pressure=pressure,
                    last_error=None,
                )
                self.state.update(disk)
                return dict(self.state)
        except Exception as exc:
            with self._lock:
                self.state.update(running=False, last_run=time.time(), last_error=str(exc))
                return dict(self.state)

    def _worker(self) -> None:
        # Give boot/model downloads time to settle before the first maintenance pass.
        time.sleep(120)
        while True:
            self.cleanup()
            time.sleep(6 * 3600)

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._worker, daemon=True, name="ripo-storage-saver")
            self._thread.start()

    def status(self) -> dict[str, Any]:
        pressure, disk = self._disk_pressure()
        with self._lock:
            snapshot = dict(self.state)
        snapshot.update(disk)
        snapshot["pressure"] = pressure
        snapshot["policy"] = {
            "session_retention_days": 5,
            "pressure_session_retention_days": 2,
            "critical_session_retention_days": 1,
            "cleanup_interval_hours": 6,
            "normal_log_cap_mb": 4,
            "protects_model_store": True,
            "protects_browser_binaries": True,
            "protects_skills_plugins_config": True,
        }
        return snapshot

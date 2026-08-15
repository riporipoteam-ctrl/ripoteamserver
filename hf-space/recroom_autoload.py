from __future__ import annotations

import hashlib
import hmac
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any


_MOUNT_LOCK = threading.Lock()
_MOUNTED = False


def _derived_key(root_secret: str, purpose: bytes) -> str:
    return hmac.new(root_secret.encode("utf-8"), purpose, hashlib.sha256).hexdigest()


def install_into_live_app(application: Any, data_dir: Path | None = None) -> dict[str, Any]:
    """Mount the Rec Room stack once onto whichever Gradio/FastAPI app is live."""
    global _MOUNTED
    with _MOUNT_LOCK:
        existing = {getattr(route, "path", None) for route in getattr(application, "routes", [])}
        if _MOUNTED or "/api/recroom-public/status" in existing:
            _MOUNTED = True
            return {"ok": True, "alreadyMounted": True}

        root = data_dir or Path(os.environ.get("RIPO_DATA_DIR", str(Path.home() / ".ripo-cloud-pc")))
        root.mkdir(parents=True, exist_ok=True)
        public_url = os.environ.get("RECROOM_PUBLIC_BASE_URL", "https://echoxr-ripoteam-cloud-pc.hf.space").rstrip("/")
        os.environ.setdefault("RECROOM_GATEWAY_URL", public_url)

        admin_token = os.environ.get("ADMIN_TOKEN", "").strip()
        if admin_token:
            os.environ.setdefault(
                "RECROOM_BROKER_KEY",
                _derived_key(admin_token, b"ripo-recroom-broker-v1"),
            )
            os.environ.setdefault(
                "RECROOM_HOST_KEY",
                _derived_key(admin_token, b"ripo-recroom-host-v1"),
            )

        from recroom_gateway import RecRoomGateway, install_recroom_gateway_routes
        from recroom_compat import install_recroom_compat_routes
        from recroom_compat_extra import install_recroom_extra_routes
        from recroom_match_compat import install_recroom_match_compat_routes
        from recroom_broker import install_recroom_broker_routes
        from recroom_capture import install_recroom_capture_routes
        from recroom_public import install_recroom_public_routes

        gateway = RecRoomGateway(root / "recroom-gateway")
        install_recroom_gateway_routes(application, gateway)
        install_recroom_compat_routes(application, gateway)
        install_recroom_extra_routes(application, gateway)
        # Matchmaking/heartbeat is mounted last among RecNet compatibility layers
        # because it intentionally replaces the earlier generic join DTOs with
        # the recovered roomInstance structure used by old clients.
        install_recroom_match_compat_routes(application, gateway)
        broker = install_recroom_broker_routes(application, root / "recroom-broker")
        capture = install_recroom_capture_routes(application, broker, root / "recroom-captures")
        install_recroom_public_routes(application, broker, capture)

        _MOUNTED = True
        return {
            "ok": True,
            "alreadyMounted": False,
            "gateway": gateway,
            "broker": broker,
            "capture": capture,
        }


def _mount_when_app_exists() -> None:
    for _ in range(320):
        module = sys.modules.get("app") or sys.modules.get("__main__")
        application = getattr(module, "app", None) if module is not None else None
        if application is None:
            time.sleep(0.25)
            continue
        time.sleep(0.75)
        try:
            data_dir = getattr(module, "DATA_DIR", None)
            result = install_into_live_app(application, data_dir if isinstance(data_dir, Path) else None)
            if module is not None and result.get("gateway") is not None:
                module.RIPO_RECROOM_GATEWAY = result["gateway"]
                module.RIPO_RECROOM_BROKER = result["broker"]
                module.RIPO_RECROOM_CAPTURE = result["capture"]
            print(f"Rec Room May 2022 runtime routes mounted: {result.get('ok')}")
            return
        except Exception as exc:
            print(f"Rec Room runtime route mount failed: {exc}")
            return
    print("Rec Room runtime route mount timed out waiting for app.py.")


def start_recroom_autoload() -> None:
    threading.Thread(
        target=_mount_when_app_exists,
        name="ripo-recroom-route-mount",
        daemon=True,
    ).start()

import os
import sys
import threading
import time

# Public TikTok Login Kit values. The client secret must stay in the Space's
# encrypted Secrets settings and is intentionally never stored in GitHub.
os.environ.setdefault("TIKTOK_CLIENT_KEY", "awx4azk8o5ac8zow")
os.environ.setdefault(
    "TIKTOK_REDIRECT_URI",
    "https://echoxr-ripoteam-cloud-pc.hf.space/api/tiktok/oauth/callback",
)
os.environ.setdefault("TIKTOK_SCOPES", "user.info.basic")
os.environ.setdefault("RIPO_PUBLIC_ORIGIN", "https://riporipoteam-ctrl.github.io")
os.environ.setdefault("RIPO_SPACE_ORIGIN", "https://echoxr-ripoteam-cloud-pc.hf.space")

# Keep the resilient LIVE event listener patch available for the AI event side.
try:
    import tiktok_resilience  # noqa: F401
except Exception as exc:
    print(f"TikTok resilience patch failed to load: {exc}")


def _mount_server_tiktok_routes() -> None:
    """Mount server-browser + server-broadcast routes on whichever module owns app.py.

    Hugging Face is currently launching app.py directly even when an alternate
    app_file is present in the Space card, so this attaches the new routes to
    the real running Server instance instead of relying on a wrapper entrypoint.
    """
    for _ in range(240):
        module = sys.modules.get("app") or sys.modules.get("__main__")
        if module is None:
            time.sleep(0.25)
            continue

        required = ("app", "TIKTOK_AI", "DATA_DIR", "DISPLAY", "authorize")
        if not all(hasattr(module, name) for name in required):
            time.sleep(0.25)
            continue

        # Give app.py a moment to finish installing its normal routes first.
        time.sleep(0.75)
        try:
            application = module.app
            existing = {getattr(route, "path", None) for route in getattr(application, "routes", [])}
            if "/api/tiktok/server-connect/status" in existing and "/api/tiktok/server-live/status" in existing:
                print("TikTok server routes already mounted.")
                return

            from server_tiktok_connect import ServerTikTokConnect, install_server_tiktok_connect_routes
            from server_live_broadcaster import ServerLiveBroadcaster, install_server_live_routes

            connector = ServerTikTokConnect(
                module.TIKTOK_AI,
                module.DATA_DIR / "tiktok-server-browser",
                module.DISPLAY,
            )
            install_server_tiktok_connect_routes(application, connector)

            broadcaster = ServerLiveBroadcaster(
                module.TIKTOK_AI,
                connector,
                module.DATA_DIR / "tiktok-server-live",
                module.authorize,
                module.DISPLAY,
            )
            install_server_live_routes(application, broadcaster)

            # Keep strong references for the lifetime of the Space process.
            module.RIPO_SERVER_TIKTOK_CONNECT = connector
            module.RIPO_SERVER_LIVE_BROADCASTER = broadcaster
            print("TikTok server-browser and server-LIVE routes mounted on the running Space app.")
            return
        except Exception as exc:
            print(f"TikTok server route mount failed: {exc}")
            return

    print("TikTok server route mount timed out waiting for app.py.")


threading.Thread(
    target=_mount_server_tiktok_routes,
    name="ripo-tiktok-route-mount",
    daemon=True,
).start()

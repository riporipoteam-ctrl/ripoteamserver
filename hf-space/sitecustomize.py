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

try:
    import tiktok_resilience  # noqa: F401
except Exception as exc:
    print(f"TikTok resilience patch failed to load: {exc}")


def _mount_server_tiktok_routes() -> None:
    for _ in range(240):
        module = sys.modules.get("app") or sys.modules.get("__main__")
        if module is None:
            time.sleep(0.25)
            continue

        required = ("app", "TIKTOK_AI", "DATA_DIR", "DISPLAY", "authorize")
        if not all(hasattr(module, name) for name in required):
            time.sleep(0.25)
            continue

        time.sleep(0.75)
        try:
            application = module.app
            existing = {getattr(route, "path", None) for route in getattr(application, "routes", [])}

            from server_tiktok_connect import ServerTikTokConnect, install_server_tiktok_connect_routes
            from server_live_broadcaster import ServerLiveBroadcaster, install_server_live_routes
            from live_studio_wine import LiveStudioWine, install_live_studio_wine_routes

            connector = getattr(module, "RIPO_SERVER_TIKTOK_CONNECT", None)
            if connector is None:
                connector = ServerTikTokConnect(
                    module.TIKTOK_AI,
                    module.DATA_DIR / "tiktok-server-browser",
                    module.DISPLAY,
                )
                if "/api/tiktok/server-connect/status" not in existing:
                    install_server_tiktok_connect_routes(application, connector)
                module.RIPO_SERVER_TIKTOK_CONNECT = connector

            broadcaster = getattr(module, "RIPO_SERVER_LIVE_BROADCASTER", None)
            if broadcaster is None:
                broadcaster = ServerLiveBroadcaster(
                    module.TIKTOK_AI,
                    connector,
                    module.DATA_DIR / "tiktok-server-live",
                    module.authorize,
                    module.DISPLAY,
                )
                if "/api/tiktok/server-live/status" not in existing:
                    install_server_live_routes(application, broadcaster)
                module.RIPO_SERVER_LIVE_BROADCASTER = broadcaster

            wine_runner = getattr(module, "RIPO_LIVE_STUDIO_WINE", None)
            if wine_runner is None:
                wine_runner = LiveStudioWine(
                    module.TIKTOK_AI,
                    connector,
                    module.DATA_DIR / "tiktok-live-studio-wine",
                    module.authorize,
                    module.DISPLAY,
                )
                if "/api/tiktok/live-studio-linux/status" not in existing:
                    install_live_studio_wine_routes(application, wine_runner)
                module.RIPO_LIVE_STUDIO_WINE = wine_runner

            print("TikTok server routes and 64-bit Wine LIVE Studio probe mounted on the running Space app.")
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

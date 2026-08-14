import os
import subprocess
import sys
import threading
import time

os.environ.setdefault("TIKTOK_CLIENT_KEY", "awx4azk8o5ac8zow")
os.environ.setdefault("TIKTOK_REDIRECT_URI", "https://echoxr-ripoteam-cloud-pc.hf.space/api/tiktok/oauth/callback")
os.environ.setdefault("TIKTOK_SCOPES", "user.info.basic")
os.environ.setdefault("RIPO_PUBLIC_ORIGIN", "https://riporipoteam-ctrl.github.io")
os.environ.setdefault("RIPO_SPACE_ORIGIN", "https://echoxr-ripoteam-cloud-pc.hf.space")
# Kept on only while the localhost LIVE Studio UI bridge is being verified.
os.environ.setdefault("RIPO_WINE_AUTOPROBE", "1")

try:
    import tiktok_resilience  # noqa: F401
except Exception as exc:
    print(f"TikTok resilience patch failed to load: {exc}")

try:
    import tiktok_session_stability  # noqa: F401
except Exception as exc:
    print(f"TikTok session stability patch failed to load: {exc}")


def _auto_probe_live_studio(connector, wine_runner) -> None:
    if os.environ.get("RIPO_WINE_AUTOPROBE", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    try:
        time.sleep(8)
        result = wine_runner.try_start()
        print(f"TikTok LIVE Studio Wine auto-probe started: {result.get('message', '')}")
    except Exception as exc:
        wine_runner.phase = "wine-failed"
        wine_runner.last_error = f"Auto-probe failed before installer test: {exc}"
        print(wine_runner.last_error)


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
            import server_tiktok_connect_stability  # noqa: F401
            import tiktok_profile_backup  # noqa: F401
            from tiktok_profile_backup_routes import install_tiktok_profile_persistence_routes
            from server_live_broadcaster import ServerLiveBroadcaster, install_server_live_routes
            from live_studio_wine import LiveStudioWine, install_live_studio_wine_routes
            import live_studio_wine_download_fix  # noqa: F401
            import live_studio_wine_runtime_fix  # noqa: F401
            import live_studio_wine_candidate_fix  # noqa: F401
            import live_studio_wine_browser_fix  # noqa: F401
            import live_studio_wine_resolver_fix  # noqa: F401
            import live_studio_wine_launch_fix  # noqa: F401
            from live_studio_cdp import LiveStudioCDP, install_live_studio_cdp_routes

            connector = getattr(module, "RIPO_SERVER_TIKTOK_CONNECT", None)
            if connector is None:
                connector = ServerTikTokConnect(module.TIKTOK_AI, module.DATA_DIR / "tiktok-server-browser", module.DISPLAY)
                if "/api/tiktok/server-connect/status" not in existing:
                    install_server_tiktok_connect_routes(application, connector)
                module.RIPO_SERVER_TIKTOK_CONNECT = connector

            if "/api/tiktok/server-connect/profile/restore" not in existing:
                install_tiktok_profile_persistence_routes(application, connector)

            broadcaster = getattr(module, "RIPO_SERVER_LIVE_BROADCASTER", None)
            if broadcaster is None:
                broadcaster = ServerLiveBroadcaster(module.TIKTOK_AI, connector, module.DATA_DIR / "tiktok-server-live", module.authorize, module.DISPLAY)
                if "/api/tiktok/server-live/status" not in existing:
                    install_server_live_routes(application, broadcaster)
                module.RIPO_SERVER_LIVE_BROADCASTER = broadcaster

            wine_runner = getattr(module, "RIPO_LIVE_STUDIO_WINE", None)
            if wine_runner is None:
                wine_runner = LiveStudioWine(module.TIKTOK_AI, connector, module.DATA_DIR / "tiktok-live-studio-wine", module.authorize, module.DISPLAY)
                if "/api/tiktok/live-studio-linux/status" not in existing:
                    install_live_studio_wine_routes(application, wine_runner)
                module.RIPO_LIVE_STUDIO_WINE = wine_runner

            cdp_bridge = getattr(module, "RIPO_LIVE_STUDIO_CDP", None)
            if cdp_bridge is None:
                cdp_bridge = LiveStudioCDP(module.TIKTOK_AI, connector, wine_runner)
                if "/api/tiktok/live-studio-linux/ui-status" not in existing:
                    # Reuse the Wine runner's auth wrapper: TikTok Connect browser
                    # sessions are accepted, with ADMIN_TOKEN as the fallback.
                    install_live_studio_cdp_routes(application, cdp_bridge, wine_runner._auth)
                module.RIPO_LIVE_STUDIO_CDP = cdp_bridge

            threading.Thread(target=_auto_probe_live_studio, args=(connector, wine_runner), name="ripo-live-studio-wine-autoprobe", daemon=True).start()
            print("TikTok persistence, Wine LIVE Studio, and localhost UI automation routes mounted.")
            return
        except Exception as exc:
            print(f"TikTok server route mount failed: {exc}")
            return
    print("TikTok server route mount timed out waiting for app.py.")


threading.Thread(target=_mount_server_tiktok_routes, name="ripo-tiktok-route-mount", daemon=True).start()

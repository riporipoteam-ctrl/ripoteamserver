import os

# Public TikTok Login Kit values. The client secret must stay in the Space's
# encrypted Secrets settings and is intentionally never stored in GitHub.
os.environ.setdefault("TIKTOK_CLIENT_KEY", "awx4azk8o5ac8zow")
os.environ.setdefault(
    "TIKTOK_REDIRECT_URI",
    "https://echoxr-ripoteam-cloud-pc.hf.space/api/tiktok/oauth/callback",
)
# Keep Login Kit on the baseline scope unless the Space explicitly overrides it.
os.environ.setdefault("TIKTOK_SCOPES", "user.info.basic")
os.environ.setdefault("RIPO_PUBLIC_ORIGIN", "https://riporipoteam-ctrl.github.io")

# Install background watcher / reconnect behavior before app.py instantiates
# the TikTokAI class. This keeps the worker alive when the creator is offline
# and lets it reconnect automatically after disconnects.
try:
    import tiktok_resilience  # noqa: F401
except Exception as exc:
    print(f"TikTok resilience patch failed to load: {exc}")

# Load the optional Windows LIVE Studio remote-control bridge bootstrap.
try:
    import live_studio_bridge  # noqa: F401
except Exception as exc:
    print(f"TikTok LIVE Studio bridge failed to load: {exc}")

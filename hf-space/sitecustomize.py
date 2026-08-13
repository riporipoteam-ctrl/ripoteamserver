import os

# Public TikTok Login Kit values. The client secret must stay in the Space's
# encrypted Secrets settings and is intentionally never stored in GitHub.
os.environ.setdefault("TIKTOK_CLIENT_KEY", "awx4azk8o5ac8zow")
os.environ.setdefault(
    "TIKTOK_REDIRECT_URI",
    "https://echoxr-ripoteam-cloud-pc.hf.space/api/tiktok/oauth/callback",
)
os.environ.setdefault("TIKTOK_SCOPES", "user.info.basic,user.info.profile")
os.environ.setdefault("RIPO_PUBLIC_ORIGIN", "https://riporipoteam-ctrl.github.io")

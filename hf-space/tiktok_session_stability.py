from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import urlencode

from tiktok_ai import TikTokAI, clean


_OLD_SESSION_VALID = TikTokAI.session_valid
_OLD_OAUTH_CALLBACK = TikTokAI.oauth_callback


def _key(ai: TikTokAI) -> bytes:
    # Stable on the Space, never written to GitHub. Using both secrets means a
    # stolen browser token cannot be forged and rotating either secret revokes it.
    material = (os.environ.get("ADMIN_TOKEN", "") + "\0" + ai.client_secret).encode("utf-8")
    return hashlib.sha256(b"ripo-tiktok-session-v2\0" + material).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _claims(ai: TikTokAI, token: str | None) -> dict:
    value = clean(token, 3000)
    if not value.startswith("rts2."):
        return {}
    try:
        _, body, signature = value.split(".", 2)
        expected = _b64(hmac.new(_key(ai), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, signature):
            return {}
        data = json.loads(_unb64(body).decode("utf-8"))
        if int(data.get("v", 0)) != 2 or int(data.get("exp", 0)) <= int(time.time()):
            return {}
        return data
    except Exception:
        return {}


def _issue(ai: TikTokAI) -> str:
    now = int(time.time())
    profile = ai.oauth_profile or {}
    payload = {
        "v": 2,
        "iat": now,
        "exp": now + 30 * 86400,
        "oid": clean(profile.get("open_id"), 200),
        "name": clean(profile.get("display_name"), 80),
        "user": clean(profile.get("username"), 40),
        "n": secrets.token_urlsafe(10),
    }
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64(hmac.new(_key(ai), body.encode("ascii"), hashlib.sha256).digest())
    return f"rts2.{body}.{signature}"


def session_valid(self: TikTokAI, token: str | None) -> bool:
    claims = _claims(self, token)
    if claims:
        owner = clean(self.oauth_profile.get("open_id"), 200)
        claim_owner = clean(claims.get("oid"), 200)
        if owner and claim_owner and owner != claim_owner:
            return False

        # After a Space rebuild oauth.json may be gone, but the signed browser
        # session on the user's device is still trustworthy. Rehydrate harmless
        # profile display fields so the dashboard stops pretending it is logged out.
        if not owner and claim_owner:
            self.oauth_profile = {
                "open_id": claim_owner,
                "display_name": clean(claims.get("name"), 80),
                "username": clean(claims.get("user"), 40),
                "avatar_url": "",
                "profile_deep_link": "",
            }
            username = clean(claims.get("user"), 40)
            if username:
                try:
                    self.settings["unique_id"] = username
                    self.save()
                except Exception:
                    pass
        return True
    return _OLD_SESSION_VALID(self, token)


def oauth_start(self: TikTokAI, basic: bool = False) -> dict:
    if not self.client_secret:
        raise RuntimeError("TIKTOK_CLIENT_SECRET is not configured in the Hugging Face Space secrets.")
    now = time.time()
    for key, row in list(self.oauth_states.items()):
        if float(row.get("expires", 0)) < now:
            self.oauth_states.pop(key, None)
    state = secrets.token_urlsafe(30)
    self.oauth_states[state] = {
        "expires": now + 600,
        "session_token": secrets.token_urlsafe(42),
    }
    requested_scopes = "user.info.basic" if basic else self.scopes
    # Do NOT set disable_auto_auth=1. That flag forced TikTok through repeated
    # authorization even when the server browser already had a valid session.
    url = "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(
        {
            "client_key": self.client_key,
            "scope": requested_scopes,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "state": state,
        }
    )
    return {"ok": True, "url": url, "requested_scopes": requested_scopes}


async def oauth_callback(self: TikTokAI, code: str, state: str) -> dict:
    result = await _OLD_OAUTH_CALLBACK(self, code, state)
    old_token = clean(result.get("session_token"), 300)
    if old_token:
        self.oauth_sessions.pop(old_token, None)

    signed = _issue(self)
    claims = _claims(self, signed)
    self.oauth_sessions[signed] = {
        "expires": float(claims.get("exp", time.time() + 30 * 86400)),
        "open_id": clean(self.oauth_profile.get("open_id"), 200),
    }
    result["session_token"] = signed
    result["session_expires"] = int(claims.get("exp", 0))
    result["session_persistence"] = "restart-safe"
    return result


TikTokAI.session_valid = session_valid
TikTokAI.oauth_start = oauth_start
TikTokAI.oauth_callback = oauth_callback

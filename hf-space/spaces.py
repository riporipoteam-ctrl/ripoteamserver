from __future__ import annotations

# This file intentionally shadows the Hugging Face `spaces` package for one
# import. It loads Ripo Team's sealed TikTok OAuth envelope into environment
# variables, then hands control straight to the real installed `spaces`
# package. No plaintext TikTok secret is stored in this repository.

import base64
import hashlib
import json
import os
import sys
from importlib.machinery import PathFinder
from importlib.util import module_from_spec
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_CONTEXT = b"ripo-team-space-sealed-secret-v1"
_PURPOSE = b"tiktok-oauth-credentials"


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))


def _load_tiktok_oauth() -> None:
    if os.environ.get("TIKTOK_CLIENT_SECRET"):
        return
    admin_token = os.environ.get("ADMIN_TOKEN", "")
    vnc_password = os.environ.get("VNC_PASSWORD", "")
    envelope_path = Path(__file__).resolve().parent / "sealed" / "tiktok-oauth.json"
    if not admin_token or not vnc_password or not envelope_path.exists():
        return
    try:
        master = hashlib.sha256(
            _CONTEXT + b"\x00" + admin_token.encode("utf-8") + b"\x00" + vnc_password.encode("utf-8")
        ).digest()
        private_key = X25519PrivateKey.from_private_bytes(master)
        public_raw = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        key_id = hashlib.sha256(public_raw).hexdigest()[:16]

        envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
        if envelope.get("version") != 1 or envelope.get("purpose") != "tiktok-oauth-credentials":
            return
        if envelope.get("key_id") != key_id:
            return

        peer = X25519PublicKey.from_public_bytes(_b64d(str(envelope["ephemeral_public_key"])))
        shared = private_key.exchange(peer)
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=_CONTEXT,
            info=_PURPOSE,
        ).derive(shared)
        aad = f"1:tiktok-oauth-credentials:{key_id}".encode("utf-8")
        plaintext = ChaCha20Poly1305(key).decrypt(
            _b64d(str(envelope["nonce"])),
            _b64d(str(envelope["ciphertext"])),
            aad,
        )
        payload = json.loads(plaintext.decode("utf-8"))
        client_key = str(payload.get("client_key") or "").strip()
        client_secret = str(payload.get("client_secret") or "").strip()
        redirect_uri = str(payload.get("redirect_uri") or "").strip()
        if client_key and client_secret and redirect_uri.startswith("https://"):
            os.environ["TIKTOK_CLIENT_KEY"] = client_key
            os.environ["TIKTOK_CLIENT_SECRET"] = client_secret
            os.environ["TIKTOK_REDIRECT_URI"] = redirect_uri
    except Exception:
        # TikTok status will report OAuth as unconfigured if sealed bootstrap fails.
        pass


_load_tiktok_oauth()

# Delegate to the actual Hugging Face spaces package while preserving its real
# package name so its relative imports continue to work normally.
_this_dir = str(Path(__file__).resolve().parent)
_search_path: list[str] = []
for _entry in sys.path:
    try:
        if str(Path(_entry or ".").resolve()) == _this_dir:
            continue
    except Exception:
        pass
    _search_path.append(_entry)

_spec = PathFinder.find_spec(__name__, _search_path)
if _spec is None or _spec.loader is None:
    raise ImportError("The real Hugging Face 'spaces' package could not be located.")
_real = module_from_spec(_spec)
sys.modules[__name__] = _real
_spec.loader.exec_module(_real)

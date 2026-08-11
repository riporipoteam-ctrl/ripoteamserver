from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

CONTEXT = b"ripo-team-space-sealed-secret-v1"
PURPOSE = b"telegram-bot-token"


def _b64e(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(value + padding)


class SealedSecretManager:
    """Decrypts a public-repo ciphertext using secrets already present only in the Space."""

    def __init__(self, *, admin_token: str, vnc_password: str, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.admin_token = admin_token
        self.vnc_password = vnc_password
        self.envelope_path = base_dir / "sealed" / "telegram.json"
        self._last_status: dict[str, Any] = {
            "loaded": False,
            "source": "environment" if os.environ.get("TELEGRAM_BOT_TOKEN") else "none",
            "message": "Telegram token has not been loaded from a sealed envelope.",
        }

    def _private_key(self) -> X25519PrivateKey | None:
        if not self.admin_token or not self.vnc_password:
            return None
        material = (
            CONTEXT
            + b"\x00"
            + self.admin_token.encode("utf-8")
            + b"\x00"
            + self.vnc_password.encode("utf-8")
        )
        seed = hashlib.sha256(material).digest()
        return X25519PrivateKey.from_private_bytes(seed)

    def public_info(self) -> dict[str, Any]:
        private_key = self._private_key()
        if private_key is None:
            return {
                "available": False,
                "key_id": None,
                "public_key": None,
                "algorithm": "X25519-HKDF-SHA256-ChaCha20Poly1305",
            }
        public_key = private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        return {
            "available": True,
            "key_id": hashlib.sha256(public_key).hexdigest()[:16],
            "public_key": _b64e(public_key),
            "algorithm": "X25519-HKDF-SHA256-ChaCha20Poly1305",
        }

    def _derive_aead_key(self, peer_public_key: bytes) -> bytes:
        private_key = self._private_key()
        if private_key is None:
            raise RuntimeError("Space sealing key is unavailable because ADMIN_TOKEN or VNC_PASSWORD is missing.")
        shared = private_key.exchange(X25519PublicKey.from_public_bytes(peer_public_key))
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=CONTEXT,
            info=PURPOSE,
        ).derive(shared)

    def load_telegram_token(self) -> dict[str, Any]:
        if os.environ.get("TELEGRAM_BOT_TOKEN"):
            self._last_status = {
                "loaded": True,
                "source": "environment",
                "message": "Telegram token is configured through the Space environment.",
            }
            return dict(self._last_status)

        if not self.envelope_path.exists():
            self._last_status = {
                "loaded": False,
                "source": "none",
                "message": "No sealed Telegram envelope is deployed yet.",
            }
            return dict(self._last_status)

        try:
            envelope = json.loads(self.envelope_path.read_text(encoding="utf-8"))
            if envelope.get("version") != 1 or envelope.get("purpose") != "telegram-bot-token":
                raise ValueError("Unsupported sealed-secret envelope.")
            public_info = self.public_info()
            if not public_info.get("available"):
                raise RuntimeError("Space sealing key is unavailable.")
            if envelope.get("key_id") != public_info.get("key_id"):
                raise RuntimeError("Envelope key id does not match this Space instance.")

            ephemeral_public = _b64d(str(envelope["ephemeral_public_key"]))
            nonce = _b64d(str(envelope["nonce"]))
            ciphertext = _b64d(str(envelope["ciphertext"]))
            key = self._derive_aead_key(ephemeral_public)
            aad = f"{envelope['version']}:{envelope['purpose']}:{envelope['key_id']}".encode("utf-8")
            plaintext = ChaCha20Poly1305(key).decrypt(nonce, ciphertext, aad)
            token = plaintext.decode("utf-8").strip()
            if not token or ":" not in token:
                raise ValueError("Decrypted Telegram credential failed format validation.")

            os.environ["TELEGRAM_BOT_TOKEN"] = token
            self._last_status = {
                "loaded": True,
                "source": "sealed-envelope",
                "message": "Telegram token was loaded from the sealed repository envelope.",
            }
            return dict(self._last_status)
        except Exception as exc:
            self._last_status = {
                "loaded": False,
                "source": "sealed-envelope",
                "message": f"Sealed Telegram envelope could not be opened: {exc}",
            }
            return dict(self._last_status)

    def status(self) -> dict[str, Any]:
        return {
            **self.public_info(),
            "loaded": bool(self._last_status.get("loaded")),
            "source": self._last_status.get("source"),
            "message": self._last_status.get("message"),
            "envelope_present": self.envelope_path.exists(),
        }

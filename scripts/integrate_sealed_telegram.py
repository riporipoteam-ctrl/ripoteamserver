from pathlib import Path

path = Path("hf-space/ai_stack.py")
text = path.read_text(encoding="utf-8")

if "from sealed_secrets import SealedSecretManager" not in text:
    marker = "from typing import Any\n"
    if marker not in text:
        raise SystemExit("typing import marker not found")
    text = text.replace(marker, marker + "\nfrom sealed_secrets import SealedSecretManager\n", 1)

if "self.sealed_secrets = SealedSecretManager(" not in text:
    marker = "        self.log_dir = log_dir\n"
    if marker not in text:
        raise SystemExit("AIStack init marker not found")
    block = (
        "        self.log_dir = log_dir\n"
        "        self.sealed_secrets = SealedSecretManager(\n"
        "            admin_token=os.environ.get(\"ADMIN_TOKEN\", \"\"),\n"
        "            vnc_password=os.environ.get(\"VNC_PASSWORD\", \"\"),\n"
        "            base_dir=Path(__file__).resolve().parent,\n"
        "        )\n"
        "        self.sealed_secret_status = self.sealed_secrets.load_telegram_token()\n"
    )
    text = text.replace(marker, block, 1)

old = (
    '            "telegram": {"token_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN")), '
    '"allowlist_configured": bool(os.environ.get("TELEGRAM_ALLOWED_USERS")), '
    '"access_mode": "allowlist" if os.environ.get("TELEGRAM_ALLOWED_USERS") else "default-deny-pairing"},\n'
)
new = (
    '            "telegram": {\n'
    '                "token_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),\n'
    '                "allowlist_configured": bool(os.environ.get("TELEGRAM_ALLOWED_USERS")),\n'
    '                "access_mode": "allowlist" if os.environ.get("TELEGRAM_ALLOWED_USERS") else "default-deny-pairing",\n'
    '                "sealing": self.sealed_secrets.status(),\n'
    '            },\n'
)
if old in text:
    text = text.replace(old, new, 1)
elif '"sealing": self.sealed_secrets.status()' not in text:
    raise SystemExit("Telegram status marker not found")

path.write_text(text, encoding="utf-8")
print("sealed Telegram integration applied")

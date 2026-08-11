from pathlib import Path

path = Path('hf-space/ai_stack.py')
text = path.read_text(encoding='utf-8')

old = '        self.sealed_secret_status = self.sealed_secrets.load_telegram_token()\n'
new = old + '        self.sealed_allowlist_status = self.sealed_secrets.load_telegram_allowlist()\n        self.pairing_request_path = Path(__file__).resolve().parent / "pairing-request.json"\n        self.pairing_export_path = data_dir / "pairing-export.json"\n'
if 'self.pairing_request_path =' not in text:
    if old not in text:
        raise SystemExit('AIStack init marker missing')
    text = text.replace(old, new, 1)

marker = '    def bootstrap(self) -> dict[str, Any]:\n'
if 'def process_pairing_request(self)' not in text:
    method = '''    def _approved_telegram_user_ids(self, output: str = "") -> list[str]:
        ids: set[str] = set(re.findall(r"\\((\\d{5,})\\)", output or ""))
        approved_path = self.hermes_home / "pairing" / "telegram-approved.json"
        if approved_path.exists():
            try:
                payload = json.loads(approved_path.read_text(encoding="utf-8"))
                def walk(value: Any) -> None:
                    if isinstance(value, dict):
                        for key, item in value.items():
                            if str(key).lower() in {"user_id", "userid", "telegram_user_id"} and str(item).isdigit():
                                ids.add(str(item))
                            else:
                                walk(item)
                    elif isinstance(value, list):
                        for item in value:
                            walk(item)
                walk(payload)
            except Exception:
                pass
        return sorted(ids)

    def process_pairing_request(self) -> dict[str, Any]:
        if not self.pairing_request_path.exists():
            return {"ok": True, "message": "No pending deployment pairing request."}
        try:
            request = json.loads(self.pairing_request_path.read_text(encoding="utf-8"))
            code = str(request.get("code", "")).strip()
        except Exception as exc:
            return {"ok": False, "message": f"Could not read pairing request: {exc}"}
        result = self.approve_pairing(code)
        ids = self._approved_telegram_user_ids(str(result.get("message", "")))
        if not ids:
            return result
        try:
            envelope = self.sealed_secrets.seal_telegram_allowlist(ids)
            os.environ["TELEGRAM_ALLOWED_USERS"] = ",".join(ids)
            export = {
                "ready": True,
                "approved_count": len(ids),
                "envelope": envelope,
                "created_at": time.time(),
            }
            self.pairing_export_path.parent.mkdir(parents=True, exist_ok=True)
            self.pairing_export_path.write_text(json.dumps(export, indent=2) + "\\n", encoding="utf-8")
            return {"ok": True, "message": "Telegram pairing approved and encrypted allowlist export is ready."}
        except Exception as exc:
            return {"ok": False, "message": f"Pairing was approved but persistence sealing failed: {exc}"}

'''
    if marker not in text:
        raise SystemExit('bootstrap marker missing')
    text = text.replace(marker, method + marker, 1)

old_gateway = '            if os.environ.get("TELEGRAM_BOT_TOKEN"):\n                self.start_gateway()\n'
new_gateway = old_gateway + '                self.process_pairing_request()\n'
if 'self.process_pairing_request()' not in text.split('def bootstrap', 1)[1]:
    if old_gateway not in text:
        raise SystemExit('gateway bootstrap marker missing')
    text = text.replace(old_gateway, new_gateway, 1)

old_status = '                "sealing": self.sealed_secrets.status(),\n            },\n'
new_status = '                "sealing": self.sealed_secrets.status(),\n                "pairing_export": (json.loads(self.pairing_export_path.read_text(encoding="utf-8")) if self.pairing_export_path.exists() else None),\n            },\n'
if '"pairing_export":' not in text:
    if old_status not in text:
        raise SystemExit('telegram status marker missing')
    text = text.replace(old_status, new_status, 1)

path.write_text(text, encoding='utf-8')

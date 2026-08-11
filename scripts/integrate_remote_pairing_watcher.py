from pathlib import Path

path = Path('hf-space/ai_stack.py')
text = path.read_text(encoding='utf-8')

init_marker = '        self.pairing_export_path = data_dir / "pairing-export.json"\n'
init_add = init_marker + '        self.remote_pairing_url = os.environ.get("RIPO_PAIRING_COMMAND_URL", "https://raw.githubusercontent.com/riporipoteam-ctrl/ripoteamserver/main/runtime/telegram-pairing-command.json")\n        self._pairing_watcher_thread: threading.Thread | None = None\n        self._processed_pairing_request_ids: set[str] = set()\n        self.pairing_command_status: dict[str, Any] = {"watching": False, "last_request_id": None, "last_ok": None, "message": "Waiting for a live pairing command."}\n'
if 'self.remote_pairing_url =' not in text:
    if init_marker not in text:
        raise SystemExit('init marker missing')
    text = text.replace(init_marker, init_add, 1)

marker = '    def bootstrap(self) -> dict[str, Any]:\n'
if 'def start_remote_pairing_watcher(self)' not in text:
    methods = '''    def _persist_approved_ids(self, ids: list[str]) -> dict[str, Any]:
        if not ids:
            return {"ok": False, "message": "Pairing approval did not reveal an approved Telegram user ID."}
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

    def _remote_pairing_watcher(self) -> None:
        self.pairing_command_status.update(watching=True, message="Watching GitHub for a live Telegram pairing command.")
        while True:
            try:
                request = urllib.request.Request(
                    self.remote_pairing_url + f"?t={int(time.time())}",
                    headers={"Accept": "application/json", "Cache-Control": "no-cache"},
                )
                with urllib.request.urlopen(request, timeout=8) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                request_id = str(payload.get("request_id", "")).strip()
                code = str(payload.get("code", "")).strip()
                if request_id and request_id not in self._processed_pairing_request_ids:
                    self._processed_pairing_request_ids.add(request_id)
                    result = self.approve_pairing(code)
                    ids = self._approved_telegram_user_ids(str(result.get("message", "")))
                    if ids:
                        result = self._persist_approved_ids(ids)
                    self.pairing_command_status.update(
                        last_request_id=request_id,
                        last_ok=bool(result.get("ok")),
                        message=str(result.get("message", ""))[-500:],
                    )
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    self.pairing_command_status.update(last_ok=False, message=f"Pairing watcher HTTP error: {exc.code}")
            except Exception as exc:
                self.pairing_command_status.update(last_ok=False, message=f"Pairing watcher error: {exc}")
            time.sleep(8)

    def start_remote_pairing_watcher(self) -> dict[str, Any]:
        with self._lock:
            if self._pairing_watcher_thread and self._pairing_watcher_thread.is_alive():
                return {"ok": True, "message": "Remote pairing watcher is already running."}
            self._pairing_watcher_thread = threading.Thread(target=self._remote_pairing_watcher, daemon=True)
            self._pairing_watcher_thread.start()
        return {"ok": True, "message": "Remote pairing watcher started."}

'''
    if marker not in text:
        raise SystemExit('bootstrap marker missing')
    text = text.replace(marker, methods + marker, 1)

old = '            if os.environ.get("TELEGRAM_BOT_TOKEN"):\n                self.start_gateway()\n                self.process_pairing_request()\n'
new = old + '                self.start_remote_pairing_watcher()\n'
if 'self.start_remote_pairing_watcher()' not in text.split('def bootstrap', 1)[1]:
    if old not in text:
        raise SystemExit('bootstrap gateway marker missing')
    text = text.replace(old, new, 1)

status_marker = '                "pairing_export": (json.loads(self.pairing_export_path.read_text(encoding="utf-8")) if self.pairing_export_path.exists() else None),\n'
status_add = status_marker + '                "pairing_command": dict(self.pairing_command_status),\n'
if '"pairing_command": dict(self.pairing_command_status)' not in text:
    if status_marker not in text:
        raise SystemExit('status marker missing')
    text = text.replace(status_marker, status_add, 1)

path.write_text(text, encoding='utf-8')

from pathlib import Path

path = Path('hf-space/ai_stack.py')
text = path.read_text(encoding='utf-8')

# 1) Public Telegram authorization + execution guard.
needle = '                "HERMES_API_TIMEOUT": env.get("HERMES_API_TIMEOUT", "1800"),\n'
replacement = needle + '                "TELEGRAM_ALLOW_ALL_USERS": "true",\n                "HERMES_EXEC_ASK": "true",\n'
if '"TELEGRAM_ALLOW_ALL_USERS": "true"' not in text:
    if needle not in text:
        raise SystemExit('Could not locate Hermes env marker')
    text = text.replace(needle, replacement, 1)

# 2) Restrict Telegram itself to the read-only safe toolset.
old_cfg = '''            "session_reset:\\n"\n            '  mode: "both"\\n'\n            "  idle_minutes: 1440\\n"\n            "  at_hour: 4\\n"\n            "gateway:\\n"\n            "  platforms:\\n"\n            "    telegram:\\n"\n            "      extra:\\n"\n            "        disable_link_previews: true\\n",\n'''
new_cfg = '''            "session_reset:\\n"\n            '  mode: "both"\\n'\n            "  idle_minutes: 1440\\n"\n            "  at_hour: 4\\n"\n            "platform_toolsets:\\n"\n            "  telegram:\\n"\n            "    - safe\\n"\n            "gateway:\\n"\n            "  platforms:\\n"\n            "    telegram:\\n"\n            "      extra:\\n"\n            "        disable_link_previews: true\\n"\n            "        require_mention: true\\n"\n            "        allow_admin_from:\\n"\n            '          - "0"\\n'\n            "        user_allowed_commands:\\n"\n            "          - status\\n"\n            "          - model\\n"\n            "          - history\\n"\n            "        group_allow_admin_from:\\n"\n            '          - "0"\\n'\n            "        group_user_allowed_commands:\\n"\n            "          - status\\n",\n'''
if '"platform_toolsets:\\n"' not in text:
    if old_cfg not in text:
        raise SystemExit('Could not locate Hermes config marker')
    text = text.replace(old_cfg, new_cfg, 1)

# 3) Update gateway startup message.
old_return = '        return {"ok": True, "message": "Hermes Telegram gateway started. " + ("Telegram is restricted by your allowlist." if os.environ.get("TELEGRAM_ALLOWED_USERS") else "Unknown Telegram users remain denied until paired.")}\n'
new_return = '''        public_telegram = str(self._env().get("TELEGRAM_ALLOW_ALL_USERS", "")).lower() in {"1", "true", "yes", "on"}\n        if public_telegram:\n            return {"ok": True, "message": "Hermes Telegram gateway started in PUBLIC SAFE mode. No pairing code is required; Telegram is limited to the safe toolset."}\n        return {"ok": True, "message": "Hermes Telegram gateway started. " + ("Telegram is restricted by your allowlist." if os.environ.get("TELEGRAM_ALLOWED_USERS") else "Unknown Telegram users remain denied until paired.")}\n'''
if 'PUBLIC SAFE mode' not in text:
    if old_return not in text:
        raise SystemExit('Could not locate gateway message marker')
    text = text.replace(old_return, new_return, 1)

# 4) Do not run pairing infrastructure in public mode.
old_boot = '''            if os.environ.get("TELEGRAM_BOT_TOKEN"):\n                self.start_gateway()\n                self.process_pairing_request()\n                self.start_remote_pairing_watcher()\n'''
new_boot = '''            if os.environ.get("TELEGRAM_BOT_TOKEN"):\n                self.start_gateway()\n                public_telegram = str(self._env().get("TELEGRAM_ALLOW_ALL_USERS", "")).lower() in {"1", "true", "yes", "on"}\n                if public_telegram:\n                    self.pairing_command_status.update(\n                        watching=False,\n                        last_ok=True,\n                        message="Pairing disabled because Telegram is running in public-safe mode.",\n                    )\n                else:\n                    self.process_pairing_request()\n                    self.start_remote_pairing_watcher()\n'''
if 'Pairing disabled because Telegram is running in public-safe mode.' not in text:
    if old_boot not in text:
        raise SystemExit('Could not locate bootstrap pairing marker')
    text = text.replace(old_boot, new_boot, 1)

# 5) Report public-safe accurately in the health/status API.
old_status_head = '''        gateway_running = bool(self._gateway_process and self._gateway_process.poll() is None)\n        return {\n'''
new_status_head = '''        gateway_running = bool(self._gateway_process and self._gateway_process.poll() is None)\n        public_telegram = str(self._env().get("TELEGRAM_ALLOW_ALL_USERS", "")).lower() in {"1", "true", "yes", "on"}\n        return {\n'''
if 'gateway_running = bool(self._gateway_process' in text and 'public_telegram = str(self._env().get("TELEGRAM_ALLOW_ALL_USERS"' not in text[text.find('    def status('):]:
    if old_status_head not in text:
        raise SystemExit('Could not locate status head marker')
    text = text.replace(old_status_head, new_status_head, 1)

old_access = '                "access_mode": "allowlist" if os.environ.get("TELEGRAM_ALLOWED_USERS") else "default-deny-pairing",\n'
new_access = '''                "access_mode": "public-safe" if public_telegram else ("allowlist" if os.environ.get("TELEGRAM_ALLOWED_USERS") else "default-deny-pairing"),\n                "pairing_required": not public_telegram,\n                "tool_profile": "safe" if public_telegram else "hermes-telegram",\n'''
if '"access_mode": "public-safe" if public_telegram' not in text:
    if old_access not in text:
        raise SystemExit('Could not locate status access marker')
    text = text.replace(old_access, new_access, 1)

# 6) Make the custom Telegram skill match the new architecture.
text = text.replace(
    '"telegram-bot": ("Operate the private Hermes Telegram bot.", """Use for Telegram gateway administration.\\n- Require pairing or an explicit allowlist; never enable allow-all for a terminal-capable agent.\\n- Never echo the bot token.\\n- Use `hermes pairing` commands to approve trusted users.\\n- After config changes, restart `hermes gateway` and verify its log.\\n"""),',
    '"telegram-bot": ("Operate the public-safe Hermes Telegram bot.", """Use for Telegram gateway administration.\\n- Telegram public access is intentionally enabled, but the Telegram platform is restricted to the read-only `safe` toolset.\\n- Never echo the bot token or expose Cloud PC admin credentials.\\n- Do not enable terminal, file-write, code-execution, cron, deployment, or admin toolsets for public Telegram sessions.\\n- After config changes, restart `hermes gateway` and verify its log.\\n"""),',
)

path.write_text(text, encoding='utf-8')
print('Patched public-safe Telegram mode.')

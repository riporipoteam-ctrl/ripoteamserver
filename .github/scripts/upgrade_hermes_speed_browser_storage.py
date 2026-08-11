from pathlib import Path

path = Path('hf-space/ai_stack.py')
s = path.read_text(encoding='utf-8')


def replace_once(old: str, new: str) -> None:
    global s
    if old not in s:
        raise SystemExit(f'Missing patch marker: {old[:100]!r}')
    s = s.replace(old, new, 1)

replace_once(
    'from sealed_secrets import SealedSecretManager\n',
    'from sealed_secrets import SealedSecretManager\nfrom storage_saver import StorageSaver\n',
)
replace_once(
    'DEFAULT_MODEL = os.environ.get("RIPO_AI_MODEL", "qwen3:4b")',
    'DEFAULT_MODEL = os.environ.get("RIPO_AI_MODEL", "qwen3:4b-instruct")',
)
replace_once(
    '        self.hermes_home = home / ".hermes"\n',
    '        self.hermes_home = home / ".hermes"\n'
    '        self.storage_saver = StorageSaver(home=home, data_dir=data_dir, log_dir=log_dir, hermes_home=self.hermes_home)\n',
)
replace_once(
    '                    f"{self.home / \'.local/bin\'}:"\n',
    '                    f"{self.hermes_home / \'node/bin\'}:"\n'
    '                    f"{self.home / \'.local/bin\'}:"\n',
)
replace_once(
    '                "OLLAMA_KEEP_ALIVE": env.get("OLLAMA_KEEP_ALIVE", "10m"),\n'
    '                "OLLAMA_NUM_PARALLEL": env.get("OLLAMA_NUM_PARALLEL", "1"),\n'
    '                "OLLAMA_MAX_LOADED_MODELS": env.get("OLLAMA_MAX_LOADED_MODELS", "1"),\n'
    '                "OLLAMA_CONTEXT_LENGTH": env.get("OLLAMA_CONTEXT_LENGTH", "65536"),\n',
    '                "OLLAMA_KEEP_ALIVE": env.get("OLLAMA_KEEP_ALIVE", "1h"),\n'
    '                "OLLAMA_NUM_PARALLEL": env.get("OLLAMA_NUM_PARALLEL", "1"),\n'
    '                "OLLAMA_MAX_LOADED_MODELS": env.get("OLLAMA_MAX_LOADED_MODELS", "1"),\n'
    '                "OLLAMA_MAX_QUEUE": env.get("OLLAMA_MAX_QUEUE", "16"),\n'
    '                "OLLAMA_CONTEXT_LENGTH": env.get("OLLAMA_CONTEXT_LENGTH", "16384"),\n'
    '                "OLLAMA_NOHISTORY": "1",\n',
)
replace_once(
    '            "platform_toolsets:\\n"\n'
    '            "  telegram:\\n"\n'
    '            "    - safe\\n"\n',
    '            "platform_toolsets:\\n"\n'
    '            "  telegram:\\n"\n'
    '            "    - safe\\n"\n'
    '            "    - browser\\n"\n'
    '            "plugins:\\n"\n'
    '            "  enabled:\\n"\n'
    '            "    - disk-cleanup\\n"\n'
    '            "    - security-guidance\\n"\n',
)
replace_once(
    '            "          - commands\\n"\n'
    '            "          - help\\n"\n'
    '            "          - whoami\\n"\n',
    '            "          - commands\\n"\n'
    '            "          - help\\n"\n'
    '            "          - whoami\\n"\n'
    '            "          - stop\\n"\n'
    '            "          - retry\\n"\n'
    '            "          - undo\\n"\n'
    '            "          - compress\\n"\n'
    '            "          - usage\\n"\n'
    '            "          - insights\\n"\n'
    '            "          - personality\\n"\n'
    '            "          - ripo-web-browser\\n"\n'
    '            "          - ripo-fact-check\\n"\n'
    '            "          - ripo-news-research\\n"\n'
    '            "          - ripo-travel-research\\n"\n'
    '            "          - ripo-product-research\\n"\n'
    '            "          - ripo-study-helper\\n"\n'
    '            "          - ripo-coding-helper\\n"\n'
    '            "          - ripo-translator\\n"\n'
    '            "          - ripo-summarizer\\n"\n'
    '            "          - ripo-storage-saver\\n"\n',
)
replace_once(
    '        self._install_ripo_skills()\n'
    '        return {"ok": True, "message": "Hermes configured for local Ollama and Ripo Team skills.", "model": model}\n',
    '        self._install_ripo_skills()\n'
    '        self._install_ripo_power_skills()\n'
    '        return {"ok": True, "message": "Hermes configured for fast local Ollama, browser tools, cleanup plugins and Ripo Team skills.", "model": model}\n',
)

marker = '    def install_optional_skills(self) -> dict[str, Any]:\n'
power = '''    def _install_ripo_power_skills(self) -> None:\n        skills: dict[str, tuple[str, str]] = {\n            "web-browser": ("Use the local headless browser for interactive web research.", "Navigate pages, inspect accessible content, click and type only when necessary, and prefer read-only browsing. Never enter secrets, payments, or credentials. Cross-check important facts with multiple sources."),\n            "fact-check": ("Verify claims against reliable sources.", "Search the web, prefer primary sources, compare dates and wording, identify uncertainty, and clearly separate verified facts from inference."),\n            "news-research": ("Research current news efficiently.", "Search recent sources, compare reputable outlets, identify when an event happened, summarize what changed, and avoid presenting rumors as confirmed facts."),\n            "travel-research": ("Research travel destinations and logistics.", "Use live web research for opening hours, transport, events and official notices. Prefer official tourism, venue and transport sources when available."),\n            "product-research": ("Compare products using current web information.", "Compare specifications, compatibility, recurring costs and credible reviews. Do not invent prices or availability."),\n            "study-helper": ("Teach and explain topics clearly.", "Give a concise explanation first, then examples. Use web research only when the fact is current or needs verification."),\n            "coding-helper": ("Help with programming without host shell access.", "Explain code, debug snippets supplied in chat, research official documentation, and propose safe patches. Telegram public sessions do not have terminal or file-write access."),\n            "translator": ("Translate while preserving meaning and tone.", "Detect the source language, preserve names and formatting, and provide a natural translation rather than a word-for-word one unless requested."),\n            "summarizer": ("Summarize long text and web pages.", "Extract the key points, decisions, dates, numbers and open questions. Keep the summary proportional to the source."),\n            "storage-saver": ("Explain and monitor the Ripo Team storage-saving policy.", "The server rotates logs, expires old public chat sessions, deletes package caches and stale temporary browser files, protects the active model/browser binaries/config/skills/plugins, and becomes more aggressive only under disk pressure."),\n        }\n        base = self.hermes_home / "skills" / "ripo-team"\n        for name, (description, body) in skills.items():\n            folder = base / name\n            folder.mkdir(parents=True, exist_ok=True)\n            (folder / "SKILL.md").write_text(\n                "---\\n"\n                f"name: ripo-{name}\\n"\n                f"description: {description}\\n"\n                "version: 1.1.0\\n"\n                "metadata:\\n"\n                "  hermes:\\n"\n                f"    tags: [ripo-team, public-safe, {name}]\\n"\n                "    category: ripo-team\\n"\n                "---\\n\\n"\n                f"# Ripo Team {name.replace('-', ' ').title()}\\n\\n{body}\\n",\n                encoding="utf-8",\n            )\n\n'''
if marker not in s:
    raise SystemExit('Missing optional skill marker')
s = s.replace(marker, power + marker, 1)

marker = '    def start_gateway(self) -> dict[str, Any]:\n'
browser_methods = '''    def install_browser_tools(self) -> dict[str, Any]:\n        if shutil.which("agent-browser", path=self._env()["PATH"]):\n            return {"ok": True, "message": "Hermes local browser tools are already installed."}\n        self._set_state("browser-install", "Installing local Chromium browser automation…")\n        command = "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --ensure browser"\n        try:\n            self._run(["bash", "-lc", command], log_name="hermes-browser", timeout=2400)\n        except Exception as exc:\n            return {"ok": False, "message": f"Browser setup failed: {exc}"}\n        available = shutil.which("agent-browser", path=self._env()["PATH"]) is not None\n        return {"ok": available, "message": "Local Chromium browser automation is ready." if available else "Browser installer finished but agent-browser was not found."}\n\n    def prewarm_model(self, model: str = DEFAULT_MODEL) -> dict[str, Any]:\n        binary = self.ollama_binary()\n        if not binary or model not in self.installed_models():\n            return {"ok": False, "message": "Model is not available to pre-warm."}\n        try:\n            self._run([str(binary), "run", model, ""], log_name="ollama-warm", timeout=600)\n            return {"ok": True, "message": f"{model} is warm and ready for fast replies."}\n        except Exception as exc:\n            return {"ok": False, "message": f"Model pre-warm failed: {exc}"}\n\n    def remove_legacy_models(self) -> dict[str, Any]:\n        binary = self.ollama_binary()\n        if not binary:\n            return {"ok": False, "removed": []}\n        removed: list[str] = []\n        for old in ("qwen3:4b",):\n            if old != DEFAULT_MODEL and old in self.installed_models():\n                try:\n                    self._run([str(binary), "rm", old], log_name="ollama-prune", timeout=300)\n                    removed.append(old)\n                except Exception:\n                    pass\n        return {"ok": True, "removed": removed}\n\n'''
if marker not in s:
    raise SystemExit('Missing gateway marker')
s = s.replace(marker, browser_methods + marker, 1)

replace_once(
    '            self.install_hermes()\n'
    '            self.configure_hermes(DEFAULT_MODEL)\n'
    '            self.install_optional_skills()\n',
    '            self.remove_legacy_models()\n'
    '            self.prewarm_model(DEFAULT_MODEL)\n'
    '            self.install_hermes()\n'
    '            self.configure_hermes(DEFAULT_MODEL)\n'
    '            self.install_browser_tools()\n'
    '            self.install_optional_skills()\n'
    '            self.storage_saver.start()\n'
    '            self.storage_saver.cleanup()\n',
)

replace_once(
    '            "storage": {"model_directory": str(self.ollama_models), "ephemeral": True},\n',
    '            "browser": {"agent_browser": shutil.which("agent-browser", path=self._env()["PATH"]) is not None, "toolset": "browser"},\n'
    '            "storage": {"model_directory": str(self.ollama_models), "ephemeral": True, "saver": self.storage_saver.status()},\n',
)

# Update one stale skill note left from the former private-pairing design.
s = s.replace(
    'Default-deny access to messaging bots with user allowlists or pairing.',
    'Public Telegram sessions must remain restricted to safe/browser toolsets; never expose terminal or file-write tools.',
)

path.write_text(s, encoding='utf-8')
print('Hermes speed/browser/storage upgrade patched successfully.')

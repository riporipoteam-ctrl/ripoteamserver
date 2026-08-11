from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from sealed_secrets import SealedSecretManager

OLLAMA_API = "http://127.0.0.1:11434"
DEFAULT_MODEL = os.environ.get("RIPO_AI_MODEL", "qwen3:4b")
SESSION_TIMEOUT_SECONDS = 1800


class AIStack:
    def __init__(self, *, home: Path, data_dir: Path, log_dir: Path) -> None:
        self.home = home
        self.data_dir = data_dir
        self.log_dir = log_dir
        self.sealed_secrets = SealedSecretManager(
            admin_token=os.environ.get("ADMIN_TOKEN", ""),
            vnc_password=os.environ.get("VNC_PASSWORD", ""),
            base_dir=Path(__file__).resolve().parent,
        )
        self.sealed_secret_status = self.sealed_secrets.load_telegram_token()
        self.ollama_root = data_dir / "ollama"
        self.ollama_models = data_dir / "ollama-models"
        self.hermes_home = home / ".hermes"
        self._ollama_process: subprocess.Popen[Any] | None = None
        self._gateway_process: subprocess.Popen[Any] | None = None
        self._bootstrap_thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self.state: dict[str, Any] = {
            "running": False,
            "stage": "idle",
            "message": "Local AI stack is idle.",
            "started_at": None,
            "finished_at": None,
            "last_error": None,
        }
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.ollama_models.mkdir(parents=True, exist_ok=True)

    def _log_path(self, name: str) -> Path:
        return self.log_dir / f"{name}.log"

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(self.home),
                "USER": env.get("USER", self.home.name),
                "HERMES_HOME": str(self.hermes_home),
                "PATH": (
                    f"{self.ollama_root / 'bin'}:"
                    f"{self.home / '.local/bin'}:"
                    f"{env.get('PATH', '')}"
                ),
                "OLLAMA_HOST": "127.0.0.1:11434",
                "OLLAMA_MODELS": str(self.ollama_models),
                "OLLAMA_KEEP_ALIVE": env.get("OLLAMA_KEEP_ALIVE", "10m"),
                "OLLAMA_NUM_PARALLEL": env.get("OLLAMA_NUM_PARALLEL", "1"),
                "OLLAMA_MAX_LOADED_MODELS": env.get("OLLAMA_MAX_LOADED_MODELS", "1"),
                "OLLAMA_CONTEXT_LENGTH": env.get("OLLAMA_CONTEXT_LENGTH", "65536"),
                "HERMES_API_TIMEOUT": env.get("HERMES_API_TIMEOUT", "1800"),
            }
        )
        lib_dir = self.ollama_root / "lib" / "ollama"
        if lib_dir.exists():
            env["LD_LIBRARY_PATH"] = f"{lib_dir}:{env.get('LD_LIBRARY_PATH', '')}"
        return env

    def _set_state(self, stage: str, message: str, *, error: str | None = None) -> None:
        with self._lock:
            self.state["stage"] = stage
            self.state["message"] = message
            self.state["last_error"] = error

    def _run(self, command: list[str], *, log_name: str, timeout: int = 1800, check: bool = True) -> subprocess.CompletedProcess[Any]:
        with self._log_path(log_name).open("ab", buffering=0) as output:
            return subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, env=self._env(), timeout=timeout, check=check)

    def ollama_binary(self) -> Path | None:
        candidates = [self.ollama_root / "bin" / "ollama", self.home / ".local/bin/ollama"]
        discovered = shutil.which("ollama", path=self._env()["PATH"])
        if discovered:
            candidates.insert(0, Path(discovered))
        return next((path for path in candidates if path.exists() and os.access(path, os.X_OK)), None)

    def hermes_binary(self) -> Path | None:
        candidates = [
            self.home / ".local/bin/hermes",
            self.hermes_home / "hermes-agent/venv/bin/hermes",
            self.hermes_home / "hermes-agent/.venv/bin/hermes",
        ]
        discovered = shutil.which("hermes", path=self._env()["PATH"])
        if discovered:
            candidates.insert(0, Path(discovered))
        return next((path for path in candidates if path.exists() and os.access(path, os.X_OK)), None)

    def _json_get(self, url: str, timeout: float = 3.5) -> dict[str, Any] | None:
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return None

    def ollama_ready(self) -> bool:
        return self._json_get(f"{OLLAMA_API}/api/tags") is not None

    def installed_models(self) -> list[str]:
        data = self._json_get(f"{OLLAMA_API}/api/tags")
        models = data.get("models", []) if isinstance(data, dict) else []
        result: list[str] = []
        for model in models:
            if isinstance(model, dict):
                name = model.get("name") or model.get("model")
                if name:
                    result.append(str(name))
        return result

    def install_ollama(self) -> dict[str, Any]:
        binary = self.ollama_binary()
        if binary:
            return {"ok": True, "message": "Ollama is already installed.", "path": str(binary)}
        if os.uname().machine not in {"x86_64", "amd64"}:
            return {"ok": False, "message": f"Unsupported Ollama architecture: {os.uname().machine}"}
        self._set_state("ollama-install", "Installing Ollama in user space…")
        self.ollama_root.mkdir(parents=True, exist_ok=True)
        archive = self.data_dir / "ollama-linux-amd64.tar.zst"
        url = "https://ollama.com/download/ollama-linux-amd64.tar.zst"
        self._run(["curl", "-fL", "--retry", "3", "--connect-timeout", "20", "-o", str(archive), url], log_name="ollama-install", timeout=900)
        self._run(["tar", "--zstd", "-xf", str(archive), "-C", str(self.ollama_root)], log_name="ollama-install", timeout=300)
        archive.unlink(missing_ok=True)
        binary = self.ollama_binary()
        if not binary:
            raise RuntimeError("Ollama archive extracted, but the ollama binary was not found.")
        return {"ok": True, "message": "Ollama installed.", "path": str(binary)}

    def start_ollama(self) -> dict[str, Any]:
        if self.ollama_ready():
            return {"ok": True, "message": "Ollama is already running."}
        binary = self.ollama_binary()
        if not binary:
            return {"ok": False, "message": "Ollama is not installed yet."}
        with self._lock:
            if self._ollama_process and self._ollama_process.poll() is None:
                return {"ok": True, "message": "Ollama is starting."}
            output = self._log_path("ollama").open("ab", buffering=0)
            self._ollama_process = subprocess.Popen([str(binary), "serve"], stdout=output, stderr=subprocess.STDOUT, env=self._env(), start_new_session=True)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if self.ollama_ready():
                return {"ok": True, "message": "Ollama is running."}
            time.sleep(1)
        return {"ok": False, "message": "Ollama did not become ready within 60 seconds."}

    def stop_ollama(self) -> dict[str, Any]:
        with self._lock:
            process = self._ollama_process
            if not process or process.poll() is not None:
                self._ollama_process = None
                return {"ok": True, "message": "Ollama is not running."}
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=12)
            except Exception:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except Exception:
                    pass
            self._ollama_process = None
        return {"ok": True, "message": "Ollama stopped."}

    def pull_model(self, model: str = DEFAULT_MODEL) -> dict[str, Any]:
        if not self.ollama_ready():
            started = self.start_ollama()
            if not started.get("ok"):
                return started
        names = self.installed_models()
        if model in names:
            return {"ok": True, "message": f"{model} is already installed.", "model": model}
        binary = self.ollama_binary()
        if not binary:
            return {"ok": False, "message": "Ollama binary is unavailable."}
        self._set_state("model-pull", f"Downloading local model {model}…")
        self._run([str(binary), "pull", model], log_name="ollama-pull", timeout=3600)
        return {"ok": True, "message": f"{model} downloaded.", "model": model}

    def install_hermes(self) -> dict[str, Any]:
        binary = self.hermes_binary()
        if binary:
            return {"ok": True, "message": "Hermes Agent is already installed.", "path": str(binary)}
        self._set_state("hermes-install", "Installing NousResearch Hermes Agent…")
        command = "curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash -s -- --skip-browser --skip-setup"
        self._run(["bash", "-lc", command], log_name="hermes-install", timeout=2400)
        binary = self.hermes_binary()
        if not binary:
            raise RuntimeError("Hermes installer finished, but the hermes command was not found.")
        return {"ok": True, "message": "Hermes Agent installed.", "path": str(binary)}

    def configure_hermes(self, model: str = DEFAULT_MODEL) -> dict[str, Any]:
        self.hermes_home.mkdir(parents=True, exist_ok=True)
        config = self.hermes_home / "config.yaml"
        config.write_text(
            "# Managed by Ripo Team Cloud PC\n"
            "model:\n"
            f'  default: "{model}"\n'
            '  provider: "custom"\n'
            f'  base_url: "{OLLAMA_API}/v1"\n'
            '  api_key: "ollama"\n'
            "session_reset:\n"
            '  mode: "both"\n'
            "  idle_minutes: 1440\n"
            "  at_hour: 4\n"
            "gateway:\n"
            "  platforms:\n"
            "    telegram:\n"
            "      extra:\n"
            "        disable_link_previews: true\n",
            encoding="utf-8",
        )
        self._install_ripo_skills()
        return {"ok": True, "message": "Hermes configured for local Ollama and Ripo Team skills.", "model": model}

    def _install_ripo_skills(self) -> None:
        skills: dict[str, tuple[str, str]] = {
            "server-admin": ("Operate and diagnose the Ripo Team Linux server safely.", """Use this skill for server status, processes, logs, CPU/RAM pressure, network checks, and service restarts.\n1. Inspect before changing: `ps aux`, `free -h`, `df -h`, `ss -lntp`, and relevant logs.\n2. Prefer reversible changes and user-space services.\n3. Never print secrets or tokens.\n4. On Hugging Face Spaces, remember the filesystem can be ephemeral and custom inbound TCP/UDP ports are not generally available.\n5. Verify every restart by checking the process and HTTP health endpoint.\n"""),
            "web-dev": ("Build, debug, and deploy Ripo Team web projects.", """Use for HTML/CSS/JavaScript/Python web work.\n- Inspect the repository and existing stack first.\n- Keep mobile layouts touch-friendly and responsive.\n- Run syntax/build checks before deployment.\n- Prefer Git branches and small commits for risky changes.\n- Verify the deployed URL after changes.\n"""),
            "github-ops": ("Safe Git and GitHub workflows for Ripo Team repositories.", """Use for repository maintenance, branches, commits, PRs, and deployment debugging.\n- Never commit API keys, bot tokens, passwords, or `.env` files.\n- Check `git status` and current branch before writes.\n- Use descriptive commits.\n- Inspect CI logs when a deployment fails; do not guess.\n"""),
            "research": ("Research technical questions with source checking and concise synthesis.", """Use for technical research.\n- Prefer official documentation, source repositories, and primary research.\n- Distinguish confirmed facts from inference.\n- For changing software behavior, verify the current version.\n- Summarize findings with the most actionable details first.\n"""),
            "security": ("Security checklist for bots, servers, and automation.", """Use before exposing a service or bot.\n- Secrets belong in environment secret stores, never public Git history.\n- Default-deny access to messaging bots with user allowlists or pairing.\n- Bind internal services to loopback unless they must be public.\n- Validate inputs to shell/tool endpoints.\n- Redact credentials from logs and status pages.\n"""),
            "deploy": ("Deploy and verify the Ripo Team Cloud PC stack.", """Use for GitHub Pages and Hugging Face Space deployments.\n1. Validate code locally or with CI.\n2. Deploy frontend and backend independently.\n3. Poll the Space health endpoint.\n4. Verify the live frontend assets with cache-busting.\n5. Record the exact failing stage if a check does not pass.\n"""),
            "telegram-bot": ("Operate the private Hermes Telegram bot.", """Use for Telegram gateway administration.\n- Require pairing or an explicit allowlist; never enable allow-all for a terminal-capable agent.\n- Never echo the bot token.\n- Use `hermes pairing` commands to approve trusted users.\n- After config changes, restart `hermes gateway` and verify its log.\n"""),
            "local-ai": ("Operate Ollama and the local Qwen model efficiently.", """Use for local inference management.\n- Default model is qwen3:4b through `http://127.0.0.1:11434/v1`.\n- Check `ollama ps` and `ollama list` before pulling duplicates.\n- Keep one model loaded at a time on the Space.\n- Reduce context or switch to a smaller model when latency is too high.\n- Remember ZeroGPU does not automatically accelerate a normal Ollama background process.\n"""),
        }
        base = self.hermes_home / "skills" / "ripo-team"
        for name, (description, body) in skills.items():
            folder = base / name
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "SKILL.md").write_text(
                "---\n"
                f"name: ripo-{name}\n"
                f"description: {description}\n"
                "version: 1.0.0\n"
                "metadata:\n"
                "  hermes:\n"
                f"    tags: [ripo-team, {name}]\n"
                "    category: ripo-team\n"
                "---\n\n"
                f"# Ripo Team {name.replace('-', ' ').title()}\n\n"
                f"{body.strip()}\n",
                encoding="utf-8",
            )

    def install_optional_skills(self) -> dict[str, Any]:
        binary = self.hermes_binary()
        if not binary:
            return {"ok": False, "message": "Hermes is not installed yet."}
        installed: list[str] = []
        failed: list[str] = []
        for skill in ("official/research/arxiv",):
            try:
                self._run([str(binary), "skills", "install", skill, "--now"], log_name="hermes-skills", timeout=300)
                installed.append(skill)
            except Exception:
                failed.append(skill)
        return {"ok": not failed, "installed": installed, "failed": failed}

    def start_gateway(self) -> dict[str, Any]:
        binary = self.hermes_binary()
        if not binary:
            return {"ok": False, "message": "Hermes Agent is not installed yet."}
        if not os.environ.get("TELEGRAM_BOT_TOKEN"):
            return {"ok": False, "message": "TELEGRAM_BOT_TOKEN is not configured in Hugging Face Space secrets."}
        if not self.ollama_ready():
            result = self.start_ollama()
            if not result.get("ok"):
                return result
        with self._lock:
            if self._gateway_process and self._gateway_process.poll() is None:
                return {"ok": True, "message": "Hermes Telegram gateway is already running."}
            output = self._log_path("hermes-gateway").open("ab", buffering=0)
            self._gateway_process = subprocess.Popen([str(binary), "gateway"], stdout=output, stderr=subprocess.STDOUT, env=self._env(), start_new_session=True)
        time.sleep(1.5)
        if self._gateway_process.poll() is not None:
            return {"ok": False, "message": "Hermes gateway exited during startup. Check the Hermes gateway log."}
        return {"ok": True, "message": "Hermes Telegram gateway started. " + ("Telegram is restricted by your allowlist." if os.environ.get("TELEGRAM_ALLOWED_USERS") else "Unknown Telegram users remain denied until paired.")}

    def stop_gateway(self) -> dict[str, Any]:
        with self._lock:
            process = self._gateway_process
            if not process or process.poll() is not None:
                self._gateway_process = None
                return {"ok": True, "message": "Hermes gateway is not running."}
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=12)
            except Exception:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except Exception:
                    pass
            self._gateway_process = None
        return {"ok": True, "message": "Hermes gateway stopped."}

    def approve_pairing(self, code: str) -> dict[str, Any]:
        code = code.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{4,32}", code):
            return {"ok": False, "message": "Invalid pairing code format."}
        binary = self.hermes_binary()
        if not binary:
            return {"ok": False, "message": "Hermes is not installed yet."}
        try:
            completed = subprocess.run([str(binary), "pairing", "approve", "telegram", code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=self._env(), text=True, timeout=60, check=False)
        except Exception as exc:
            return {"ok": False, "message": f"Pairing approval failed: {exc}"}
        output = (completed.stdout or "").strip()
        return {"ok": completed.returncode == 0, "message": output[-1000:] or ("Pairing approved." if completed.returncode == 0 else "Pairing command failed.")}

    def bootstrap(self) -> dict[str, Any]:
        with self._lock:
            if self.state["running"]:
                return {"ok": True, "message": "AI bootstrap is already running."}
            self.state.update(running=True, stage="starting", message="Preparing local AI stack…", started_at=time.time(), finished_at=None, last_error=None)
        try:
            self.install_ollama()
            started = self.start_ollama()
            if not started.get("ok"):
                raise RuntimeError(started.get("message", "Ollama failed to start."))
            pulled = self.pull_model(DEFAULT_MODEL)
            if not pulled.get("ok"):
                raise RuntimeError(pulled.get("message", "Model pull failed."))
            self.install_hermes()
            self.configure_hermes(DEFAULT_MODEL)
            self.install_optional_skills()
            if os.environ.get("TELEGRAM_BOT_TOKEN"):
                self.start_gateway()
            self._set_state("ready", "Local AI and Hermes are ready.")
            return {"ok": True, "message": "Local AI and Hermes are ready."}
        except Exception as exc:
            self._set_state("error", "AI bootstrap failed.", error=str(exc))
            return {"ok": False, "message": str(exc)}
        finally:
            with self._lock:
                self.state["running"] = False
                self.state["finished_at"] = time.time()

    def bootstrap_async(self) -> dict[str, Any]:
        with self._lock:
            if self._bootstrap_thread and self._bootstrap_thread.is_alive():
                return {"ok": True, "message": "AI bootstrap is already running."}
            self._bootstrap_thread = threading.Thread(target=self.bootstrap, daemon=True)
            self._bootstrap_thread.start()
        return {"ok": True, "message": "AI bootstrap started in the background."}

    def status(self) -> dict[str, Any]:
        models = self.installed_models() if self.ollama_ready() else []
        skills_dir = self.hermes_home / "skills"
        skill_count = len(list(skills_dir.rglob("SKILL.md"))) if skills_dir.exists() else 0
        plugin_paths: set[Path] = set()
        for root in (self.hermes_home / "plugins", self.hermes_home / "hermes-agent" / "plugins"):
            if root.exists():
                plugin_paths.update(root.rglob("plugin.yaml"))
        gateway_running = bool(self._gateway_process and self._gateway_process.poll() is None)
        return {
            "ok": True,
            "model": {"name": DEFAULT_MODEL, "installed": DEFAULT_MODEL in models, "available_models": models, "endpoint": f"{OLLAMA_API}/v1", "context_length": int(self._env()["OLLAMA_CONTEXT_LENGTH"])},
            "ollama": {"installed": self.ollama_binary() is not None, "running": self.ollama_ready()},
            "hermes": {"installed": self.hermes_binary() is not None, "gateway_running": gateway_running, "skills": skill_count, "plugins": len(plugin_paths)},
            "telegram": {
                "token_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
                "allowlist_configured": bool(os.environ.get("TELEGRAM_ALLOWED_USERS")),
                "access_mode": "allowlist" if os.environ.get("TELEGRAM_ALLOWED_USERS") else "default-deny-pairing",
                "sealing": self.sealed_secrets.status(),
            },
            "bootstrap": dict(self.state),
            "storage": {"model_directory": str(self.ollama_models), "ephemeral": True},
        }

    def read_log(self, name: str, max_bytes: int = 30000) -> str:
        allowed = {"ollama-install", "ollama", "ollama-pull", "hermes-install", "hermes-skills", "hermes-gateway"}
        if name not in allowed:
            return "Unknown AI log."
        path = self._log_path(name)
        if not path.exists():
            return "No log output yet."
        return path.read_bytes()[-max_bytes:].decode("utf-8", errors="replace")

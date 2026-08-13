from __future__ import annotations

import json
import secrets
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from fastapi import Body, Header, HTTPException
from fastapi.responses import JSONResponse


COMMANDS = {
    "start_live",
    "stop_live",
    "toggle_mic",
    "scene_next",
    "scene_prev",
    "guest_panel",
    "guest_accept",
    "guest_decline",
    "refresh_live_studio",
}


class LiveStudioBridge:
    def __init__(self, data_dir: Path, tiktok_ai: Any, authorize: Callable[[str | None], None]) -> None:
        self.data_dir = data_dir
        self.tiktok_ai = tiktok_ai
        self.authorize = authorize
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.agents_file = self.data_dir / "live-studio-agents.json"
        self.pair_codes: dict[str, float] = {}
        self.agents: dict[str, dict[str, Any]] = {}
        self.commands: deque[dict[str, Any]] = deque(maxlen=100)
        self.results: deque[dict[str, Any]] = deque(maxlen=100)
        self.next_id = 0
        try:
            saved = json.loads(self.agents_file.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                self.agents = saved
        except Exception:
            pass

    def _save_agents(self) -> None:
        self.agents_file.write_text(json.dumps(self.agents, indent=2), encoding="utf-8")

    def _control_auth(self, token: str | None) -> None:
        if self.tiktok_ai.session_valid(token):
            return
        self.authorize(token)

    def _agent(self, token: str | None) -> tuple[str, dict[str, Any]]:
        if not token:
            raise HTTPException(401, "LIVE Studio agent token required.")
        for agent_id, row in self.agents.items():
            saved = str(row.get("token") or "")
            if saved and secrets.compare_digest(saved, token):
                row["last_seen"] = time.time()
                return agent_id, row
        raise HTTPException(401, "Invalid LIVE Studio agent token.")

    def status(self) -> dict[str, Any]:
        now = time.time()
        public_agents = []
        for agent_id, row in self.agents.items():
            last_seen = float(row.get("last_seen") or 0)
            public_agents.append({
                "id": agent_id,
                "name": row.get("name") or "Windows LIVE Studio",
                "online": bool(last_seen and now - last_seen < 12),
                "last_seen": last_seen or None,
                "live_studio_running": bool(row.get("live_studio_running")),
                "last_command": row.get("last_command") or "",
                "last_result": row.get("last_result") or "",
            })
        return {
            "ok": True,
            "agents": public_agents,
            "online": any(row["online"] for row in public_agents),
            "pending_commands": len(self.commands),
            "supported_commands": sorted(COMMANDS),
            "requires_windows_live_studio": True,
        }

    def new_pair_code(self) -> dict[str, Any]:
        now = time.time()
        for key, expires in list(self.pair_codes.items()):
            if expires < now:
                self.pair_codes.pop(key, None)
        code = f"{secrets.randbelow(1_000_000):06d}"
        self.pair_codes[code] = now + 600
        return {"ok": True, "code": code, "expires_seconds": 600}

    def register(self, code: str, name: str) -> dict[str, Any]:
        code = str(code or "").strip()
        expires = self.pair_codes.pop(code, 0)
        if not expires or expires < time.time():
            raise HTTPException(400, "Pairing code is invalid or expired.")
        agent_id = secrets.token_hex(8)
        token = secrets.token_urlsafe(40)
        self.agents[agent_id] = {
            "token": token,
            "name": (str(name or "Windows LIVE Studio").strip() or "Windows LIVE Studio")[:80],
            "created_at": time.time(),
            "last_seen": time.time(),
            "live_studio_running": False,
            "last_command": "",
            "last_result": "",
        }
        self._save_agents()
        return {"ok": True, "agent_id": agent_id, "agent_token": token}

    def queue(self, command: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        command = str(command or "").strip()
        if command not in COMMANDS:
            raise HTTPException(400, "Unsupported LIVE Studio command.")
        if not self.status()["online"]:
            raise HTTPException(409, "Windows LIVE Studio agent is offline. Keep the Windows companion running.")
        self.next_id += 1
        row = {
            "id": self.next_id,
            "command": command,
            "args": args or {},
            "created_at": time.time(),
            "claimed_by": None,
        }
        self.commands.append(row)
        return {"ok": True, "queued": True, "command_id": row["id"], "command": command}

    def poll(self, token: str | None, state: dict[str, Any]) -> dict[str, Any]:
        agent_id, agent = self._agent(token)
        agent["live_studio_running"] = bool(state.get("live_studio_running"))
        agent["last_seen"] = time.time()
        for row in self.commands:
            if not row.get("claimed_by"):
                row["claimed_by"] = agent_id
                agent["last_command"] = row["command"]
                return {"ok": True, "command": row}
        return {"ok": True, "command": None}

    def complete(self, token: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        agent_id, agent = self._agent(token)
        command_id = int(payload.get("command_id") or 0)
        ok = bool(payload.get("ok"))
        message = str(payload.get("message") or "")[:500]
        agent["last_result"] = message
        agent["last_seen"] = time.time()
        self.results.append({
            "command_id": command_id,
            "agent_id": agent_id,
            "ok": ok,
            "message": message,
            "time": time.time(),
        })
        for row in list(self.commands):
            if int(row.get("id") or 0) == command_id:
                try:
                    self.commands.remove(row)
                except ValueError:
                    pass
                break
        return {"ok": True}


def install_live_studio_routes(app: Any, bridge: LiveStudioBridge) -> None:
    @app.get("/api/tiktok/live-studio/status")
    async def live_studio_status() -> JSONResponse:
        return JSONResponse(bridge.status())

    @app.post("/api/tiktok/live-studio/pair")
    async def live_studio_pair(x_admin_token: str | None = Header(default=None)) -> JSONResponse:
        bridge._control_auth(x_admin_token)
        return JSONResponse(bridge.new_pair_code())

    @app.post("/api/tiktok/live-studio/command")
    async def live_studio_command(
        payload: dict[str, Any] = Body(default_factory=dict),
        x_admin_token: str | None = Header(default=None),
    ) -> JSONResponse:
        bridge._control_auth(x_admin_token)
        return JSONResponse(bridge.queue(str(payload.get("command") or ""), payload.get("args") if isinstance(payload.get("args"), dict) else {}))

    @app.post("/api/tiktok/live-studio/agent/register")
    async def live_studio_agent_register(payload: dict[str, Any] = Body(default_factory=dict)) -> JSONResponse:
        return JSONResponse(bridge.register(str(payload.get("code") or ""), str(payload.get("name") or "")))

    @app.post("/api/tiktok/live-studio/agent/poll")
    async def live_studio_agent_poll(
        payload: dict[str, Any] = Body(default_factory=dict),
        x_live_agent_token: str | None = Header(default=None),
    ) -> JSONResponse:
        return JSONResponse(bridge.poll(x_live_agent_token, payload))

    @app.post("/api/tiktok/live-studio/agent/result")
    async def live_studio_agent_result(
        payload: dict[str, Any] = Body(default_factory=dict),
        x_live_agent_token: str | None = Header(default=None),
    ) -> JSONResponse:
        return JSONResponse(bridge.complete(x_live_agent_token, payload))

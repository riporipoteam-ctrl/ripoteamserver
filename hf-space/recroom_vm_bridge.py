from __future__ import annotations

import os
import secrets
import time
import types
import uuid
from typing import Any

from fastapi import HTTPException

from recroom_broker import HostRecord, SessionRecord, TARGET_BUILD_ID, _hash_token
from recroom_vm_pool import RecRoomVmPool, install_recroom_vm_routes


def attach_recroom_vm_pool(app: Any, broker: Any, data_dir: Any) -> RecRoomVmPool:
    """Make RipoTeamServer itself provision one disposable Windows VM/player.

    This replaces the old requirement that a separately paired Windows PC must
    already be online. Existing manual-host routes remain available for admin
    diagnostics, but public allocations prefer/require the server-owned VM pool
    by default.
    """

    public_base = os.environ.get("RECROOM_PUBLIC_BASE_URL", "https://echoxr-ripoteam-cloud-pc.hf.space").rstrip("/")
    pool = RecRoomVmPool(data_dir / "recroom-vms", public_base, broker.host_key)
    broker.vm_pool = pool
    vm_only = os.environ.get("RECROOM_VM_ONLY", "1").strip() not in {"0", "false", "False"}

    original_allocate = broker.allocate
    original_release_locked = broker._release_locked
    original_status = broker.status
    original_heartbeat = broker.heartbeat
    original_mark_ready = broker.mark_ready
    original_public_session = broker.public_session

    def allocate(self: Any, identity_payload: dict[str, Any], build_id: str):
        if not vm_only:
            try:
                return original_allocate(identity_payload, build_id)
            except HTTPException as exc:
                if exc.status_code != 503:
                    raise

        self.cleanup()
        if build_id != TARGET_BUILD_ID:
            raise HTTPException(status_code=400, detail=f"Unsupported build {build_id!r}.")

        can_start, reason = pool.can_provision()
        if not can_start:
            capability = pool.capability()
            detail = reason or capability.get("reason") or "RipoTeamServer VM runtime is unavailable."
            warning = capability.get("warning")
            if warning:
                detail = f"{detail} {warning}"
            raise HTTPException(
                status_code=503,
                detail=f"RipoTeamServer cannot create the disposable Windows Rec Room VM yet: {detail}",
            )

        account = identity_payload["account"]
        recnet_session_token = str(identity_payload["sessionToken"])
        uid = str(account.get("uid") or identity_payload.get("uid") or "")
        if not uid:
            uid = f"account:{account.get('accountId', 'unknown')}"

        host_id = f"ripo-vm-{uuid.uuid4().hex[:16]}"
        session_id = str(uuid.uuid4())
        access_token = secrets.token_urlsafe(32)
        now = time.time()
        host = HostRecord(
            host_id=host_id,
            name=f"RipoTeamServer VM {host_id[-8:]}",
            builds={build_id},
            capacity=1,
            metadata={
                "vmPool": True,
                "provider": "kvm",
                "phase": "queued",
                "progress": 1,
            },
        )
        session = SessionRecord(
            session_id=session_id,
            access_token_hash=_hash_token(access_token),
            uid=uid,
            account=account,
            host_id=host_id,
            build_id=build_id,
            created_at=now,
            expires_at=now + int(os.environ.get("RECROOM_SESSION_TTL_SECONDS", "7200")),
            state="starting",
            host_details={
                "provider": "ripoteam-kvm",
                "phase": "queued",
                "progress": 1,
            },
        )
        host.active_sessions.add(session_id)
        host.jobs.append(
            {
                "type": "start-session",
                "sessionId": session_id,
                "buildId": build_id,
                "gatewayUrl": self.gateway_url,
                "recnetSessionToken": recnet_session_token,
                "account": {
                    "accountId": account.get("accountId"),
                    "username": account.get("username"),
                    "displayName": account.get("displayName"),
                    "isAdmin": bool(account.get("isAdmin")),
                },
            }
        )
        with self.lock:
            self.hosts[host_id] = host
            self.sessions[session_id] = session

        def on_progress(phase: str, progress: int) -> None:
            with self.lock:
                current = self.sessions.get(session_id)
                current_host = self.hosts.get(host_id)
                if current:
                    current.host_details.update({"provider": "ripoteam-kvm", "phase": phase, "progress": progress})
                if current_host:
                    current_host.metadata.update({"phase": phase, "progress": progress})

        def on_failed(error: str) -> None:
            try:
                self.mark_failed(host_id, session_id, error)
            except Exception:
                pass

        started, start_error = pool.provision(host_id, on_progress, on_failed)
        if not started:
            with self.lock:
                self.sessions.pop(session_id, None)
                self.hosts.pop(host_id, None)
            raise HTTPException(status_code=503, detail=start_error or "RipoTeamServer could not start the Windows VM.")

        self._audit(
            "session.vm.allocate",
            session_id=session_id,
            host_id=host_id,
            build_id=build_id,
            provider="kvm",
        )
        return session, access_token

    def release_locked(self: Any, session_id: str, reason: str) -> None:
        session = self.sessions.get(session_id)
        host_id = session.host_id if session else ""
        host = self.hosts.get(host_id) if host_id else None
        server_vm = bool(host and host.metadata.get("vmPool"))
        original_release_locked(session_id, reason)
        if server_vm and host_id:
            self.hosts.pop(host_id, None)
            pool.destroy(host_id)
            self._audit("session.vm.destroy", session_id=session_id, host_id=host_id, reason=reason)

    def heartbeat(self: Any, host_id: str, payload: dict[str, Any]):
        host = original_heartbeat(host_id, payload)
        if host.metadata.get("vmPool"):
            host.metadata.update({"phase": "launching-game", "progress": 70})
            with self.lock:
                for session_id in list(host.active_sessions):
                    session = self.sessions.get(session_id)
                    if session and session.state == "starting":
                        session.host_details.update({"phase": "launching-game", "progress": 70})
        return host

    def mark_ready(self: Any, host_id: str, session_id: str, payload: dict[str, Any]):
        rewritten = dict(payload)
        stream_url = str(rewritten.get("streamUrl") or "")
        if stream_url:
            rewritten["streamUrl"] = pool.rewrite_stream_url(host_id, stream_url)
        session = original_mark_ready(host_id, session_id, rewritten)
        session.host_details.update({"provider": "ripoteam-kvm", "phase": "ready", "progress": 100})
        return session

    def public_session(self: Any, session: Any, access_token: str | None = None) -> dict[str, Any]:
        payload = original_public_session(session, access_token)
        details = session.host_details if isinstance(session.host_details, dict) else {}
        vm_progress = pool.progress(session.host_id)
        payload["provider"] = details.get("provider") or ("ripoteam-kvm" if vm_progress else "remote")
        payload["phase"] = "ready" if session.state == "ready" else details.get("phase") or (vm_progress or {}).get("phase") or session.state
        payload["progress"] = 100 if session.state == "ready" else int(details.get("progress") or (vm_progress or {}).get("progress") or 0)
        return payload

    def status(self: Any) -> dict[str, Any]:
        payload = original_status()
        capability = pool.capability()
        payload["vmRuntime"] = capability
        payload["mode"] = "server-vm"
        payload["configured"] = bool(payload.get("configured") and capability.get("supported"))
        payload["vmReadyForGame"] = bool(capability.get("readyForGame"))
        return payload

    broker.allocate = types.MethodType(allocate, broker)
    broker._release_locked = types.MethodType(release_locked, broker)
    broker.heartbeat = types.MethodType(heartbeat, broker)
    broker.mark_ready = types.MethodType(mark_ready, broker)
    broker.public_session = types.MethodType(public_session, broker)
    broker.status = types.MethodType(status, broker)

    install_recroom_vm_routes(app, pool)
    return pool

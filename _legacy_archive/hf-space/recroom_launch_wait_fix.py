from __future__ import annotations

import os
import secrets
import threading
import time
import types
import uuid
from typing import Any

from fastapi import HTTPException

from recroom_broker import HostRecord, SessionRecord, TARGET_BUILD_ID, _hash_token


_PATCH_REVISION = "client-restore-wait-v1"


def install_launch_wait_fix(broker: Any, wine_pool: Any, client_installer: Any) -> None:
    """Turn the ephemeral Space client restore into a wait state, not a launch failure.

    Hugging Face rebuilds wipe the local May-2022 client directory. The installer
    restores the pinned archive automatically, but players can click Play before
    that multi-GB restore finishes. In that case we allocate the session now,
    expose restore progress, and automatically continue into Wine when the exact
    fingerprint becomes game-ready.
    """

    if getattr(broker, "_ripo_client_restore_wait_revision", "") == _PATCH_REVISION:
        return

    original_allocate = broker.allocate
    wait_seconds = max(120, int(os.environ.get("RECROOM_CLIENT_WAIT_SECONDS", "720")))

    def installer_snapshot() -> dict[str, Any]:
        try:
            with client_installer.lock:
                job_id = client_installer.active_job_id
                if not job_id:
                    return {}
                job = client_installer.jobs.get(job_id)
                return dict(job) if isinstance(job, dict) else {}
        except Exception:
            return {}

    def restore_phase(job: dict[str, Any]) -> tuple[str, int]:
        state = str(job.get("state") or "queued")
        raw = max(0, min(100, int(job.get("progress") or 0)))
        # Reserve 46%+ for the real runtime launch. Client restore stays below it.
        mapped = max(8, min(44, 8 + int(raw * 0.36)))
        labels = {
            "queued": "waiting-for-game-image",
            "downloading": "downloading-game-image",
            "extracting": "extracting-game-image",
            "validating": "validating-game-image",
            "installing": "installing-game-image",
            "ready": "game-image-ready",
            "failed": "game-image-restore-failed",
        }
        return labels.get(state, "restoring-game-image"), mapped

    def allocate(self: Any, identity_payload: dict[str, Any], build_id: str):
        try:
            return original_allocate(identity_payload, build_id)
        except HTTPException as exc:
            if exc.status_code != 503 or build_id != TARGET_BUILD_ID:
                raise

            runtime_preference = os.environ.get("RECROOM_RUNTIME_PROVIDER", "auto").strip().lower() or "auto"
            capability = wine_pool.capability()
            checks = capability.get("checks") if isinstance(capability.get("checks"), dict) else {}
            client_missing = not bool(checks.get("client"))
            archive_configured = bool(os.environ.get("RECROOM_WINE_CLIENT_ARCHIVE_URL", "").strip())
            if runtime_preference == "kvm" or not client_missing or not archive_configured:
                raise

            max_slots = max(1, int(capability.get("maxSandboxes") or 2))
            running = max(0, int(capability.get("runningSandboxes") or 0))
            with self.lock:
                waiting = sum(
                    1
                    for host in self.hosts.values()
                    if bool((host.metadata or {}).get("waitForClient"))
                )
            if running + waiting >= max_slots:
                raise HTTPException(
                    status_code=503,
                    detail="RipoTeamServer is restoring the May 19 2022 game image and all launch slots are currently reserved.",
                )

            account = identity_payload["account"]
            recnet_session_token = str(identity_payload["sessionToken"])
            uid = str(account.get("uid") or identity_payload.get("uid") or "")
            if not uid:
                uid = f"account:{account.get('accountId', 'unknown')}"

            job = installer_snapshot()
            phase, progress = restore_phase(job)
            host_id = f"ripo-wine-{uuid.uuid4().hex[:16]}"
            session_id = str(uuid.uuid4())
            access_token = secrets.token_urlsafe(32)
            now = time.time()

            host = HostRecord(
                host_id=host_id,
                name=f"RipoTeamServer WINE {host_id[-8:]}",
                builds={build_id},
                capacity=1,
                metadata={
                    "runtimePool": True,
                    "winePool": True,
                    "provider": "wine",
                    "waitForClient": True,
                    "phase": phase,
                    "progress": progress,
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
                    "provider": "ripoteam-wine",
                    "phase": phase,
                    "progress": progress,
                    "waitingForGameImage": True,
                },
            )
            host.active_sessions.add(session_id)
            with self.lock:
                self.hosts[host_id] = host
                self.sessions[session_id] = session

            def on_progress(runtime_phase: str, runtime_progress: int) -> None:
                with self.lock:
                    current = self.sessions.get(session_id)
                    current_host = self.hosts.get(host_id)
                    if current:
                        current.host_details.update(
                            {
                                "provider": "ripoteam-wine",
                                "phase": runtime_phase,
                                "progress": runtime_progress,
                                "waitingForGameImage": runtime_phase.endswith("game-image"),
                            }
                        )
                    if current_host:
                        current_host.last_heartbeat = time.time()
                        current_host.metadata.update({"phase": runtime_phase, "progress": runtime_progress})
                        if not runtime_phase.endswith("game-image"):
                            current_host.metadata.pop("waitForClient", None)

            def on_failed(error: str) -> None:
                try:
                    self.mark_failed(host_id, session_id, error)
                except Exception:
                    pass

            def on_ready(stream_url: str) -> None:
                try:
                    self.mark_ready(
                        host_id,
                        session_id,
                        {
                            "streamUrl": stream_url,
                            "resolution": "1280x720",
                            "streamer": "ripo-wine-browser",
                        },
                    )
                except Exception as ready_exc:
                    on_failed(f"Could not publish the browser stream: {ready_exc}")

            def wait_then_launch() -> None:
                deadline = time.time() + wait_seconds
                last_reason = "May 19 2022 game image is restoring."
                while time.time() < deadline:
                    with self.lock:
                        current = self.sessions.get(session_id)
                    if current is None or current.state != "starting":
                        return

                    current_capability = wine_pool.capability()
                    if current_capability.get("readyForGame"):
                        on_progress("preparing-windows-runtime", 24)
                        started, start_error = wine_pool.provision(
                            host_id,
                            session_id,
                            recnet_session_token,
                            on_progress,
                            on_ready,
                            on_failed,
                        )
                        if not started:
                            on_failed(start_error or "RipoTeamServer could not start the restored May 19 2022 runtime.")
                        return

                    last_reason = str(current_capability.get("reason") or last_reason)
                    current_job = installer_snapshot()
                    if str(current_job.get("state") or "") == "failed":
                        detail = str(current_job.get("error") or "The server game image restore failed.")
                        on_failed(f"RipoTeamServer could not restore the May 19 2022 game image: {detail}")
                        return
                    current_phase, current_progress = restore_phase(current_job)
                    on_progress(current_phase, current_progress)
                    time.sleep(2.0)

                on_failed(
                    "RipoTeamServer timed out restoring the May 19 2022 game image. "
                    f"Last runtime status: {last_reason}"
                )

            threading.Thread(
                target=wait_then_launch,
                name=f"recroom-client-wait-{session_id[:8]}",
                daemon=True,
            ).start()

            self._audit(
                "session.client-restore-wait",
                session_id=session_id,
                host_id=host_id,
                build_id=build_id,
                wait_seconds=wait_seconds,
            )
            return session, access_token

    broker.allocate = types.MethodType(allocate, broker)
    broker._ripo_client_restore_wait_revision = _PATCH_REVISION
    print(f"Rec Room client restore wait patch loaded: {_PATCH_REVISION}")

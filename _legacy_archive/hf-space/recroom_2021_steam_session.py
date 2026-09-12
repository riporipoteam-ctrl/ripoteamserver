from __future__ import annotations

import types
from typing import Any


_PATCH_REVISION = "aug25-2021-steam-session-phase-v1"


def expose_steam_phase(broker: Any) -> None:
    if getattr(broker, "_ripo_steam_phase_patch", False):
        return
    original_public_session = broker.public_session

    def public_session(self: Any, session: Any, access_token: str | None = None) -> dict[str, Any]:
        payload = original_public_session(session, access_token)
        details = session.host_details if isinstance(getattr(session, "host_details", None), dict) else {}
        phase = str(details.get("phase") or "")
        if phase.startswith("steam-") or phase in {"launching-game", "starting-browser-stream"}:
            payload["phase"] = phase
            payload["progress"] = int(details.get("progress") or payload.get("progress") or 0)
            payload["interactiveStream"] = bool(payload.get("streamUrl"))
            payload["gameReady"] = False
        elif session.state == "ready":
            payload["phase"] = "ready"
            payload["progress"] = 100
            payload["gameReady"] = True
        return payload

    broker.public_session = types.MethodType(public_session, broker)
    broker._ripo_steam_phase_patch = True
    print(f"Rec Room Steam interactive-session phase loaded: {_PATCH_REVISION}")

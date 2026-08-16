from __future__ import annotations

import time
from typing import Any

from fastapi import Header
from fastapi.responses import JSONResponse

from recroom_gateway import PHOTON_REGION, NativeSession, RecRoomGateway
from recroom_match_model import (
    DORM_SCENE_LOCATION_ID,
    ORIENTATION_INSTANCE_ID,
    ORIENTATION_ROOM_ID,
    build_orientation_instance,
    build_player,
    build_room_instance,
)


def _remove_exact_route(app: Any, path: str, methods: set[str]) -> None:
    routes = getattr(getattr(app, "router", None), "routes", None)
    if not isinstance(routes, list):
        return
    kept = []
    for route in routes:
        if getattr(route, "path", None) != path:
            kept.append(route)
            continue
        route_methods = set(getattr(route, "methods", set()) or set())
        if not (route_methods & methods):
            kept.append(route)
    routes[:] = kept


def _room_instance(session: NativeSession, room_id: int, location: str, *, private: bool) -> dict[str, Any]:
    current = session.state.get("currentRoomInstance")
    instance = build_room_instance(
        account_id=session.account_id,
        room_id=room_id,
        location=location,
        photon_region=PHOTON_REGION,
        private=private,
        existing=current if isinstance(current, dict) else None,
    )
    session.state["currentRoomInstance"] = instance
    session.state["lastRoomId"] = int(room_id)
    return instance


def _orientation_instance(session: NativeSession) -> dict[str, Any]:
    # Orientation is a client-local onboarding room. Keep the recovered sentinel
    # -2 and do NOT set lastRoomId here; setting it would make the next heartbeat
    # look like normal matchmaking and prematurely kick a new player to Dorm.
    current = session.state.get("currentRoomInstance")
    if isinstance(current, dict) and int(current.get("roomInstanceId") or 0) == ORIENTATION_INSTANCE_ID:
        return current
    instance = build_orientation_instance(photon_region="us")
    session.state["currentRoomInstance"] = instance
    return instance


def _player(session: NativeSession) -> dict[str, Any]:
    return build_player(
        account_id=session.account_id,
        username=session.username,
        display_name=session.display_name,
        now_ms=int(time.time() * 1000),
    )


def install_recroom_match_compat_routes(app: Any, gateway: RecRoomGateway) -> None:
    """Override join/heartbeat DTOs with recovered old-client room presence."""

    for path, methods in [
        ("/Matchmaking/matchmake/dorm", {"POST"}),
        ("/Matchmaking/matchmake/v2/room/{room_id}", {"POST"}),
        ("/Matchmaking/player/heartbeat", {"POST"}),
    ]:
        _remove_exact_route(app, path, methods)

    def session_for(authorization: str | None) -> NativeSession:
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        return gateway.from_token(token)

    @app.post("/Matchmaking/matchmake/dorm")
    async def rr2022_matchmake_dorm(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        room_id = int(session.state.get("dormRoomId") or session.account_id + 1_000_000_000)
        # Calling Dorm matchmaking is the transition out of first-run Orientation.
        gateway.save(session, {"dormRoomId": room_id, "lastRoomId": room_id})
        instance = _room_instance(session, room_id, DORM_SCENE_LOCATION_ID, private=True)
        return JSONResponse({"errorCode": 0, "roomInstance": instance})

    @app.post("/Matchmaking/matchmake/v2/room/{room_id}")
    async def rr2022_matchmake_room(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        gateway.save(session, {"lastRoomId": room_id})
        instance = _room_instance(session, room_id, f"FluxRoom_{room_id}", private=False)
        return JSONResponse({"errorCode": 0, "roomInstance": instance})

    @app.post("/Matchmaking/player/heartbeat")
    async def rr2022_player_heartbeat(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)

        # Critical first-run behavior: newly created accounts have never entered
        # a matched room. The 2022-era client loads Orientation locally and expects
        # heartbeat to echo room 13 / instance -2. A normal Dorm instance here can
        # produce the black/empty-world onboarding failure.
        if "lastRoomId" not in session.state:
            current = _orientation_instance(session)
            return JSONResponse(
                {
                    "errorCode": 0,
                    "player": _player(session),
                    "roomInstance": current,
                    "serverTime": int(time.time() * 1000),
                }
            )

        dorm_id = int(session.state.get("dormRoomId") or session.account_id + 1_000_000_000)
        room_id = int(session.state.get("lastRoomId") or dorm_id)
        current = session.state.get("currentRoomInstance")
        location = DORM_SCENE_LOCATION_ID if room_id == dorm_id else f"FluxRoom_{room_id}"
        current = build_room_instance(
            account_id=session.account_id,
            room_id=room_id,
            location=location,
            photon_region=PHOTON_REGION,
            private=room_id == dorm_id,
            existing=current if isinstance(current, dict) else None,
        )
        session.state["currentRoomInstance"] = current
        return JSONResponse(
            {
                "errorCode": 0,
                "player": _player(session),
                "roomInstance": current,
                "serverTime": int(time.time() * 1000),
            }
        )

    @app.get("/Matchmaking/roominstance/{instance_id}")
    async def rr2022_room_instance(instance_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)

        if int(instance_id) == ORIENTATION_INSTANCE_ID and "lastRoomId" not in session.state:
            return JSONResponse(_orientation_instance(session))

        dorm_id = int(session.state.get("dormRoomId") or session.account_id + 1_000_000_000)
        room_id = int(session.state.get("lastRoomId") or dorm_id)
        current = session.state.get("currentRoomInstance")
        location = DORM_SCENE_LOCATION_ID if room_id == dorm_id else f"FluxRoom_{room_id}"
        current = build_room_instance(
            account_id=session.account_id,
            room_id=room_id,
            location=location,
            photon_region=PHOTON_REGION,
            private=room_id == dorm_id,
            existing=current if isinstance(current, dict) else None,
        )
        if int(current.get("roomInstanceId") or -1) != instance_id:
            current = dict(current)
            current["roomInstanceId"] = int(instance_id)
            room_name = "DormRoom" if room_id == dorm_id else f"FluxRoom_{room_id}"
            current["photonRoomId"] = f"FluxRecRoom2022-{room_name}-1-{room_id}-{instance_id}"
            current["inviteCode"] = str(instance_id)
        return JSONResponse(current)

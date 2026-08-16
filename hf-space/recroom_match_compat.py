from __future__ import annotations

import time
from typing import Any

from fastapi import Header
from fastapi.responses import JSONResponse

from recroom_gateway import PHOTON_REGION, NativeSession, RecRoomGateway
from recroom_match_model import DORM_SCENE_LOCATION_ID, build_player, build_room_instance


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


def _player(session: NativeSession) -> dict[str, Any]:
    return build_player(
        account_id=session.account_id,
        username=session.username,
        display_name=session.display_name,
        now_ms=int(time.time() * 1000),
    )


def install_recroom_match_compat_routes(app: Any, gateway: RecRoomGateway) -> None:
    """Override join/heartbeat DTOs with the recovered room-instance shape."""

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
        gateway.save(session, {"dormRoomId": room_id, "lastRoomId": room_id})
        # Critical: location is the Unity scene location GUID used by the real
        # Dorm subroom, not the human-readable room name.
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
        # Do not mutate the active session when the client asks about another
        # instance id. Return a normalized copy with a string Photon room name.
        if int(current.get("roomInstanceId") or -1) != instance_id:
            current = dict(current)
            current["roomInstanceId"] = int(instance_id)
            room_name = "DormRoom" if room_id == dorm_id else f"FluxRoom_{room_id}"
            current["photonRoomId"] = f"FluxRecRoom2022-{room_name}-1-{room_id}-{instance_id}"
            current["inviteCode"] = str(instance_id)
        return JSONResponse(current)

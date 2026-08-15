from __future__ import annotations

import time
from typing import Any

from fastapi import Header
from fastapi.responses import JSONResponse

from recroom_gateway import PHOTON_REGION, NativeSession, RecRoomGateway


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
    if isinstance(current, dict) and int(current.get("roomId") or -1) == room_id:
        return current

    room_instance_id = int(session.account_id * 100_000 + (room_id % 100_000))
    instance = {
        "roomInstanceId": room_instance_id,
        "roomId": room_id,
        "subRoomId": 1,
        "location": location,
        "photonRegionId": PHOTON_REGION,
        "photonRoomId": room_instance_id,
        "name": location,
        "maxCapacity": 4 if private else 8,
        "isFull": False,
        "isPrivate": private,
        "isInProgress": False,
        "roomInstanceType": 0,
        "isMatchmakingSocial": False,
        "dataBlobName": "",
        "dataBlobChecksum": "",
        "dataBlob": None,
        "matchMakingPolicy": 0,
        "inviteCode": str(room_instance_id),
    }
    session.state["currentRoomInstance"] = instance
    session.state["lastRoomId"] = room_id
    return instance


def _player(session: NativeSession) -> dict[str, Any]:
    return {
        "playerId": session.account_id,
        "accountId": session.account_id,
        "username": session.username,
        "displayName": session.display_name,
        "statusVisibility": 1,
        "platform": "Steam",
        "isOnline": True,
        "lastHeartbeatAt": int(time.time() * 1000),
    }


def install_recroom_match_compat_routes(app: Any, gateway: RecRoomGateway) -> None:
    """Override join/heartbeat DTOs with the room-instance shape old clients use."""

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
        instance = _room_instance(session, room_id, "DormRoom", private=True)
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
        room_id = int(session.state.get("lastRoomId") or session.state.get("dormRoomId") or session.account_id + 1_000_000_000)
        current = session.state.get("currentRoomInstance")
        if not isinstance(current, dict) or int(current.get("roomId") or -1) != room_id:
            location = "DormRoom" if room_id == int(session.state.get("dormRoomId") or -1) else f"FluxRoom_{room_id}"
            current = _room_instance(session, room_id, location, private=location == "DormRoom")
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
        current = session.state.get("currentRoomInstance")
        if not isinstance(current, dict):
            room_id = int(session.state.get("lastRoomId") or session.state.get("dormRoomId") or session.account_id + 1_000_000_000)
            current = _room_instance(session, room_id, "DormRoom", private=True)
        # If the client asks for the active instance, return the active typed DTO.
        # If it asks for another id, preserve the requested id but keep neutral data.
        if int(current.get("roomInstanceId") or -1) != instance_id:
            current = dict(current)
            current["roomInstanceId"] = instance_id
            current["photonRoomId"] = instance_id
            current["inviteCode"] = str(instance_id)
        return JSONResponse(current)

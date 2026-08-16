from __future__ import annotations

from typing import Any

from fastapi import Header
from fastapi.responses import JSONResponse

from recroom_gateway import NativeSession, RecRoomGateway
from recroom_match_model import (
    DORM_ROOM_REPLICATION_ID,
    DORM_SCENE_LOCATION_ID,
    DORM_SCENE_REPLICATION_ID,
    ORIENTATION_ROOM_ID,
    ORIENTATION_ROOM_REPLICATION_ID,
    ORIENTATION_SCENE_LOCATION_ID,
    ORIENTATION_SCENE_REPLICATION_ID,
    ORIENTATION_SUBROOM_ID,
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


def build_dorm_room(session: NativeSession) -> dict[str, Any]:
    room_id = int(session.state.get("dormRoomId") or session.account_id + 1_000_000_000)
    subroom = {
        "Accessibility": 2,
        "CurrentSave": None,
        "IsSandbox": False,
        "MaxPlayers": 1,
        "Name": "Home",
        "RoomId": room_id,
        "SubRoomId": 1,
        "UnitySceneId": DORM_SCENE_LOCATION_ID,
        "ReplicationId": DORM_SCENE_REPLICATION_ID,
        "CanMatchmakeInto": True,
        "SupportsJoinInProgress": False,
    }
    scene = {
        "Name": "Home",
        "ReplicationId": DORM_SCENE_REPLICATION_ID,
        "RoomSceneLocationId": DORM_SCENE_LOCATION_ID,
        "IsSandbox": False,
        "CanMatchmakeInto": True,
        "SupportsJoinInProgress": False,
        "UseLevelBasedMatchmaking": False,
        "UseAgeBasedMatchmaking": False,
        "UseRecRoyaleMatchmaking": False,
        "MaxPlayers": 1,
        "ReleaseStatus": 2,
    }
    return {
        "RoomId": room_id,
        "Name": "DormRoom",
        "Description": "Your private room",
        "CreatorAccountId": session.account_id,
        "ReplicationId": DORM_ROOM_REPLICATION_ID,
        "IsDorm": True,
        "IsDormRoom": True,
        "IsDeveloperOwned": True,
        "IsRRO": True,
        "State": 0,
        "Accessibility": 2,
        "CloningAllowed": False,
        "CloningPermission": 0,
        "MaxPlayerCalculationMode": 1,
        "MaxPlayers": 1,
        "MinLevel": 0,
        "SupportsJuniors": True,
        "SupportsLevelVoting": False,
        "SupportsMobile": True,
        "SupportsQuest2": True,
        "SupportsScreens": True,
        "SupportsTeleportVR": True,
        "SupportsVRLow": True,
        "SupportsWalkVR": True,
        "LoadScreenLocked": False,
        "LoadScreens": [],
        "Tags": [],
        "Roles": [
            {"AccountId": session.account_id, "InvitedRole": 0, "Role": 30},
        ],
        "Stats": {"CheerCount": 0, "FavoriteCount": 0, "VisitCount": 0, "VisitorCount": 0},
        "SubRooms": [subroom],
        "Scenes": [scene],
    }


def build_orientation_room() -> dict[str, Any]:
    subroom = {
        "SubRoomId": ORIENTATION_SUBROOM_ID,
        "RoomId": ORIENTATION_ROOM_ID,
        "CreatorAccountId": None,
        "UnitySceneId": ORIENTATION_SCENE_LOCATION_ID,
        "ReplicationId": ORIENTATION_SCENE_REPLICATION_ID,
        "Name": "Home",
        "IsSandbox": False,
        "MaxPlayers": 1,
        "Accessibility": 1,
        "CanMatchmakeInto": True,
        "SupportsJoinInProgress": False,
        "CurrentSave": None,
    }
    scene = {
        "Name": "Home",
        "ReplicationId": ORIENTATION_SCENE_REPLICATION_ID,
        "RoomSceneLocationId": ORIENTATION_SCENE_LOCATION_ID,
        "IsSandbox": False,
        "CanMatchmakeInto": True,
        "SupportsJoinInProgress": False,
        "UseLevelBasedMatchmaking": False,
        "UseAgeBasedMatchmaking": True,
        "UseRecRoyaleMatchmaking": False,
        "MaxPlayers": 1,
        "ReleaseStatus": 2,
    }
    return {
        "RoomId": ORIENTATION_ROOM_ID,
        "Name": "Orientation",
        "Description": "An introductory tour of Rec Room!",
        "CreatorAccountId": 1,
        "ReplicationId": ORIENTATION_ROOM_REPLICATION_ID,
        "IsDorm": False,
        "IsDormRoom": False,
        "IsDeveloperOwned": True,
        "IsRRO": True,
        "State": 0,
        "Accessibility": 1,
        "CloningAllowed": False,
        "CloningPermission": 0,
        "MaxPlayerCalculationMode": 0,
        "MaxPlayers": 1,
        "MinLevel": 0,
        "SupportsJuniors": True,
        "SupportsLevelVoting": False,
        "SupportsMobile": True,
        "SupportsQuest2": True,
        "SupportsScreens": True,
        "SupportsTeleportVR": True,
        "SupportsVRLow": True,
        "SupportsWalkVR": True,
        "LoadScreenLocked": False,
        "LoadScreens": [],
        "Tags": [{"Tag": "rro", "Type": 2}],
        "Roles": [],
        "Stats": {"CheerCount": 0, "FavoriteCount": 0, "VisitCount": 0, "VisitorCount": 0},
        "SubRooms": [subroom],
        "Scenes": [scene],
    }


def build_generic_room(session: NativeSession, room_id: int) -> dict[str, Any]:
    dorm_id = int(session.state.get("dormRoomId") or session.account_id + 1_000_000_000)
    if int(room_id) == dorm_id:
        return build_dorm_room(session)
    return {
        "RoomId": int(room_id),
        "Name": f"FluxRoom_{int(room_id)}",
        "Description": "Flux compatibility room",
        "CreatorAccountId": session.account_id,
        "IsDorm": False,
        "IsDormRoom": False,
        "IsDeveloperOwned": False,
        "IsRRO": False,
        "State": 0,
        "Accessibility": 1,
        "CloningAllowed": False,
        "CloningPermission": 0,
        "MaxPlayerCalculationMode": 0,
        "MaxPlayers": 8,
        "SupportsScreens": True,
        "SupportsTeleportVR": True,
        "SupportsWalkVR": True,
        "SubRooms": [],
        "Scenes": [],
        "Tags": [],
        "Roles": [],
        "Stats": {"CheerCount": 0, "FavoriteCount": 0, "VisitCount": 0, "VisitorCount": 0},
    }


def install_recroom_room_compat_routes(app: Any, gateway: RecRoomGateway) -> None:
    """Override recovered RRO metadata needed for Dorm and Orientation loading."""
    # The generic dynamic route would otherwise consume /rooms/13 before a later
    # static route could match it, so replace that route while preserving a
    # neutral fallback for arbitrary room ids.
    _remove_exact_route(app, "/Room_server/dormroom/me", {"GET"})
    _remove_exact_route(app, "/Room_server/rooms/{room_id}", {"GET"})

    def session_for(authorization: str | None) -> NativeSession:
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        return gateway.from_token(token)

    @app.get("/Room_server/dormroom/me")
    async def rr2022_dorm_room_me(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        room_id = int(session.state.get("dormRoomId") or session.account_id + 1_000_000_000)
        if int(session.state.get("dormRoomId") or -1) != room_id:
            gateway.save(session, {"dormRoomId": room_id})
        return JSONResponse(build_dorm_room(session))

    @app.get("/Room_server/rooms/{room_id}")
    async def rr2022_room_by_id(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        if int(room_id) == ORIENTATION_ROOM_ID:
            return JSONResponse(build_orientation_room())
        return JSONResponse(build_generic_room(session, room_id))

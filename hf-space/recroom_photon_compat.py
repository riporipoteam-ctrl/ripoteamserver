from __future__ import annotations

from typing import Any

from fastapi import Header
from fastapi.responses import JSONResponse

from recroom_gateway import NativeSession, RecRoomGateway
from recroom_match_model import ORIENTATION_INSTANCE_ID, build_orientation_instance


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


def _permission(name: str, role: int, override: bool) -> dict[str, Any]:
    return {
        "Permission": name,
        "Role": int(role),
        "Override": bool(override),
        "Type": 0,
        "Value": "True",
    }


def _permissions(session: NativeSession) -> list[dict[str, Any]]:
    # This mirrors the old-client permission table recovered from multiple
    # revival servers. Role 30 is co-owner. Role 0 is the global/default role.
    permissions = [
        _permission("CAN_USE_ROOM_RESET_BUTTON", 0, True),
        _permission("CAN_USE_DELETE_ALL_BUTTON", 0, True),
        _permission("CAN_SAVE_INVENTIONS", 0, True),
        _permission("CAN_SPAWN_INVENTIONS", 0, True),
        _permission("CAN_USE_PLAY_GIZMOS_TOGGLE", 0, True),
        _permission("CAN_USE_MAKER_PEN", 30, False),
        _permission("CAN_USE_ROOM_RESET_BUTTON", 30, True),
        _permission("CAN_USE_DELETE_ALL_BUTTON", 30, True),
        _permission("CAN_SAVE_INVENTIONS", 30, True),
        _permission("CAN_SPAWN_INVENTIONS", 30, True),
        _permission("CAN_USE_PLAY_GIZMOS_TOGGLE", 30, True),
    ]

    # The user's own private Dorm is effectively their room. Giving the global
    # maker-pen grant there matches the behavior revival servers use for their
    # developer/owner accounts without globally granting it in arbitrary rooms.
    current = session.state.get("currentRoomInstance")
    dorm_id = int(session.state.get("dormRoomId") or session.account_id + 1_000_000_000)
    if isinstance(current, dict) and int(current.get("roomId") or -1) == dorm_id:
        permissions.insert(0, _permission("CAN_USE_MAKER_PEN", 0, True))
    return permissions


def _current_room_instance_id(session: NativeSession) -> int | None:
    current = session.state.get("currentRoomInstance")
    if isinstance(current, dict):
        try:
            return int(current.get("roomInstanceId"))
        except (TypeError, ValueError):
            pass

    # A truly fresh account sits in the local Orientation sentinel until it
    # explicitly matchmakes into Dorm. Keep the Photon DTO consistent if the
    # client happens to ask for permissions while Orientation is active.
    if "lastRoomId" not in session.state:
        orientation = build_orientation_instance(photon_region="us")
        session.state["currentRoomInstance"] = orientation
        return ORIENTATION_INSTANCE_ID
    return None


def install_recroom_photon_compat_routes(app: Any, gateway: RecRoomGateway) -> None:
    """Replace generic Photon metadata with the DTO old clients deserialize.

    Photon AppId/AppVersion/Region remain in /api/config/v2. This endpoint is the
    per-room access/permission object: RoomInstanceId, PhotonAccessToken, Permissions.
    """

    for path in ("/Room_server/photon_access_token", "/api/rooms/v1/photon_access_token"):
        _remove_exact_route(app, path, {"GET"})

    def session_for(authorization: str | None) -> NativeSession:
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        return gateway.from_token(token)

    async def photon_access(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse(
            {
                "RoomInstanceId": _current_room_instance_id(session),
                # Recovered private-server implementations keep this empty when
                # their Photon application accepts clients without signed custom
                # authentication. Do not substitute the RecNet OAuth JWT: it is
                # a different token with different issuer/audience semantics.
                "PhotonAccessToken": "",
                "Permissions": _permissions(session),
            }
        )

    photon_access.__name__ = "rr2022_photon_access_room_server"
    app.add_api_route("/Room_server/photon_access_token", photon_access, methods=["GET"])

    async def photon_access_legacy(authorization: str | None = Header(default=None)) -> JSONResponse:
        return await photon_access(authorization)

    photon_access_legacy.__name__ = "rr2022_photon_access_api_rooms_v1"
    app.add_api_route("/api/rooms/v1/photon_access_token", photon_access_legacy, methods=["GET"])

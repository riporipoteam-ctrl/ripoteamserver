from __future__ import annotations

import time
from typing import Any

from fastapi import Header, Request, Response
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


async def _remember_login_lock(request: Request, session: NativeSession) -> None:
    """Best-effort capture of the client's LoginLock without making it a gate.

    The recovered service carries LoginLock through presence lifecycle requests.
    The compatibility gateway has one native session per Flux login already, so we
    do not reject beats on this value yet, but retaining it keeps our presence state
    wire-compatible and gives the real-client diagnostic path useful evidence.
    """
    value = ""
    try:
        content_type = (request.headers.get("content-type") or "").lower()
        if "application/json" in content_type:
            body = await request.json()
            if isinstance(body, dict):
                value = str(body.get("LoginLock") or body.get("loginLock") or "")
        else:
            form = await request.form()
            value = str(form.get("LoginLock") or form.get("loginLock") or "")
    except Exception:
        try:
            raw = (await request.body()).decode("utf-8", errors="ignore")
            for item in raw.split("&"):
                if "=" not in item:
                    continue
                key, candidate = item.split("=", 1)
                if key.lower() == "loginlock":
                    value = candidate
                    break
        except Exception:
            pass
    if value:
        session.state["loginLock"] = value[:128]


def install_recroom_match_compat_routes(app: Any, gateway: RecRoomGateway) -> None:
    """Override presence/join DTOs with recovered old-client behavior."""

    # These generic gateway routes return shapes that differ from old Rec Room.
    # In particular, player/login is an empty ACK and a first-run logout must not
    # destroy the synthetic Orientation -2 presence.
    for path, methods in [
        ("/Matchmaking/player/login", {"POST"}),
        ("/Matchmaking/player/logout", {"POST"}),
        ("/Matchmaking/player/exclusivelogin", {"POST"}),
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

    @app.post("/Matchmaking/player/login")
    async def rr2022_player_login(request: Request, authorization: str | None = Header(default=None)) -> Response:
        session = session_for(authorization)
        await _remember_login_lock(request, session)
        # The account-creation bootstrap on the recovered service already has the
        # player seeded into Orientation before login. Our Flux auth exchange has
        # no separate presence store, so seed the equivalent -2 presence here for
        # a genuinely fresh account, but do not set lastRoomId.
        if "lastRoomId" not in session.state:
            _orientation_instance(session)
        # Old client expects a bare/empty 200 ACK here, not a player JSON object.
        return Response(status_code=200)

    @app.post("/Matchmaking/player/exclusivelogin")
    async def rr2022_player_exclusive_login(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        await _remember_login_lock(request, session)
        return JSONResponse({"errorCode": 0})

    @app.post("/Matchmaking/player/logout")
    async def rr2022_player_logout(request: Request, authorization: str | None = Header(default=None)) -> Response:
        session = session_for(authorization)
        await _remember_login_lock(request, session)
        current = session.state.get("currentRoomInstance")
        instance_id = None
        if isinstance(current, dict):
            try:
                instance_id = int(current.get("roomInstanceId"))
            except (TypeError, ValueError):
                instance_id = None

        # The stock client can emit a spurious logout immediately after account
        # creation. Clearing Orientation here destroys the -2 bootstrap and sends
        # the client down the normal Dorm path before onboarding finishes.
        if "lastRoomId" not in session.state or instance_id == ORIENTATION_INSTANCE_ID:
            _orientation_instance(session)
            return Response(status_code=200)

        # A real logout from a normal room clears only the active presence. Keep
        # lastRoomId/dormRoomId as persisted history so the next login is not
        # incorrectly treated as a brand-new account.
        session.state.pop("currentRoomInstance", None)
        return Response(status_code=200)

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
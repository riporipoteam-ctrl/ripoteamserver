from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import Header, Request, Response
from fastapi.responses import JSONResponse

from recroom_compat import _account, _issue_access_token
from recroom_gateway import PHOTON_REGION, NativeSession, RecRoomGateway
from recroom_match_compat import _orientation_instance, _player, _remember_login_lock, _room_instance
from recroom_match_model import DORM_SCENE_LOCATION_ID, ORIENTATION_INSTANCE_ID, ORIENTATION_ROOM_ID, build_room_instance
from recroom_photon_compat import _current_room_instance_id, _permissions
from recroom_room_compat import build_dorm_room, build_generic_room, build_orientation_room


def install_recroom_service_alias_routes(app: Any, gateway: RecRoomGateway) -> None:
    """Expose the paths produced when old `*.rec.net` service hosts are redirected.

    The Windows binary patch preserves every path after the host name and replaces
    hosts such as match.rec.net / rooms.rec.net / accounts.rec.net with distinct
    same-length localhost prefixes. host-proxy.mjs strips those prefixes before
    forwarding, so an old client can legitimately arrive here as `/player/login`,
    `/dormroom/me`, `/account/me`, etc. Newer builds use the unified
    `/Matchmaking`, `/Room_server`, `/Accounts` paths. Both generations must work.
    """

    def session_for(authorization: str | None) -> NativeSession:
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        return gateway.from_token(token)

    def current_presence(session: NativeSession) -> dict[str, Any]:
        if "lastRoomId" not in session.state:
            return _orientation_instance(session)
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
        return current

    def heartbeat_payload(session: NativeSession) -> dict[str, Any]:
        return {
            "errorCode": 0,
            "player": _player(session),
            "roomInstance": current_presence(session),
            "serverTime": int(time.time() * 1000),
        }

    # ------------------------------------------------------------------ auth.rec.net
    async def token_response(authorization: str | None) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse(
            {
                "access_token": _issue_access_token(session),
                "token_type": "Bearer",
                "expires_in": 3600,
                "refresh_token": "flux.refresh." + secrets.token_urlsafe(24),
                "scope": "openid profile rn.api.write rn.match.write rn.chat.write",
            }
        )

    @app.post("/connect/token")
    async def rr_service_auth_token(authorization: str | None = Header(default=None)) -> JSONResponse:
        return await token_response(authorization)

    @app.post("/cachedlogin/forplatformids")
    async def rr_service_cached_login(authorization: str | None = Header(default=None)) -> JSONResponse:
        return await token_response(authorization)

    @app.get("/role/developer")
    async def rr_service_developer_role(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse(True)

    # -------------------------------------------------------------- accounts.rec.net
    @app.get("/account/me")
    async def rr_service_account_me(authorization: str | None = Header(default=None)) -> JSONResponse:
        return JSONResponse(_account(session_for(authorization), self_account=True))

    @app.get("/account/bulk")
    async def rr_service_account_bulk(authorization: str | None = Header(default=None)) -> JSONResponse:
        return JSONResponse([_account(session_for(authorization), self_account=False)])

    @app.get("/account/{account_id}")
    async def rr_service_account_id(account_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        payload = _account(session, self_account=False)
        if account_id != session.account_id:
            payload.update({"accountId": account_id, "username": f"Player{account_id}", "displayName": f"Player {account_id}"})
        return JSONResponse(payload)

    @app.get("/parentalcontrol/me")
    async def rr_service_parental(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({"IsJunior": False, "JuniorState": 0})

    # ---------------------------------------------------------------- match.rec.net
    @app.get("/player")
    async def rr_service_player(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        payload = _player(session)
        payload["roomInstance"] = current_presence(session)
        return JSONResponse(payload)

    @app.post("/player/login")
    async def rr_service_player_login(request: Request, authorization: str | None = Header(default=None)) -> Response:
        session = session_for(authorization)
        await _remember_login_lock(request, session)
        if "lastRoomId" not in session.state:
            _orientation_instance(session)
        return Response(status_code=200)

    @app.post("/player/exclusivelogin")
    async def rr_service_player_exclusive_login(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        await _remember_login_lock(request, session)
        return JSONResponse({"errorCode": 0})

    @app.post("/player/logout")
    async def rr_service_player_logout(request: Request, authorization: str | None = Header(default=None)) -> Response:
        session = session_for(authorization)
        await _remember_login_lock(request, session)
        current = session.state.get("currentRoomInstance")
        instance_id = None
        if isinstance(current, dict):
            try:
                instance_id = int(current.get("roomInstanceId"))
            except (TypeError, ValueError):
                pass
        if "lastRoomId" not in session.state or instance_id == ORIENTATION_INSTANCE_ID:
            _orientation_instance(session)
        else:
            session.state.pop("currentRoomInstance", None)
        return Response(status_code=200)

    @app.post("/player/heartbeat")
    async def rr_service_player_heartbeat(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        await _remember_login_lock(request, session)
        return JSONResponse(heartbeat_payload(session))

    @app.get("/player/avoidjuniors")
    async def rr_service_avoid_juniors_get(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse(bool(session.state.get("avoidJuniors", False)))

    async def set_avoid_juniors(request: Request, authorization: str | None) -> JSONResponse:
        session = session_for(authorization)
        value = False
        try:
            content_type = (request.headers.get("content-type") or "").lower()
            if "application/json" in content_type:
                body = await request.json()
                if isinstance(body, dict):
                    raw = body.get("avoidJuniors", body.get("AvoidJuniors", False))
                else:
                    raw = body
            else:
                raw_body = (await request.body()).decode("utf-8", errors="ignore")
                pairs = dict(item.split("=", 1) for item in raw_body.split("&") if "=" in item)
                raw = pairs.get("avoidJuniors", pairs.get("AvoidJuniors", False))
            value = str(raw).strip().lower() in {"true", "1", "yes"}
        except Exception:
            value = False
        session.state["avoidJuniors"] = value
        return JSONResponse(value)

    @app.put("/player/avoidjuniors")
    async def rr_service_avoid_juniors_put(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
        return await set_avoid_juniors(request, authorization)

    @app.post("/player/avoidjuniors")
    async def rr_service_avoid_juniors_post(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
        return await set_avoid_juniors(request, authorization)

    @app.get("/player/qos")
    async def rr_service_player_qos(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({"regions": [{"region": PHOTON_REGION, "ip": "127.0.0.1", "port": 5055}]})

    @app.get("/player/connection-info")
    async def rr_service_connection_info(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({"host": "127.0.0.1", "port": 5055, "region": PHOTON_REGION})

    async def region_pings_ack(authorization: str | None) -> Response:
        session_for(authorization)
        return Response(status_code=200)

    for path in ("/player/photonregionpings", "/player/gameserverregionpings"):
        for method in ("GET", "POST", "PUT"):
            async def ping_handler(authorization: str | None = Header(default=None)) -> Response:
                return await region_pings_ack(authorization)
            ping_handler.__name__ = f"rr_service_region_ping_{path.replace('/', '_')}_{method.lower()}"
            app.add_api_route(path, ping_handler, methods=[method])

    @app.put("/player/statusvisibility")
    async def rr_service_status_visibility(authorization: str | None = Header(default=None)) -> Response:
        session_for(authorization)
        return Response(status_code=200)

    @app.put("/player/vrmovementmode")
    async def rr_service_vr_movement_mode(authorization: str | None = Header(default=None)) -> Response:
        session_for(authorization)
        return Response(status_code=200)

    @app.post("/matchmake/dorm")
    async def rr_service_matchmake_dorm(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        room_id = int(session.state.get("dormRoomId") or session.account_id + 1_000_000_000)
        gateway.save(session, {"dormRoomId": room_id, "lastRoomId": room_id})
        instance = _room_instance(session, room_id, DORM_SCENE_LOCATION_ID, private=True)
        return JSONResponse({"errorCode": 0, "roomInstance": instance})

    @app.post("/matchmake/v2/room/{room_id}")
    async def rr_service_matchmake_room(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        gateway.save(session, {"lastRoomId": room_id})
        instance = _room_instance(session, room_id, f"FluxRoom_{room_id}", private=False)
        return JSONResponse({"errorCode": 0, "roomInstance": instance})

    @app.post("/matchmake/none")
    async def rr_service_matchmake_none(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({"errorCode": 0, "roomInstance": None})

    @app.get("/roominstance/{instance_id}")
    async def rr_service_room_instance(instance_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        if instance_id == ORIENTATION_INSTANCE_ID and "lastRoomId" not in session.state:
            return JSONResponse(_orientation_instance(session))
        current = current_presence(session)
        if int(current.get("roomInstanceId") or -999) == instance_id:
            return JSONResponse(current)
        copy = dict(current)
        copy["roomInstanceId"] = int(instance_id)
        copy["inviteCode"] = str(instance_id)
        return JSONResponse(copy)

    @app.post("/roominstance/{instance_id}/reportjoinresult")
    async def rr_service_report_join(instance_id: int, authorization: str | None = Header(default=None)) -> Response:
        session_for(authorization)
        return Response(status_code=200)

    # ---------------------------------------------------------------- rooms.rec.net
    @app.get("/dormroom/me")
    async def rr_service_dorm_me(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        room_id = int(session.state.get("dormRoomId") or session.account_id + 1_000_000_000)
        if int(session.state.get("dormRoomId") or -1) != room_id:
            gateway.save(session, {"dormRoomId": room_id})
        return JSONResponse(build_dorm_room(session))

    async def photon_access_payload(authorization: str | None) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse(
            {
                "RoomInstanceId": _current_room_instance_id(session),
                "PhotonAccessToken": "",
                "Permissions": _permissions(session),
            }
        )

    @app.get("/photon_access_token")
    async def rr_service_photon_access(authorization: str | None = Header(default=None)) -> JSONResponse:
        return await photon_access_payload(authorization)

    @app.get("/rooms/{room_id}")
    async def rr_service_room_id(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        if room_id == ORIENTATION_ROOM_ID:
            return JSONResponse(build_orientation_room())
        return JSONResponse(build_generic_room(session, room_id))

    @app.get("/rooms")
    async def rr_service_rooms(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse([build_dorm_room(session)])

    @app.get("/rooms/search")
    async def rr_service_rooms_search(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse([build_dorm_room(session)])

    @app.get("/rooms/bulk")
    async def rr_service_rooms_bulk(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse([build_dorm_room(session)])

    for path in (
        "/rooms/hot",
        "/rooms/autocomplete_search",
        "/rooms/ownedby/me",
        "/rooms/visitedby/me",
        "/featuredrooms/current",
    ):
        async def room_list_handler(authorization: str | None = Header(default=None)) -> JSONResponse:
            session_for(authorization)
            return JSONResponse([])
        room_list_handler.__name__ = "rr_service_rooms_list_" + path.replace("/", "_")
        app.add_api_route(path, room_list_handler, methods=["GET"])

    @app.get("/publishState/configs")
    async def rr_service_publish_state(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({})

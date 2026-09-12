from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Header, Request
from fastapi.responses import JSONResponse

from recroom_gateway import NativeSession, RecRoomGateway


def install_recroom_leaderboard_compat_routes(app: Any, gateway: RecRoomGateway) -> None:
    """Serve old leaderboard.rec.net DTOs without list/dictionary mismatches."""

    def session_for(authorization: str | None) -> NativeSession:
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        return gateway.from_token(token)

    async def json_body(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
            return body if isinstance(body, dict) else {}
        except Exception:
            return {}

    def field(body: dict[str, Any], name: str, default: int = 0) -> int:
        raw = body.get(name, body.get(name[:1].lower() + name[1:], default))
        try:
            return int(raw)
        except (TypeError, ValueError):
            return int(default)

    def stat_store(session: NativeSession) -> dict[str, int]:
        raw = session.state.get("leaderboardStats")
        if not isinstance(raw, dict):
            raw = {}
            session.state["leaderboardStats"] = raw
        return raw

    def stat_key(room_id: int, channel: int) -> str:
        return f"{int(room_id)}:{int(channel)}"

    def entry(session: NativeSession, room_id: int, channel: int) -> dict[str, Any]:
        value = int(stat_store(session).get(stat_key(room_id, channel), 0))
        return {"playerId": int(session.account_id), "score": value, "rank": 1 if value else 0}

    def full_payload(session: NativeSession, room_id: int, channel: int) -> dict[str, Any]:
        row = entry(session, room_id, channel)
        rows = [row] if row["score"] != 0 else []
        return {
            "GlobalOverall": rows,
            "GlobalPeriodic": rows,
            "FriendsOverall": [],
            "FriendsPeriodic": [],
            "NextResetUTC": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
        }

    async def set_stat(request: Request, authorization: str | None) -> JSONResponse:
        session = session_for(authorization)
        body = await json_body(request)
        room_id = field(body, "RoomId")
        channel = field(body, "StatChannel")
        value = field(body, "StatValue")
        stat_store(session)[stat_key(room_id, channel)] = value
        return JSONResponse({"success": True, "error": ""})

    async def check_and_set(request: Request, authorization: str | None) -> JSONResponse:
        session = session_for(authorization)
        body = await json_body(request)
        room_id = field(body, "RoomId")
        channel = field(body, "StatChannel")
        value = field(body, "StatValue")
        store = stat_store(session)
        key = stat_key(room_id, channel)
        current = store.get(key)
        expected_raw = body.get("CurrentStatValue", body.get("currentStatValue"))
        if expected_raw is not None:
            try:
                expected = int(expected_raw)
            except (TypeError, ValueError):
                expected = None
            if expected is not None and current is not None and int(current) != expected:
                return JSONResponse({
                    "success": False,
                    "error": "stat_value_mismatch",
                    "StatValue": int(current),
                    "CurrentStatValue": int(current),
                })
        store[key] = value
        return JSONResponse({"success": True, "error": "", "StatValue": value, "CurrentStatValue": value})

    async def get_player_rank(request: Request, authorization: str | None) -> JSONResponse:
        session = session_for(authorization)
        body = await json_body(request)
        player_id = field(body, "PlayerId", session.account_id) or session.account_id
        room_id = field(body, "RoomId")
        channel = field(body, "StatChannel")
        row = entry(session, room_id, channel)
        row["playerId"] = int(player_id)
        return JSONResponse(row)

    async def get_rows(request: Request, authorization: str | None) -> JSONResponse:
        session = session_for(authorization)
        body = await json_body(request)
        room_id = field(body, "RoomId")
        channel = field(body, "StatChannel")
        row = entry(session, room_id, channel)
        return JSONResponse({"rows": [row] if row["score"] != 0 else []})

    async def top(request: Request, authorization: str | None) -> JSONResponse:
        session = session_for(authorization)
        try:
            room_id = int(request.query_params.get("roomId", "0"))
        except ValueError:
            room_id = 0
        try:
            channel = int(request.query_params.get("channel", "0"))
        except ValueError:
            channel = 0
        return JSONResponse(full_payload(session, room_id, channel))

    # leaderboard.rec.net canonical paths and defensive api-host aliases.
    for prefix in ("/leaderboard", "/api/leaderboard"):
        async def set_handler(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
            return await set_stat(request, authorization)
        set_handler.__name__ = "rr_leaderboard_set_" + prefix.replace("/", "_")
        app.add_api_route(prefix + "/SetStat", set_handler, methods=["POST"])

        async def check_handler(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
            return await check_and_set(request, authorization)
        check_handler.__name__ = "rr_leaderboard_check_" + prefix.replace("/", "_")
        app.add_api_route(prefix + "/CheckAndSetStat", check_handler, methods=["POST"])

        async def rank_handler(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
            return await get_player_rank(request, authorization)
        rank_handler.__name__ = "rr_leaderboard_rank_" + prefix.replace("/", "_")
        app.add_api_route(prefix + "/GetPlayerRank", rank_handler, methods=["POST"])

        async def nearby_handler(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
            return await get_rows(request, authorization)
        nearby_handler.__name__ = "rr_leaderboard_nearby_" + prefix.replace("/", "_")
        app.add_api_route(prefix + "/GetNearbyScores", nearby_handler, methods=["POST"])

        async def ranks_handler(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
            return await get_rows(request, authorization)
        ranks_handler.__name__ = "rr_leaderboard_ranks_" + prefix.replace("/", "_")
        app.add_api_route(prefix + "/GetRanks", ranks_handler, methods=["POST"])

        async def top_handler(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
            return await top(request, authorization)
        top_handler.__name__ = "rr_leaderboard_top_" + prefix.replace("/", "_")
        app.add_api_route(prefix + "/Top", top_handler, methods=["GET"])

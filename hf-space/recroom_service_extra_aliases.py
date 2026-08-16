from __future__ import annotations

from typing import Any

from fastapi import Header, Request, Response
from fastapi.responses import JSONResponse

from recroom_gateway import NativeSession, RecRoomGateway


def install_recroom_service_extra_alias_routes(app: Any, gateway: RecRoomGateway) -> None:
    """Expose recovered stripped paths for non-core dedicated RecNet hosts.

    These are intentionally explicit. Unknown paths remain 404 so the real-client
    proxy/playtest trace can still tell us what the May-2022 build actually needs.
    """

    def session_for(authorization: str | None) -> NativeSession:
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        return gateway.from_token(token)

    # --------------------------------------------------------- playersettings.rec.net
    # Recovered implementations expose a list-style GET and accept either one
    # key/value setting or an array on PUT. Keep this in the native session so a
    # client read immediately reflects its write without inventing global state.
    for path in ("/playersettings", "/playersettings/playersettings"):
        async def get_player_settings(authorization: str | None = Header(default=None)) -> JSONResponse:
            session = session_for(authorization)
            raw = session.state.get("playerSettings")
            settings = raw if isinstance(raw, list) else []
            return JSONResponse(settings)
        get_player_settings.__name__ = "rr_service_player_settings_get_" + path.replace("/", "_")
        app.add_api_route(path, get_player_settings, methods=["GET"])

        async def put_player_settings(request: Request, authorization: str | None = Header(default=None)) -> Response:
            session = session_for(authorization)
            settings: list[dict[str, str]] = []
            try:
                content_type = (request.headers.get("content-type") or "").lower()
                if "application/json" in content_type:
                    body = await request.json()
                    if isinstance(body, list):
                        candidates = body
                    elif isinstance(body, dict):
                        candidates = [body]
                    else:
                        candidates = []
                    for item in candidates:
                        if not isinstance(item, dict):
                            continue
                        key = str(item.get("Key") or item.get("key") or "")
                        value = str(item.get("Value") or item.get("value") or "")
                        if key:
                            settings.append({"Key": key, "Value": value})
                else:
                    raw = (await request.body()).decode("utf-8", errors="ignore")
                    pairs = dict(item.split("=", 1) for item in raw.split("&") if "=" in item)
                    key = str(pairs.get("key") or pairs.get("Key") or "")
                    value = str(pairs.get("value") or pairs.get("Value") or "")
                    if key:
                        settings.append({"Key": key, "Value": value})
            except Exception:
                settings = []
            if settings:
                session.state["playerSettings"] = settings[:500]
            return Response(status_code=200)
        put_player_settings.__name__ = "rr_service_player_settings_put_" + path.replace("/", "_")
        app.add_api_route(path, put_player_settings, methods=["PUT"])

    # ------------------------------------------------------------------ econ.rec.net
    @app.get("/roomInventory/player")
    async def rr_service_econ_player_inventory(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    @app.get("/roomInventory/room/{room_id}")
    async def rr_service_econ_room_inventory(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    @app.get("/roomInventory/room/{room_id}/player")
    async def rr_service_econ_room_player_inventory(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    @app.get("/roomInventoryItemTags/room/{room_id}")
    async def rr_service_econ_item_tags(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    @app.get("/roomOffer/room/{room_id}")
    async def rr_service_econ_room_offers(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    @app.get("/roomOffer/room/{room_id}/purchaseCounts")
    async def rr_service_econ_offer_purchase_counts(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({})

    @app.get("/roomGiftDropShops/room/{room_id}")
    async def rr_service_econ_gift_drop_shops(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    @app.get("/roomEconConfig/{room_id}")
    async def rr_service_econ_config(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({})

    # --------------------------------------------------------------- commerce.rec.net
    for path in (
        "/api/catalog/v1/all",
        "/purchasecampaign/allcurrent/v2",
        "/api/purchasecampaign/allcurrent/v2",
    ):
        async def commerce_list(authorization: str | None = Header(default=None)) -> JSONResponse:
            session_for(authorization)
            return JSONResponse([])
        commerce_list.__name__ = "rr_service_commerce_" + path.replace("/", "_")
        app.add_api_route(path, commerce_list, methods=["GET"])

    # ---------------------------------------------------------------- notify.rec.net
    @app.get("/hub/v1")
    async def rr_service_notifications_hub(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({})

    @app.post("/hub/v1/negotiate")
    async def rr_service_notifications_negotiate(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        try:
            await request.body()
        except Exception:
            pass
        return JSONResponse({})

    @app.get("/crm/me/config/v3")
    async def rr_service_notifications_crm(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({})

    # ------------------------------------------------------------------ clubs.rec.net
    @app.get("/club/home/me")
    async def rr_service_club_home(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({})

    for path in (
        "/club/mine/member",
        "/subscription/mine/member",
        "/announcements/v2/mine/unread",
        "/announcements/v2/subscription/mine/unread",
    ):
        async def club_list(authorization: str | None = Header(default=None)) -> JSONResponse:
            session_for(authorization)
            return JSONResponse([])
        club_list.__name__ = "rr_service_club_" + path.replace("/", "_")
        app.add_api_route(path, club_list, methods=["GET"])

from __future__ import annotations

from typing import Any

from fastapi import Header, Request
from fastapi.responses import JSONResponse

from recroom_gateway import NativeSession, RecRoomGateway


def install_recroom_extra_routes(app: Any, gateway: RecRoomGateway) -> None:
    """Add known non-blocking RecNet calls used after login/room startup.

    These DTO categories follow recovered endpoint maps from modern and
    School's-Out-era clients. Unknown calls remain 404 for diagnostics.
    """

    def session_for(authorization: str | None) -> NativeSession:
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        return gateway.from_token(token)

    def add_get_list(path: str, name: str) -> None:
        async def handler(authorization: str | None = Header(default=None)) -> JSONResponse:
            session_for(authorization)
            return JSONResponse([])
        handler.__name__ = name
        app.add_api_route(path, handler, methods=["GET"], name=name)

    def add_get_object(path: str, name: str, payload: dict[str, Any] | None = None) -> None:
        async def handler(authorization: str | None = Header(default=None)) -> JSONResponse:
            session_for(authorization)
            return JSONResponse(dict(payload or {}))
        handler.__name__ = name
        app.add_api_route(path, handler, methods=["GET"], name=name)

    def add_post_object(path: str, name: str, payload: dict[str, Any] | None = None) -> None:
        async def handler(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
            session_for(authorization)
            try:
                await request.body()
            except Exception:
                pass
            return JSONResponse(dict(payload or {}))
        handler.__name__ = name
        app.add_api_route(path, handler, methods=["POST"], name=name)

    list_gets = {
        "/api/gameconfigs/v1/all": "rr2022_gameconfigs_all",
        "/api/rooms/v1/filters": "rr2022_room_filters",
        "/api/relationships/v2/get": "rr2022_relationships_get",
        "/api/messages/v2/get": "rr2022_messages_get",
        "/api/messages/v1/friendOnlineStatus": "rr2022_friend_online_status_extra",
        "/api/externalfriendinvite/v1/getplatformreferrers": "rr2022_platform_referrers",
        "/api/players/v2/progression/bulk": "rr2022_progression_bulk_extra",
        "/api/progressionEvents/active": "rr2022_progression_events_extra",
        "/api/progressionEvents/event/id": "rr2022_progression_event",
        "/api/purchasableXpBoosts/activations": "rr2022_xp_boost_activations",
        "/api/playerReputation/v2/bulk": "rr2022_reputation_bulk_extra",
        "/api/objectives/v1/current": "rr2022_objectives_current",
        "/api/gamerewards/v1/pending": "rr2022_rewards_pending",
        "/outfits/me/saved": "rr2022_saved_outfits",
        "/Commerce/api/catalog/v1/all": "rr2022_catalog_all",
        "/Commerce/purchasecampaign/allcurrent/v2": "rr2022_purchase_campaigns_schoolout",
        "/Commerce/api/purchasecampaign/allcurrent/v2": "rr2022_purchase_campaigns_api_alias",
        "/api/roomEarningsDistributions": "rr2022_room_earnings",
        "/api/consumables/v1/all": "rr2022_consumables_all",
        "/api/consumables/v1/query/bulk": "rr2022_consumables_bulk",
        "/api/roomkeys/v1": "rr2022_roomkeys_root",
        "/api/roomkeys/v1/": "rr2022_roomkeys_root_slash",
        "/api/roomkeys/v1/mine": "rr2022_roomkeys_mine",
        "/api/inventions/v2/mine": "rr2022_inventions_mine",
        "/api/inventions/v1/room": "rr2022_inventions_room",
        "/api/images/v2/named": "rr2022_images_named",
        "/api/images/v5/cheered/bulk": "rr2022_images_cheered",
        "/api/PlayerReporting/v1/voteToKickReasons": "rr2022_votekick_reasons",
        "/clubs/club/mine/member": "rr2022_club_memberships",
        "/clubs/subscription/mine/member": "rr2022_club_subscription_memberships",
        "/api/playerevents/v1/all": "rr2022_player_events_all",
        "/api/referee/files": "rr2022_referee_files",
    }
    for path, name in list_gets.items():
        add_get_list(path, name)

    object_gets: dict[str, tuple[str, dict[str, Any]]] = {
        "/AuthorizeDevice/v1": ("rr2022_authorize_device", {}),
        "/api/checklist/v1/current": ("rr2022_checklist_current", {}),
        "/api/checklist/v1/complete": ("rr2022_checklist_complete_get", {}),
        "/api/avatar/v2/gifts/generate": ("rr2022_avatar_gifts_v2", {}),
        "/api/avatar/v3/gifts/generate": ("rr2022_avatar_gifts_v3", {}),
        "/api/subscriptionseasons/v1/seasons/current": ("rr2022_current_season", {}),
        "/api/PlayerReporting/v1/moderationBlockDetails": ("rr2022_moderation_blocks", {}),
        "/api/communityboard/v2/current": ("rr2022_community_board", {}),
        "/crm/me/config/v3": ("rr2022_crm_config", {}),
        "/Notifications/crm/me/config/v3": ("rr2022_notifications_crm_config", {}),
    }
    for path, (name, payload) in object_gets.items():
        add_get_object(path, name, payload)

    posts = {
        "/api/checklist/v1/complete": ("rr2022_checklist_complete_post", {}),
        "/api/gamerewards/v1/request": ("rr2022_rewards_request", {}),
        "/api/roomCurrencies/v2/purchase": ("rr2022_currency_purchase", {}),
        "/api/consumables/v1/consume": ("rr2022_consumable_use", {}),
        "/api/roomkeys/v1/award": ("rr2022_roomkey_award", {}),
        "/api/roomkeys/v1/create": ("rr2022_roomkey_create", {}),
        "/api/sanitize/v1": ("rr2022_sanitize_post", {}),
    }
    for path, (name, payload) in posts.items():
        add_post_object(path, name, payload)

    room_lists = {
        "/Room_server/featuredrooms/current": "rr2022_featured_rooms",
        "/Room_server/rooms/hot": "rr2022_hot_rooms",
        "/Room_server/rooms/ownedby/me": "rr2022_owned_rooms",
        "/Room_server/rooms/visitedby/me": "rr2022_visited_rooms",
    }
    for path, name in room_lists.items():
        add_get_list(path, name)

    # Static room paths above must precede dynamic /rooms/{room_id} routes.
    router_routes = getattr(getattr(app, "router", None), "routes", None)
    if isinstance(router_routes, list):
        dynamic_room_routes = [
            route
            for route in router_routes
            if str(getattr(route, "path", "")).startswith("/Room_server/rooms/{room_id}")
        ]
        if dynamic_room_routes:
            router_routes[:] = [route for route in router_routes if route not in dynamic_room_routes] + dynamic_room_routes

    @app.get("/econ/roomInventory/room/{room_id}")
    async def rr2022_room_inventory(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    @app.get("/econ/roomInventory/room/{room_id}/player")
    async def rr2022_room_player_inventory(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    @app.get("/econ/roomInventory/player")
    async def rr2022_player_inventory_alias(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    @app.get("/econ/roomInventoryItemTags/room/{room_id}")
    async def rr2022_room_item_tags(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    @app.get("/econ/roomOffer/room/{room_id}")
    async def rr2022_room_offers(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    @app.get("/econ/roomOffer/room/{room_id}/purchaseCounts")
    async def rr2022_room_offer_purchase_counts(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({})

    @app.get("/econ/roomGiftDropShops/room/{room_id}")
    async def rr2022_room_gift_drop_shops(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    @app.get("/econ/roomEconConfig/{room_id}")
    async def rr2022_room_econ_config(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({})

    @app.get("/api/storefronts/v4/balance/{currency}")
    async def rr2022_storefront_balance(currency: str, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({"currency": currency, "amount": 0})

    @app.get("/api/storefronts/v3/giftdropstore/{store_id}")
    async def rr2022_gift_drop_store(store_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({})

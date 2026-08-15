from __future__ import annotations

from typing import Any, Callable

from fastapi import Header, Request
from fastapi.responses import JSONResponse

from recroom_gateway import NativeSession, RecRoomGateway


def install_recroom_extra_routes(app: Any, gateway: RecRoomGateway) -> None:
    """Add known non-blocking RecNet calls used after login/room startup.

    Keep this separate from recroom_compat.py so startup DTO fixes stay small and
    easy to reason about. Unknown calls still remain 404 for diagnostics.
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

    # General config/browse data. These are optional collections for the client;
    # an empty typed collection is safer than a 404 during boot/profile hydration.
    list_gets = {
        "/api/gameconfigs/v1/all": "rr2022_gameconfigs_all",
        "/api/rooms/v1/filters": "rr2022_room_filters",
        "/api/relationships/v2/get": "rr2022_relationships_get",
        "/api/messages/v2/get": "rr2022_messages_get",
        "/api/progressionEvents/event/id": "rr2022_progression_event",
        "/api/objectives/v1/definitions": "rr2022_objective_definitions",
        "/api/objectives/v1/progress": "rr2022_objective_progress",
        "/api/checklist/v1/current": "rr2022_checklist_current",
        "/api/gamerewards/v1/pending": "rr2022_rewards_pending",
        "/api/avatar/v2/gifts/generate": "rr2022_avatar_gifts_v2",
        "/api/avatar/v3/gifts/generate": "rr2022_avatar_gifts_v3",
        "/outfits/me/saved": "rr2022_saved_outfits",
        "/Commerce/api/catalog/v1/all": "rr2022_catalog_all",
        "/api/roomEarningsDistributions": "rr2022_room_earnings",
        "/api/consumables/v1/all": "rr2022_consumables_all",
        "/api/consumables/v1/query/bulk": "rr2022_consumables_bulk",
        "/api/roomkeys/v1/mine": "rr2022_roomkeys_mine",
        "/api/inventions/v2/mine": "rr2022_inventions_mine",
        "/api/images/v2/named": "rr2022_images_named",
        "/api/images/v5/cheered/bulk": "rr2022_images_cheered",
        "/api/PlayerReporting/v1/voteToKickReasons": "rr2022_votekick_reasons",
        "/api/PlayerReporting/v1/moderationBlockDetails": "rr2022_moderation_blocks",
        "/clubs/club/mine/member": "rr2022_club_memberships",
        "/api/communityboard/v2/current": "rr2022_community_board",
        "/api/referee/files": "rr2022_referee_files",
    }
    for path, name in list_gets.items():
        add_get_list(path, name)

    object_gets: dict[str, tuple[str, dict[str, Any]]] = {
        "/api/storefronts/v4/balance/1": ("rr2022_token_balance", {"Balance": 0, "Currency": 1}),
        "/api/subscriptionseasons/v1/seasons/current": ("rr2022_current_season", {}),
    }
    for path, (name, payload) in object_gets.items():
        add_get_object(path, name, payload)

    posts = {
        "/api/checklist/v1/complete": ("rr2022_checklist_complete", {}),
        "/api/gamerewards/v1/request": ("rr2022_rewards_request", {}),
        "/api/roomCurrencies/v2/purchase": ("rr2022_currency_purchase", {"success": False}),
        "/api/consumables/v1/consume": ("rr2022_consumable_use", {}),
        "/api/roomkeys/v1/award": ("rr2022_roomkey_award", {}),
        "/api/roomkeys/v1/create": ("rr2022_roomkey_create", {}),
    }
    for path, (name, payload) in posts.items():
        add_post_object(path, name, payload)

    # Parameterized economy/invention endpoints. Return empty collections or
    # neutral config objects while preserving path variables for diagnostics.
    @app.get("/econ/roomInventory/room/{room_id}")
    async def rr2022_room_inventory(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    @app.get("/econ/roomInventory/player")
    async def rr2022_player_inventory(authorization: str | None = Header(default=None)) -> JSONResponse:
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

    @app.get("/econ/purchaseCounts")
    async def rr2022_purchase_counts(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    @app.get("/econ/roomEconConfig/{room_id}")
    async def rr2022_room_econ_config(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({"RoomId": room_id})

    @app.get("/api/inventions/v1/room")
    async def rr2022_inventions_room(id: int | None = None, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    @app.get("/api/storefronts/v3/giftdropstore/{store_id}")
    async def rr2022_gift_drop_store(store_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    @app.get("/api/roomkeys/v1/")
    async def rr2022_roomkeys_root(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

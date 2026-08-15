from __future__ import annotations

import base64
import json
import secrets
import time
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import Header, Request
from fastapi.responses import JSONResponse

from recroom_gateway import (
    PHOTON_APP_ID,
    PHOTON_APP_VERSION,
    PHOTON_REGION,
    TARGET_BUILD_DATE,
    TARGET_BUILD_ID,
    NativeSession,
    RecRoomGateway,
)


LOCAL_RECNET_BASE = "http://127.0.0.1:81"
TOKEN_ISSUER = f"{LOCAL_RECNET_BASE}/Auth"
TOKEN_KID = "flux-recroom-local-2022"
_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _int_b64(value: int) -> str:
    length = max(1, (value.bit_length() + 7) // 8)
    return _b64url(value.to_bytes(length, "big"))


def _issue_access_token(session: NativeSession, lifetime_seconds: int = 3600) -> str:
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT", "kid": TOKEN_KID}
    payload = {
        "iss": TOKEN_ISSUER,
        "aud": "rec.net",
        "sub": str(session.account_id),
        "accountId": session.account_id,
        "username": session.username,
        "iat": now,
        "exp": now + lifetime_seconds,
        "scope": ["rn.api.write", "rn.match.write", "rn.chat.write"],
    }
    encoded_header = _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_payload = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = _RSA_KEY.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return signing_input.decode("ascii") + "." + _b64url(signature)


def _jwks() -> dict[str, Any]:
    public = _RSA_KEY.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": TOKEN_KID,
                "n": _int_b64(public.n),
                "e": _int_b64(public.e),
            }
        ]
    }


def _discovery() -> dict[str, Any]:
    return {
        "issuer": TOKEN_ISSUER,
        "authorization_endpoint": f"{TOKEN_ISSUER}/connect/authorize",
        "token_endpoint": f"{TOKEN_ISSUER}/connect/token",
        "jwks_uri": f"{TOKEN_ISSUER}/.well-known/openid-configuration/jwks",
        "userinfo_endpoint": f"{TOKEN_ISSUER}/connect/userinfo",
        "response_types_supported": ["code", "token", "id_token"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "profile", "rn.api.write", "rn.match.write", "rn.chat.write"],
        "grant_types_supported": ["authorization_code", "client_credentials", "refresh_token", "password"],
    }


def _account(session: NativeSession, *, self_account: bool = True) -> dict[str, Any]:
    created_at = str(session.state.get("createdAt") or "2017-01-01T00:00:00Z")
    payload: dict[str, Any] = {
        "accountId": session.account_id,
        "username": session.username,
        "displayName": session.display_name,
        "profileImage": "",
        "bannerImage": "",
        "isJunior": False,
        # Keep the older alias too. Extra JSON fields are ignored by RecNet DTOs
        # but this helps builds that still deserialize the pre-IsJunior name.
        "junior": False,
        "isMetaPlatformBlocked": False,
        "personalPronouns": 0,
        "identityFlags": 0,
        "platforms": 1,
        "platform": "Steam",
        "createdAt": created_at,
        "level": int(session.state.get("level") or 1),
    }
    if self_account:
        payload.update(
            {
                "birthday": "2000-01-01T00:00:00Z",
                "juniorState": 0,
                "availableUsernameChanges": 1,
            }
        )
    return payload


def _player(session: NativeSession) -> dict[str, Any]:
    return {
        "success": True,
        "playerId": session.account_id,
        "accountId": session.account_id,
        "username": session.username,
        "isOnline": True,
        "statusVisibility": 1,
        "platform": "Steam",
    }


def _room(session: NativeSession, room_id: int | None = None) -> dict[str, Any]:
    resolved = int(room_id or session.state.get("dormRoomId") or session.account_id + 1_000_000_000)
    is_dorm = resolved == int(session.state.get("dormRoomId") or session.account_id + 1_000_000_000)
    return {
        "RoomId": resolved,
        "Name": f"DormRoom_{session.account_id}" if is_dorm else f"FluxRoom_{resolved}",
        "Description": "Flux private dorm room" if is_dorm else "Flux compatibility room",
        "CreatorAccountId": session.account_id,
        "IsDormRoom": is_dorm,
        "MaxPlayerCalculationMode": 0,
        "MaxPlayers": 8 if not is_dorm else 1,
        "Accessibility": 1,
        "CloningPermission": 0,
        "SupportsScreens": True,
        "SupportsWalkVR": True,
        "SupportsTeleportVR": True,
    }


def _remove_exact_route(app: Any, path: str, methods: set[str] | None = None) -> None:
    router = getattr(app, "router", None)
    routes = getattr(router, "routes", None)
    if not isinstance(routes, list):
        return
    kept = []
    for route in routes:
        if getattr(route, "path", None) != path:
            kept.append(route)
            continue
        route_methods = set(getattr(route, "methods", set()) or set())
        if methods is not None and not (route_methods & methods):
            kept.append(route)
    routes[:] = kept


def install_recroom_compat_routes(app: Any, gateway: RecRoomGateway) -> None:
    """Install strict May-2022 compatibility responses on top of the Flux gateway.

    The core gateway owns identity/session persistence. This layer only fixes DTO
    shapes and fills well-known RecNet startup routes so the old client does not
    fail boot because of a 404 or an incompatible JSON schema.
    """

    def session_for(authorization: str | None) -> NativeSession:
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        return gateway.from_token(token)

    # Replace gateway routes whose response shapes were convenient for Flux but
    # not compatible with the RecNet DTOs used by the old client.
    for path, methods in [
        ("/api/config/v2", {"GET"}),
        ("/Accounts/account/me", {"GET"}),
        ("/Accounts/account/bulk", {"GET"}),
        ("/Matchmaking/player", {"GET"}),
        ("/Matchmaking/player/login", {"POST"}),
        ("/Matchmaking/player/logout", {"POST"}),
        ("/Matchmaking/player/heartbeat", {"POST"}),
        ("/Room_server/dormroom/me", {"GET"}),
        ("/Room_server/photon_access_token", {"GET"}),
        ("/Matchmaking/matchmake/dorm", {"POST"}),
        ("/api/sanitize/v1/isPure", {"GET"}),
    ]:
        _remove_exact_route(app, path, methods)

    @app.get("/api/config/v2")
    async def rr2022_config_v2() -> JSONResponse:
        entries = {
            "Screens.ForceVerification": "0",
            "Screens.ForceWaitlist": "false",
            "Maintenance.Enabled": "false",
            "Photon.Enabled": "true",
            "Photon.AppId": PHOTON_APP_ID,
            "Photon.AppVersion": PHOTON_APP_VERSION,
            "Photon.Region": PHOTON_REGION,
            "Environment": "Flux",
            "BuildId": TARGET_BUILD_ID,
            "BuildDate": TARGET_BUILD_DATE,
            "AllowUnsupportedVersion": "true",
        }
        return JSONResponse(
            [
                {
                    "Key": key,
                    "Value": str(value),
                    "ActiveExperiments": None,
                    "StartTime": None,
                    "EndTime": None,
                }
                for key, value in entries.items()
            ]
        )

    version_paths = [
        "/api/versioncheck/islandedversions",
        "/api/versioncheck/v1",
        "/api/versioncheck/v2",
        "/api/versioncheck/v3",
        "/api/versioncheck/v4",
        "/api/versioncheck",
    ]

    async def version_check() -> JSONResponse:
        return JSONResponse(
            {
                "VersionStatus": 0,
                "UpdateNotificationStage": 0,
                "IsCrossPlayDisabled": False,
                "RequiresUpdate": False,
            }
        )

    for index, path in enumerate(version_paths):
        version_check.__name__ = f"rr2022_version_check_{index}"
        app.add_api_route(path, version_check, methods=["GET"])

    @app.get("/.well-known/openid-configuration")
    async def rr2022_oidc_root() -> JSONResponse:
        return JSONResponse(_discovery())

    @app.get("/Auth/.well-known/openid-configuration")
    async def rr2022_oidc_auth() -> JSONResponse:
        return JSONResponse(_discovery())

    @app.get("/.well-known/openid-configuration/jwks")
    async def rr2022_jwks_root() -> JSONResponse:
        return JSONResponse(_jwks())

    @app.get("/Auth/.well-known/openid-configuration/jwks")
    async def rr2022_jwks_auth() -> JSONResponse:
        return JSONResponse(_jwks())

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

    @app.post("/Auth/connect/token")
    async def rr2022_auth_token(authorization: str | None = Header(default=None)) -> JSONResponse:
        return await token_response(authorization)

    @app.post("/Auth/cachedlogin/forplatformids")
    async def rr2022_cached_login(authorization: str | None = Header(default=None)) -> JSONResponse:
        return await token_response(authorization)

    @app.get("/Auth/role/developer")
    async def rr2022_developer_role(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse(True)

    @app.get("/Accounts/account/me")
    async def rr2022_account_me(authorization: str | None = Header(default=None)) -> JSONResponse:
        return JSONResponse(_account(session_for(authorization), self_account=True))

    @app.get("/Accounts/account/bulk")
    async def rr2022_account_bulk(authorization: str | None = Header(default=None)) -> JSONResponse:
        return JSONResponse([_account(session_for(authorization), self_account=False)])

    @app.get("/Accounts/account/{account_id}")
    async def rr2022_account_id(account_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        payload = _account(session, self_account=False)
        if account_id != session.account_id:
            payload.update({"accountId": account_id, "username": f"Player{account_id}", "displayName": f"Player {account_id}"})
        return JSONResponse(payload)

    @app.get("/Accounts/parentalcontrol/me")
    async def rr2022_parental_control(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({"IsJunior": False, "JuniorState": 0})

    @app.get("/Matchmaking/player")
    async def rr2022_player(authorization: str | None = Header(default=None)) -> JSONResponse:
        return JSONResponse(_player(session_for(authorization)))

    @app.post("/Matchmaking/player/login")
    async def rr2022_player_login(authorization: str | None = Header(default=None)) -> JSONResponse:
        return JSONResponse(_player(session_for(authorization)))

    @app.post("/Matchmaking/player/logout")
    async def rr2022_player_logout(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({})

    @app.post("/Matchmaking/player/heartbeat")
    async def rr2022_player_heartbeat(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse({"playerId": session.account_id, "serverTime": int(time.time() * 1000)})

    @app.get("/Matchmaking/player/qos")
    async def rr2022_player_qos(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({"regions": [{"region": PHOTON_REGION, "ip": "127.0.0.1", "port": 5055}]})

    @app.get("/Matchmaking/player/connection-info")
    async def rr2022_connection_info(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({"host": "127.0.0.1", "port": 5055, "region": PHOTON_REGION})

    @app.post("/Matchmaking/player/exclusivelogin")
    async def rr2022_exclusive_login(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({})

    @app.put("/Matchmaking/player/gameserverregionpings")
    async def rr2022_region_pings(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({})

    @app.put("/Matchmaking/player/statusvisibility")
    async def rr2022_status_visibility(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({})

    @app.get("/Room_server/dormroom/me")
    async def rr2022_dorm_me(authorization: str | None = Header(default=None)) -> JSONResponse:
        return JSONResponse(_room(session_for(authorization)))

    @app.get("/Room_server/photon_access_token")
    async def rr2022_photon_access_token(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse(
            {
                "Token": _issue_access_token(session),
                "AppId": PHOTON_APP_ID,
                "AppVersion": PHOTON_APP_VERSION,
                "Region": PHOTON_REGION,
                "UserId": str(session.account_id),
                "ExpirationDate": "2099-01-01T00:00:00Z",
            }
        )

    @app.post("/Matchmaking/matchmake/dorm")
    async def rr2022_matchmake_dorm(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        room = _room(session)
        return JSONResponse(
            {
                "success": True,
                "roomId": room["RoomId"],
                "instanceId": f"flux-dorm-{session.account_id}",
                "roomInstanceId": f"flux-dorm-{session.account_id}",
                "host": "127.0.0.1",
                "port": 5055,
                "photon": {"configured": bool(PHOTON_APP_ID), "region": PHOTON_REGION},
            }
        )

    @app.post("/Matchmaking/matchmake/v2/room/{room_id}")
    async def rr2022_matchmake_room(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse(
            {
                "success": True,
                "roomId": room_id,
                "instanceId": f"flux-room-{room_id}-{session.account_id}",
                "roomInstanceId": f"flux-room-{room_id}-{session.account_id}",
                "host": "127.0.0.1",
                "port": 5055,
                "photon": {"configured": bool(PHOTON_APP_ID), "region": PHOTON_REGION},
            }
        )

    @app.post("/Matchmaking/matchmake/none")
    async def rr2022_matchmake_none(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({})

    @app.post("/Matchmaking/roominstance/{instance_id}/reportjoinresult")
    async def rr2022_report_join(instance_id: str, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({"success": True, "instanceId": instance_id})

    async def rooms_requiring(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    app.add_api_route("/Matchmaking/rooms/requiring/developer", rooms_requiring, methods=["GET"], name="rr2022_rooms_developer")
    app.add_api_route("/Matchmaking/rooms/requiring/rrplus", rooms_requiring, methods=["GET"], name="rr2022_rooms_rrplus")

    @app.get("/Room_server/rooms")
    async def rr2022_rooms(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse([_room(session)])

    @app.get("/Room_server/rooms/search")
    async def rr2022_rooms_search(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse([_room(session)])

    @app.get("/Room_server/rooms/autocomplete_search")
    async def rr2022_rooms_autocomplete(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse([])

    @app.get("/Room_server/rooms/bulk")
    async def rr2022_rooms_bulk(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse([_room(session)])

    @app.get("/Room_server/rooms/{room_id}")
    async def rr2022_room_id(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        return JSONResponse(_room(session_for(authorization), room_id))

    @app.get("/Room_server/rooms/{room_id}/experience")
    async def rr2022_room_experience(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({"RoomId": room_id})

    @app.get("/Room_server/rooms/{room_id}/experience/player")
    async def rr2022_room_experience_player(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({"RoomId": room_id})

    @app.get("/Room_server/rooms/{room_id}/interactionby/me")
    async def rr2022_room_interaction(room_id: int, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({"RoomId": room_id})

    @app.get("/Room_server/rooms/{room_id}/subrooms/{subroom}/saves/{save_id}")
    async def rr2022_subroom_save(room_id: int, subroom: str, save_id: str, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({"RoomId": room_id, "SubRoom": subroom, "SaveId": save_id})

    @app.get("/Room_server/publishState/configs")
    async def rr2022_publish_configs(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({})

    @app.get("/api/sanitize/v1/isPure")
    async def rr2022_sanitize_pure(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse(True)

    # Known non-blocking startup/feed endpoints. Lists return lists, resource /
    # capability/config endpoints return objects, and mutations acknowledge with
    # an empty object. No catch-all is installed: unknown RecNet calls remain 404
    # so logs still reveal missing compatibility work instead of hiding it.
    get_lists = [
        "/api/messages/v1/friendOnlineStatus",
        "/api/players/v2/progression/bulk",
        "/api/progressionEvents/active",
        "/api/playerReputation/v2/bulk",
        "/api/customAvatarItems/v1/bulk",
        "/api/avatar/v1/defaultunlocked",
        "/api/avatar/v4/items",
        "/clubs/announcements/v2/mine/unread",
        "/clubs/announcements/v2/subscription/mine/unread",
        "/api/keepsakes/categories",
        "/api/playerevents/v1/tagfilters",
        "/Commerce/api/purchasecampaign/allcurrent/v2",
    ]
    for index, path in enumerate(get_lists):
        async def list_handler(authorization: str | None = Header(default=None)) -> JSONResponse:
            session_for(authorization)
            return JSONResponse([])
        list_handler.__name__ = f"rr2022_list_{index}"
        app.add_api_route(path, list_handler, methods=["GET"])

    get_objects = [
        "/api/players/v1/playerPhotoTaggingSetting",
        "/api/keepsakes/globalconfig",
        "/statsigUserProperties",
        "/chat/thread",
        "/chat/thread/party",
        "/chat/thread/chatPrivacySetting",
        "/Notifications/hub/v1",
        "/Notifications/crm/me/config/v3",
        "/clubs/club/home/me",
    ]
    for index, path in enumerate(get_objects):
        async def object_handler(authorization: str | None = Header(default=None)) -> JSONResponse:
            session_for(authorization)
            return JSONResponse({})
        object_handler.__name__ = f"rr2022_object_{index}"
        app.add_api_route(path, object_handler, methods=["GET"])

    true_paths = [
        "/api/customAvatarItems/v1/isRenderingEnabled",
        "/api/customAvatarItems/v1/isCreationEnabled",
        "/api/customAvatarItems/v1/isCreationAllowedForAccount",
    ]
    for index, path in enumerate(true_paths):
        async def true_handler(authorization: str | None = Header(default=None)) -> JSONResponse:
            session_for(authorization)
            return JSONResponse(True)
        true_handler.__name__ = f"rr2022_true_{index}"
        app.add_api_route(path, true_handler, methods=["GET"])

    post_ok = [
        "/api/relationships/v1/ignore",
        "/api/relationships/v1/unignore",
        "/api/relationships/v1/mute",
        "/api/relationships/v1/unmute",
        "/api/rooms/v1/verifyRole",
        "/api/rooms/v3/report",
        "/api/quickPlay/v1/getandclear",
        "/api/images/v1/cheer",
        "/api/PlayerCheer/v1/create",
        "/api/PlayerReporting/v1/roomModKick",
    ]
    for index, path in enumerate(post_ok):
        async def post_handler(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
            session_for(authorization)
            try:
                await request.body()
            except Exception:
                pass
            return JSONResponse({})
        post_handler.__name__ = f"rr2022_post_{index}"
        app.add_api_route(path, post_handler, methods=["POST"])

    @app.post("/Notifications/hub/v1/negotiate")
    async def rr2022_notifications_negotiate(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        token = secrets.token_urlsafe(24)
        return JSONResponse(
            {
                "connectionId": token,
                "connectionToken": token,
                "negotiateVersion": 1,
                "availableTransports": [],
            }
        )

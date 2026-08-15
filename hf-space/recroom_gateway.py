from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse


FIREBASE_PROJECT_ID = "flux-544a6"
FIREBASE_WEB_API_KEY = os.environ.get("RECROOM_FIREBASE_WEB_API_KEY", "AIzaSyBV_ev8AVpKwgioPSSWiBYf8UU09bLbWOU")
ADMIN_EMAIL = "ripo.ripoteam@gmail.com"
TARGET_BUILD_DATE = "2022-05-19"
TARGET_BUILD_ID = "8751857"
TARGET_MANIFEST_ID = "6337851004861751095"
# Photon App IDs are client identifiers rather than authentication credentials;
# allow a deployment override while keeping the supplied Flux app identifier as
# the compatibility default.
PHOTON_APP_ID = os.environ.get("RECROOM_PHOTON_APP_ID", "ec2eaafc-0c8d-4e68-8f5f-fe4b1d3fb02f")
PHOTON_APP_VERSION = os.environ.get("RECROOM_PHOTON_APP_VERSION", "flux-recroom-2022")
PHOTON_REGION = os.environ.get("RECROOM_PHOTON_REGION", "eu")
SESSION_TTL_SECONDS = int(os.environ.get("RECROOM_NATIVE_SESSION_TTL_SECONDS", "3300"))


def _request_json(url: str, method: str = "GET", body: Any = None, bearer: str | None = None) -> dict[str, Any]:
    headers = {"accept": "application/json", "user-agent": "RipoTeam-RecRoom-Gateway/1.0"}
    data = None
    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["content-type"] = "application/json"
    if bearer:
        headers["authorization"] = f"Bearer {bearer}"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise HTTPException(status_code=exc.code, detail=detail or f"Upstream returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="Firebase service is unavailable.") from exc


def _stable_account_id(uid: str) -> int:
    digest = hashlib.sha256(uid.encode("utf-8")).digest()
    return 100_000 + (int.from_bytes(digest[:4], "big") % 899_000_000)


def _decode_jwt_exp(token: str) -> int:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        return int(data.get("exp") or 0)
    except Exception:
        return 0


def _firestore_decode(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    if "nullValue" in value:
        return None
    if "stringValue" in value:
        return str(value["stringValue"])
    if "booleanValue" in value:
        return bool(value["booleanValue"])
    if "integerValue" in value:
        try:
            return int(value["integerValue"])
        except Exception:
            return 0
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "timestampValue" in value:
        return str(value["timestampValue"])
    if "arrayValue" in value:
        return [_firestore_decode(item) for item in value.get("arrayValue", {}).get("values", [])]
    if "mapValue" in value:
        return {key: _firestore_decode(item) for key, item in value.get("mapValue", {}).get("fields", {}).items()}
    return None


def _firestore_encode(value: Any) -> dict[str, Any]:
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value[:20000]}
    if isinstance(value, list):
        return {"arrayValue": {"values": [_firestore_encode(item) for item in value[:500]]}}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {str(key)[:200]: _firestore_encode(item) for key, item in list(value.items())[:500]}}}
    return {"stringValue": str(value)[:20000]}


@dataclass
class NativeSession:
    token: str
    uid: str
    email: str | None
    username: str
    display_name: str
    account_id: int
    is_admin: bool
    firebase_token: str
    expires_at: float
    state: dict[str, Any]


class RecRoomGateway:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.sessions: dict[str, NativeSession] = {}
        self.audit_path = self.data_dir / "gateway-audit.jsonl"

    def _audit(self, event: str, **fields: Any) -> None:
        safe = {"ts": time.time(), "event": event, **fields}
        for key in ["token", "firebase_token", "authorization"]:
            safe.pop(key, None)
        try:
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(safe, separators=(",", ":")) + "\n")
        except OSError:
            pass

    def _lookup_firebase(self, id_token: str) -> dict[str, Any]:
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:lookup?key={urllib.parse.quote(FIREBASE_WEB_API_KEY)}"
        payload = _request_json(url, method="POST", body={"idToken": id_token})
        users = payload.get("users") if isinstance(payload.get("users"), list) else []
        if not users or not isinstance(users[0], dict):
            raise HTTPException(status_code=401, detail="Flux Firebase identity could not be verified.")
        return users[0]

    def _read_flux_profile(self, uid: str, id_token: str) -> dict[str, Any]:
        encoded_uid = urllib.parse.quote(uid, safe="")
        url = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents/users/{encoded_uid}"
        try:
            doc = _request_json(url, bearer=id_token)
        except HTTPException as exc:
            if exc.status_code == 404:
                return {}
            raise
        fields = doc.get("fields") if isinstance(doc.get("fields"), dict) else {}
        return {key: _firestore_decode(value) for key, value in fields.items()}

    def _write_recroom_state(self, session: NativeSession) -> None:
        encoded_uid = urllib.parse.quote(session.uid, safe="")
        mask = urllib.parse.quote("recroomState", safe="")
        url = (
            f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents/users/{encoded_uid}"
            f"?updateMask.fieldPaths={mask}"
        )
        _request_json(
            url,
            method="PATCH",
            bearer=session.firebase_token,
            body={"fields": {"recroomState": _firestore_encode(session.state)}},
        )

    def exchange(self, authorization: str | None) -> dict[str, Any]:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Flux Firebase token required.")
        id_token = authorization[7:].strip()
        if not id_token:
            raise HTTPException(status_code=401, detail="Flux Firebase token required.")
        user = self._lookup_firebase(id_token)
        uid = str(user.get("localId") or "")
        if not uid:
            raise HTTPException(status_code=401, detail="Flux Firebase identity is invalid.")
        email = str(user.get("email") or "") or None
        profile = self._read_flux_profile(uid, id_token)
        email_stem = (email or "player").split("@", 1)[0]
        default_username = "".join(ch for ch in email_stem if ch.isalnum() or ch == "_")[:20] or "player"
        username = str(profile.get("username") or default_username)[:20]
        display_name = str(profile.get("displayName") or user.get("displayName") or username)[:32]
        account_id = _stable_account_id(uid)
        raw_state = profile.get("recroomState")
        state = dict(raw_state) if isinstance(raw_state, dict) else {}
        session_token = secrets.token_urlsafe(32)
        jwt_exp = _decode_jwt_exp(id_token)
        hard_deadline = time.time() + SESSION_TTL_SECONDS
        expires_at = min(hard_deadline, float(jwt_exp - 30)) if jwt_exp > time.time() + 60 else hard_deadline
        native = NativeSession(
            token=session_token,
            uid=uid,
            email=email,
            username=username,
            display_name=display_name,
            account_id=account_id,
            is_admin=bool(email and email.lower() == ADMIN_EMAIL),
            firebase_token=id_token,
            expires_at=expires_at,
            state=state,
        )
        with self.lock:
            self.sessions[hashlib.sha256(session_token.encode()).hexdigest()] = native
        self._audit("session.exchange", uid=uid, account_id=account_id)
        return {
            "ok": True,
            "uid": uid,
            "sessionToken": session_token,
            "expiresAtMs": int(expires_at * 1000),
            "account": self.account(native),
        }

    def from_token(self, raw_token: str) -> NativeSession:
        key = hashlib.sha256(raw_token.encode()).hexdigest() if raw_token else ""
        with self.lock:
            session = self.sessions.get(key)
        if not session or session.expires_at <= time.time():
            raise HTTPException(status_code=401, detail="Flux Rec Room session is missing or expired.")
        return session

    def account(self, session: NativeSession) -> dict[str, Any]:
        return {
            "uid": session.uid,
            "accountId": session.account_id,
            "username": session.username,
            "displayName": session.display_name,
            "profileImage": "",
            "junior": False,
            "platforms": ["Steam"],
            "createdAt": session.state.get("createdAt"),
            "isAdmin": session.is_admin,
            "level": int(session.state.get("level") or 1),
            "xp": int(session.state.get("xp") or 0),
            "tokens": int(session.state.get("tokens") if session.state.get("tokens") is not None else 500),
        }

    def save(self, session: NativeSession, patch: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "displayName", "username", "level", "xp", "tokens", "outfit", "settings",
            "inventory", "dormRoomId", "avatar", "avatarCustomization", "lastRoomId",
        }
        safe = {key: value for key, value in patch.items() if key in allowed}
        with self.lock:
            session.state.update(safe)
        self._write_recroom_state(session)
        self._audit("state.save", uid=session.uid, keys=sorted(safe))
        return safe


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        return ""
    return authorization[7:].strip()


def install_recroom_gateway_routes(app: Any, gateway: RecRoomGateway) -> None:
    @app.get("/flux/health")
    async def flux_recroom_health() -> JSONResponse:
        return JSONResponse({
            "ok": True,
            "service": "Flux Rec Room compatibility gateway",
            "buildDate": TARGET_BUILD_DATE,
            "buildId": TARGET_BUILD_ID,
            "manifestId": TARGET_MANIFEST_ID,
            "photon": {"configured": bool(PHOTON_APP_ID), "region": PHOTON_REGION},
        })

    @app.get("/flux/config")
    async def flux_recroom_config() -> JSONResponse:
        return JSONResponse({
            "buildDate": TARGET_BUILD_DATE,
            "buildId": TARGET_BUILD_ID,
            "manifestId": TARGET_MANIFEST_ID,
            "photon": {"configured": bool(PHOTON_APP_ID), "appVersion": PHOTON_APP_VERSION, "region": PHOTON_REGION},
        })

    @app.post("/flux/auth/firebase")
    async def flux_recroom_auth(request: Request) -> JSONResponse:
        body = await request.json()
        token = str(body.get("idToken") or "") if isinstance(body, dict) else ""
        authorization = f"Bearer {token}" if token else request.headers.get("authorization")
        return JSONResponse(gateway.exchange(authorization))

    def session_for(authorization: str | None) -> NativeSession:
        return gateway.from_token(_bearer(authorization))

    @app.get("/flux/player/state")
    async def flux_player_state(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse({"ok": True, "account": gateway.account(session), "state": session.state})

    @app.patch("/flux/player/state")
    async def flux_player_state_patch(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        body = await request.json()
        patch = body if isinstance(body, dict) else {}
        return JSONResponse({"ok": True, "saved": gateway.save(session, patch)})

    @app.get("/api/config/v2")
    async def rr_config_v2() -> JSONResponse:
        return JSONResponse({"Environment": "Flux", "BuildId": TARGET_BUILD_ID, "BuildDate": TARGET_BUILD_DATE, "AllowUnsupportedVersion": True})

    @app.get("/Accounts/account/me")
    async def rr_account_me(authorization: str | None = Header(default=None)) -> JSONResponse:
        return JSONResponse(gateway.account(session_for(authorization)))

    @app.get("/Accounts/account/bulk")
    async def rr_account_bulk(authorization: str | None = Header(default=None)) -> JSONResponse:
        return JSONResponse([gateway.account(session_for(authorization))])

    @app.post("/Matchmaking/player/login")
    async def rr_player_login(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse({"success": True, "accountId": session.account_id, "playerId": session.account_id, "statusVisibility": 0, "platform": "Steam"})

    @app.post("/Matchmaking/player/logout")
    async def rr_player_logout(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({"success": True})

    @app.post("/Matchmaking/player/heartbeat")
    async def rr_player_heartbeat(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse({"success": True, "playerId": session.account_id, "serverTime": int(time.time() * 1000)})

    @app.get("/Matchmaking/player")
    async def rr_player_get(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse({"accountId": session.account_id, "playerId": session.account_id, "isOnline": True})

    @app.get("/Room_server/dormroom/me")
    async def rr_dorm_me(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        room_id = int(session.state.get("dormRoomId") or session.account_id + 1_000_000_000)
        return JSONResponse({
            "RoomId": room_id,
            "Name": f"DormRoom_{session.account_id}",
            "Description": "Flux private dorm room",
            "CreatorAccountId": session.account_id,
            "IsDormRoom": True,
            "MaxPlayerCalculationMode": 0,
            "MaxPlayers": 1,
            "Accessibility": 1,
            "SupportsScreens": True,
            "SupportsWalkVR": True,
            "SupportsTeleportVR": True,
        })

    @app.get("/Room_server/photon_access_token")
    async def rr_photon_token(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse({
            "AppId": PHOTON_APP_ID,
            "AppVersion": PHOTON_APP_VERSION,
            "Region": PHOTON_REGION,
            "UserId": str(session.account_id),
            "Token": "",
        })

    @app.post("/Matchmaking/matchmake/dorm")
    async def rr_matchmake_dorm(authorization: str | None = Header(default=None)) -> JSONResponse:
        session = session_for(authorization)
        return JSONResponse({
            "success": True,
            "roomId": session.account_id + 1_000_000_000,
            "roomInstanceId": f"flux-dorm-{session.account_id}",
            "photon": {"configured": bool(PHOTON_APP_ID), "region": PHOTON_REGION},
        })

    empty_routes = [
        "/api/relationships/v2/get", "/api/messages/v2/get", "/Room_server/featuredrooms/current",
        "/Room_server/rooms/hot", "/Room_server/rooms/ownedby/me", "/Room_server/rooms/visitedby/me",
        "/api/rooms/v1/filters", "/api/inventions/v2/mine", "/outfits/me/saved",
        "/clubs/club/mine/member", "/clubs/subscription/mine/member", "/Commerce/api/catalog/v1/all",
        "/api/gameconfigs/v1/all", "/api/playerevents/v1/all",
    ]
    for index, path in enumerate(empty_routes):
        async def empty_handler(authorization: str | None = Header(default=None)) -> JSONResponse:
            session_for(authorization)
            return JSONResponse([])
        empty_handler.__name__ = f"rr_empty_{index}"
        app.add_api_route(path, empty_handler, methods=["GET"])

    @app.get("/api/communityboard/v2/current")
    async def rr_community(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({"entries": []})

    @app.get("/api/sanitize/v1/isPure")
    async def rr_sanitize_pure(authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        return JSONResponse({"isPure": True})

    @app.post("/api/sanitize/v1")
    async def rr_sanitize(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
        session_for(authorization)
        try:
            body = await request.json()
        except Exception:
            body = {}
        return JSONResponse(body)

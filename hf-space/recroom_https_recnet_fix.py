from __future__ import annotations

import ipaddress
import os
import secrets
import struct
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response

import recroom_black_viewport_fix as black_viewport
import recroom_nameserver_fix as nameserver_fix
from recroom_build_fingerprint import FINGERPRINT
from recroom_wine_pool import LOCAL_SERVICE_PREFIXES, RecRoomWinePool


_PATCH_REVISION = "aug25-2021-public-https-relay-v2"
_TRANSPORT_REVISION = "public-https-bootstrap-public-https-services-aug25-2021-v2"
_PUBLIC_BOOTSTRAP_PATH = "/api/recroom-bootstrap/ns"
_PUBLIC_RELAY_PREFIX = "/api/recroom-bridge"
_EXACT_METADATA_RELATIVE = Path(str(FINGERPRINT["criticalFiles"]["global-metadata.dat"]["path"]))
_METADATA_MAGIC = 0xFAB11BAF
_METADATA_VERSION = 24
_LITERAL_ENTRY_SIZE = 8
_ORIGINAL_CAPABILITY = RecRoomWinePool.capability
_ORIGINAL_PROVISION = RecRoomWinePool.provision
_ORIGINAL_DESTROY = RecRoomWinePool.destroy
_RELAY_LOCK = threading.RLock()
_PENDING_SESSION_TOKENS: dict[str, tuple[str, float]] = {}
_RELAY_BY_IP: dict[str, str] = {}
_RELAY_SESSIONS: dict[str, dict[str, Any]] = {}
_RELAY_TTL_SECONDS = 20 * 60

nameserver_fix.LOCAL_NAMESERVER_PATH = "/nsx"
nameserver_fix._PATCH_REVISION = _PATCH_REVISION


def _configured_public_base() -> str:
    value = os.environ.get(
        "RECROOM_PUBLIC_BASE_URL",
        "https://echoxr-ripoteam-cloud-pc.hf.space",
    ).rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise RuntimeError("Rec Room public bootstrap base must use trusted HTTPS.")
    return value


def _public_base(self: RecRoomWinePool) -> str:
    value = str(getattr(self, "public_base_url", "") or "").rstrip("/") or _configured_public_base()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        return _configured_public_base()
    return value


def _loopback_ip(local_base: str) -> str:
    parsed = urllib.parse.urlsplit(local_base)
    raw = parsed.hostname or ""
    address = ipaddress.ip_address(raw)
    if address.version != 4 or not address.is_loopback:
        raise RuntimeError(f"Rec Room sandbox service address must be IPv4 loopback, got {raw!r}.")
    return str(address)


def _normalize_relay_path(raw: str) -> str:
    value = raw or "/"
    while value.startswith("//"):
        value = value[1:]
    query_index = value.find("?")
    path_only = value[:query_index] if query_index >= 0 else value
    suffix = value[query_index:] if query_index >= 0 else ""
    for prefix in LOCAL_SERVICE_PREFIXES:
        if path_only == prefix or path_only.startswith(prefix + "/"):
            stripped = path_only[len(prefix):] or "/"
            return (stripped if stripped.startswith("/") else "/" + stripped) + suffix
    return (path_only if path_only.startswith("/") else "/" + path_only) + suffix


def _prune_relays() -> None:
    now = time.time()
    with _RELAY_LOCK:
        dead_pending = [host for host, (_, expiry) in _PENDING_SESSION_TOKENS.items() if expiry <= now]
        for host in dead_pending:
            _PENDING_SESSION_TOKENS.pop(host, None)
        dead_relays = [relay for relay, row in _RELAY_SESSIONS.items() if float(row.get("expiresAt", 0)) <= now]
        for relay in dead_relays:
            row = _RELAY_SESSIONS.pop(relay, {})
            ip = str(row.get("ip") or "")
            if ip and _RELAY_BY_IP.get(ip) == relay:
                _RELAY_BY_IP.pop(ip, None)


def _register_relay(self: RecRoomWinePool, ip: str) -> str:
    _prune_relays()
    host_id = ""
    try:
        with self.lock:
            for candidate in self.instances.values():
                if candidate.loopback_ip == ip and not candidate.destroying:
                    host_id = candidate.host_id
                    break
    except Exception:
        host_id = ""
    if not host_id:
        raise RuntimeError(f"Could not resolve Wine sandbox for RecNet relay address {ip}.")

    with _RELAY_LOCK:
        pending = _PENDING_SESSION_TOKENS.get(host_id)
        if not pending or pending[1] <= time.time():
            raise RuntimeError("RecNet relay session token was not registered for this Wine sandbox.")
        session_token = pending[0]
        old = _RELAY_BY_IP.get(ip)
        if old:
            _RELAY_SESSIONS.pop(old, None)
        relay_id = secrets.token_urlsafe(24)
        expiry = time.time() + _RELAY_TTL_SECONDS
        _RELAY_BY_IP[ip] = relay_id
        _RELAY_SESSIONS[relay_id] = {
            "sessionToken": session_token,
            "hostId": host_id,
            "ip": ip,
            "expiresAt": expiry,
        }
        return relay_id


def _bootstrap_url(self: RecRoomWinePool, ip: str) -> str:
    address = ipaddress.ip_address(ip)
    if address.version != 4 or not address.is_loopback:
        raise RuntimeError(f"Rec Room bootstrap address must be IPv4 loopback, got {ip!r}.")
    return f"{_public_base(self)}{_PUBLIC_BOOTSTRAP_PATH}?ip={urllib.parse.quote(str(address), safe='.')}"


def _relocate_bootstrap_literal(path: Path, target: str) -> int:
    if not path.is_file():
        raise RuntimeError(f"Exact target Rec Room metadata is missing: {path}.")

    data = bytearray(path.read_bytes())
    if len(data) < 24:
        raise RuntimeError("Exact target Rec Room IL2CPP metadata is truncated.")

    sanity, version = struct.unpack_from("<II", data, 0)
    if sanity != _METADATA_MAGIC or version != _METADATA_VERSION:
        raise RuntimeError(
            f"Unexpected IL2CPP metadata header: sanity={sanity:#x}, version={version}; "
            f"expected {_METADATA_MAGIC:#x}/v{_METADATA_VERSION}."
        )

    literal_offset, literal_count, literal_data_offset, literal_data_count = struct.unpack_from("<IIII", data, 8)
    if literal_offset < 24 or literal_count <= 0 or literal_count % _LITERAL_ENTRY_SIZE:
        raise RuntimeError("Target Rec Room IL2CPP literal table is invalid.")
    if literal_data_offset <= literal_offset or literal_data_count <= 0:
        raise RuntimeError("Target Rec Room IL2CPP literal-data table is invalid.")
    if literal_offset + literal_count > len(data):
        raise RuntimeError("Target Rec Room IL2CPP literal table exceeds metadata size.")

    source = nameserver_fix.LEGACY_NAMESERVER_URL.encode("ascii")
    target_bytes = target.encode("ascii")
    positions: list[int] = []
    cursor = 0
    while True:
        found = data.find(source, cursor)
        if found < 0:
            break
        positions.append(found)
        cursor = found + 1

    matches: list[tuple[int, int]] = []
    for source_pos in positions:
        source_index = source_pos - literal_data_offset
        if source_index < 0:
            continue
        for entry in range(literal_offset, literal_offset + literal_count, _LITERAL_ENTRY_SIZE):
            length, data_index = struct.unpack_from("<II", data, entry)
            if length == len(source) and data_index == source_index:
                matches.append((entry, source_pos))

    if len(matches) != 1:
        raise RuntimeError(
            "Target Rec Room RecNet bootstrap literal could not be uniquely relocated "
            f"(occurrences={len(positions)}, literalEntries={len(matches)})."
        )

    entry, _source_pos = matches[0]
    new_file_offset = len(data)
    new_data_index = new_file_offset - literal_data_offset
    if new_data_index < 0 or new_data_index > 0xFFFFFFFF:
        raise RuntimeError("Relocated RecNet bootstrap string exceeds IL2CPP metadata index range.")
    if len(target_bytes) > 0xFFFFFFFF:
        raise RuntimeError("Relocated RecNet bootstrap URL is unexpectedly large.")

    data.extend(target_bytes)
    struct.pack_into("<II", data, entry, len(target_bytes), new_data_index)
    length_check, index_check = struct.unpack_from("<II", data, entry)
    resolved = bytes(data[literal_data_offset + index_check:literal_data_offset + index_check + length_check])
    if resolved != target_bytes:
        raise RuntimeError("Relocated RecNet bootstrap URL failed metadata self-verification.")

    nameserver_fix._atomic_replace(path, bytes(data), "aug25trustedbootstrap")
    return 1


def _patch_client_trusted(self: RecRoomWinePool, root: Path, local_base: str) -> int:
    ip = _loopback_ip(local_base)
    _register_relay(self, ip)
    target = _bootstrap_url(self, ip)
    return _relocate_bootstrap_literal(root / _EXACT_METADATA_RELATIVE, target)


def _provision_with_relay_token(
    self: RecRoomWinePool,
    host_id: str,
    session_id: str,
    session_token: str,
    on_progress: Any,
    on_ready: Any,
    on_failed: Any,
) -> tuple[bool, str | None]:
    with _RELAY_LOCK:
        _PENDING_SESSION_TOKENS[host_id] = (session_token, time.time() + _RELAY_TTL_SECONDS)
    try:
        return _ORIGINAL_PROVISION(self, host_id, session_id, session_token, on_progress, on_ready, on_failed)
    except Exception:
        with _RELAY_LOCK:
            _PENDING_SESSION_TOKENS.pop(host_id, None)
        raise


def _destroy_with_relay_cleanup(self: RecRoomWinePool, host_id: str) -> Any:
    with _RELAY_LOCK:
        _PENDING_SESSION_TOKENS.pop(host_id, None)
        dead = [relay for relay, row in _RELAY_SESSIONS.items() if row.get("hostId") == host_id]
        for relay in dead:
            row = _RELAY_SESSIONS.pop(relay, {})
            ip = str(row.get("ip") or "")
            if ip and _RELAY_BY_IP.get(ip) == relay:
                _RELAY_BY_IP.pop(ip, None)
    return _ORIGINAL_DESTROY(self, host_id)


def _relay_entry(relay_id: str) -> dict[str, Any] | None:
    _prune_relays()
    with _RELAY_LOCK:
        row = _RELAY_SESSIONS.get(relay_id)
        return dict(row) if row else None


def install_public_bootstrap_route(application: Any) -> None:
    existing = {getattr(route, "path", None) for route in getattr(application, "routes", [])}

    if _PUBLIC_BOOTSTRAP_PATH not in existing:
        async def recroom_public_nameserver(request: Request) -> JSONResponse:
            raw = str(request.query_params.get("ip") or "").strip()
            try:
                address = ipaddress.ip_address(raw)
            except ValueError:
                return JSONResponse({"ok": False, "error": "invalid loopback address"}, status_code=400)
            if address.version != 4 or not address.is_loopback:
                return JSONResponse({"ok": False, "error": "loopback address required"}, status_code=400)

            ip = str(address)
            _prune_relays()
            with _RELAY_LOCK:
                relay_id = _RELAY_BY_IP.get(ip, "")
                row = dict(_RELAY_SESSIONS.get(relay_id) or {}) if relay_id else {}
            if not relay_id or not row:
                return JSONResponse({"ok": False, "error": "RecNet relay is not ready"}, status_code=409)

            relay_base = f"{_configured_public_base()}{_PUBLIC_RELAY_PREFIX}/{relay_id}"
            payload = nameserver_fix._nameserver_payload(relay_base)
            return JSONResponse(
                payload,
                headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"},
            )

        application.add_api_route(
            _PUBLIC_BOOTSTRAP_PATH,
            recroom_public_nameserver,
            methods=["GET"],
            include_in_schema=False,
            name="recroom_public_bootstrap_nameserver",
        )

    relay_route = f"{_PUBLIC_RELAY_PREFIX}/{{relay_id}}/{{path:path}}"
    relay_root = f"{_PUBLIC_RELAY_PREFIX}/{{relay_id}}"
    existing = {getattr(route, "path", None) for route in getattr(application, "routes", [])}
    if relay_route in existing:
        return

    async def recroom_public_relay(relay_id: str, request: Request, path: str = "") -> Response:
        row = _relay_entry(relay_id)
        if not row:
            return JSONResponse({"ok": False, "error": "RecNet relay expired"}, status_code=401)
        session_token = str(row.get("sessionToken") or "")
        if not session_token:
            return JSONResponse({"ok": False, "error": "RecNet relay session missing"}, status_code=401)

        raw = "/" + (path or "")
        if request.url.query:
            raw += "?" + request.url.query
        normalized = _normalize_relay_path(raw)
        target = urllib.parse.urljoin(_configured_public_base().rstrip("/") + "/", normalized.lstrip("/"))
        body = await request.body()
        blocked = {
            "authorization", "connection", "content-length", "host", "transfer-encoding", "upgrade",
        }
        headers = {k: v for k, v in request.headers.items() if k.lower() not in blocked}
        headers["Authorization"] = f"Bearer {session_token}"
        headers["X-Flux-RecRoom-Host-Proxy"] = "wine-public-https"

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
                upstream = await client.request(request.method, target, headers=headers, content=body or None)
            response_headers: dict[str, str] = {}
            for key in ("content-type", "location", "cache-control", "etag", "last-modified"):
                value = upstream.headers.get(key)
                if value:
                    response_headers[key] = value
            response_headers["cache-control"] = response_headers.get("cache-control", "no-store")
            return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"RecNet relay upstream failed: {exc}"}, status_code=502)

    for route in (relay_route, relay_root):
        application.add_api_route(
            route,
            recroom_public_relay,
            methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"],
            include_in_schema=False,
            name="recroom_public_https_relay_" + ("path" if route == relay_route else "root"),
        )


def _capability_trusted(self: RecRoomWinePool) -> dict[str, Any]:
    payload = dict(_ORIGINAL_CAPABILITY(self))
    payload["recNetRedirectPatch"] = _PATCH_REVISION
    payload["recNetTransport"] = _TRANSPORT_REVISION
    payload["recNetPublicBootstrap"] = True
    payload["recNetBootstrapPath"] = _PUBLIC_BOOTSTRAP_PATH
    payload["recNetBootstrapBase"] = _public_base(self)
    payload["recNetBootstrapScheme"] = "https"
    payload["recNetBootstrapHost"] = "public-hf-space"
    payload["recNetLocalServiceScheme"] = "https"
    payload["recNetPublicServiceRelay"] = True
    payload["recNetPublicServiceRelayPrefix"] = _PUBLIC_RELAY_PREFIX
    payload["recNetMetadataLiteralRelocation"] = True
    payload["recNetTlsVerificationBypassed"] = False
    payload["recNetLoopbackTls"] = False
    return payload


nameserver_fix._patch_client = _patch_client_trusted
RecRoomWinePool._patch_client = _patch_client_trusted  # type: ignore[method-assign]
RecRoomWinePool._start_proxy = black_viewport._start_proxy_traced  # type: ignore[method-assign]
RecRoomWinePool.provision = _provision_with_relay_token  # type: ignore[method-assign]
RecRoomWinePool.destroy = _destroy_with_relay_cleanup  # type: ignore[method-assign]
RecRoomWinePool.capability = _capability_trusted  # type: ignore[method-assign]
print(f"Rec Room trusted public HTTPS relay loaded: {_PATCH_REVISION} / {_TRANSPORT_REVISION}")

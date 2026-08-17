from __future__ import annotations

import ipaddress
import os
import struct
import urllib.parse
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

import recroom_black_viewport_fix as black_viewport
import recroom_nameserver_fix as nameserver_fix
from recroom_wine_pool import RecRoomWinePool


_PATCH_REVISION = "may2022-public-bootstrap-v3"
_TRANSPORT_REVISION = "trusted-public-https-bootstrap-local-http-v1"
_PUBLIC_BOOTSTRAP_PATH = "/api/recroom-bootstrap/ns"
_EXACT_METADATA_RELATIVE = Path("RecRoom_Data/il2cpp_data/Metadata/global-metadata.dat")
_METADATA_MAGIC = 0xFAB11BAF
_METADATA_VERSION = 24
_LITERAL_ENTRY_SIZE = 8
_ORIGINAL_CAPABILITY = RecRoomWinePool.capability

# The trusted public bootstrap is intentionally the only HTTPS hop. It returns
# sandbox-local HTTP service URLs, so the RecNet bearer never leaves the host.
nameserver_fix.LOCAL_NAMESERVER_PATH = "/nsx"
nameserver_fix._DEFAULT_LOCAL_BASE = "http://127.0.0.1:81"
nameserver_fix._PATCH_REVISION = _PATCH_REVISION


def _loopback_ip(local_base: str) -> str:
    parsed = urllib.parse.urlsplit(local_base)
    raw = parsed.hostname or ""
    address = ipaddress.ip_address(raw)
    if address.version != 4 or not address.is_loopback:
        raise RuntimeError(f"Rec Room sandbox service address must be IPv4 loopback, got {raw!r}.")
    return str(address)


def _public_base(self: RecRoomWinePool) -> str:
    value = (
        str(getattr(self, "gateway_url", "") or "")
        or os.environ.get("RECROOM_PUBLIC_BASE_URL", "")
        or "https://echoxr-ripoteam-cloud-pc.hf.space"
    ).rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise RuntimeError("Rec Room public bootstrap base must use trusted HTTPS.")
    return value


def _bootstrap_url(self: RecRoomWinePool, ip: str) -> str:
    return f"{_public_base(self)}{_PUBLIC_BOOTSTRAP_PATH}?ip={urllib.parse.quote(ip, safe='.')}"


def _relocate_bootstrap_literal(path: Path, target: str) -> int:
    if not path.is_file():
        raise RuntimeError(f"Exact May 2022 metadata is missing: {path}.")

    data = bytearray(path.read_bytes())
    if len(data) < 24:
        raise RuntimeError("Exact May 2022 IL2CPP metadata is truncated.")

    sanity, version = struct.unpack_from("<II", data, 0)
    if sanity != _METADATA_MAGIC or version != _METADATA_VERSION:
        raise RuntimeError(
            f"Unexpected IL2CPP metadata header: sanity={sanity:#x}, version={version}; "
            f"expected {_METADATA_MAGIC:#x}/v{_METADATA_VERSION}."
        )

    literal_offset, literal_count, literal_data_offset, literal_data_count = struct.unpack_from(
        "<IIII", data, 8
    )
    if literal_offset < 24 or literal_count <= 0 or literal_count % _LITERAL_ENTRY_SIZE:
        raise RuntimeError("Exact May 2022 IL2CPP literal table is invalid.")
    if literal_data_offset <= literal_offset or literal_data_count <= 0:
        raise RuntimeError("Exact May 2022 IL2CPP literal-data table is invalid.")
    if literal_offset + literal_count > len(data):
        raise RuntimeError("Exact May 2022 IL2CPP literal table exceeds metadata size.")

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
            "Exact May 2022 RecNet bootstrap literal could not be uniquely relocated "
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
    resolved = bytes(
        data[
            literal_data_offset + index_check:
            literal_data_offset + index_check + length_check
        ]
    )
    if resolved != target_bytes:
        raise RuntimeError("Relocated RecNet bootstrap URL failed metadata self-verification.")

    nameserver_fix._atomic_replace(path, bytes(data), "trustedbootstrap")
    return 1


def _patch_client_trusted(self: RecRoomWinePool, root: Path, local_base: str) -> int:
    ip = _loopback_ip(local_base)
    target = _bootstrap_url(self, ip)
    return _relocate_bootstrap_literal(root / _EXACT_METADATA_RELATIVE, target)


def install_public_bootstrap_route(application: Any) -> None:
    existing = {getattr(route, "path", None) for route in getattr(application, "routes", [])}
    if _PUBLIC_BOOTSTRAP_PATH in existing:
        return

    async def recroom_public_nameserver(request: Request) -> JSONResponse:
        raw = str(request.query_params.get("ip") or "").strip()
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            return JSONResponse({"ok": False, "error": "invalid loopback address"}, status_code=400)
        if address.version != 4 or not address.is_loopback:
            return JSONResponse({"ok": False, "error": "loopback address required"}, status_code=400)

        local_base = f"http://{address}:81"
        payload = nameserver_fix._nameserver_payload(local_base)
        return JSONResponse(
            payload,
            headers={
                "Cache-Control": "no-store, max-age=0",
                "Pragma": "no-cache",
            },
        )

    application.add_api_route(
        _PUBLIC_BOOTSTRAP_PATH,
        recroom_public_nameserver,
        methods=["GET"],
        include_in_schema=False,
        name="recroom_public_bootstrap_nameserver",
    )


def _capability_trusted(self: RecRoomWinePool) -> dict[str, Any]:
    payload = dict(_ORIGINAL_CAPABILITY(self))
    payload["recNetRedirectPatch"] = _PATCH_REVISION
    payload["recNetTransport"] = _TRANSPORT_REVISION
    payload["recNetPublicBootstrap"] = True
    payload["recNetBootstrapPath"] = _PUBLIC_BOOTSTRAP_PATH
    payload["recNetBootstrapBase"] = _public_base(self)
    payload["recNetLocalServiceScheme"] = "http"
    payload["recNetMetadataLiteralRelocation"] = True
    payload["recNetTls12"] = False
    payload["recNetTlsOpenSSL"] = False
    return payload


nameserver_fix._patch_client = _patch_client_trusted
RecRoomWinePool._patch_client = _patch_client_trusted  # type: ignore[method-assign]
RecRoomWinePool._start_proxy = black_viewport._start_proxy_traced  # type: ignore[method-assign]
RecRoomWinePool.capability = _capability_trusted  # type: ignore[method-assign]
print(f"Rec Room trusted RecNet bootstrap patch loaded: {_PATCH_REVISION} / {_TRANSPORT_REVISION}")

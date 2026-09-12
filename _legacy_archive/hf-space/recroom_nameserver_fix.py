from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from recroom_wine_pool import LOCAL_SERVICE_PREFIXES, RecRoomWinePool, WineInstance


LEGACY_NAMESERVER_URL = "https://ns.rec.net/?v=2"
LOCAL_NAMESERVER_PATH = "/nsx"
_DEFAULT_LOCAL_BASE = "http://127.0.0.1:81"
_EXACT_METADATA_RELATIVE = Path("RecRoom_Data/il2cpp_data/Metadata/global-metadata.dat")
# Confirmed by the compact endpoint scan of exact build 8751857. The code still
# searches the metadata if this offset ever disagrees, so the offset is an
# accelerator/diagnostic rather than an unsafe blind write.
_EXACT_NAMESERVER_OFFSET = 0xB5028
_PATCH_REVISION = "may2022-metadata-only-v1"

# Preserve the current host-routing prefixes that RipoTeamServer already uses.
# These paths are removed by RecRoomWinePool._normalize_path before forwarding
# requests to the compatibility gateway.
_SERVICE_SUFFIX = {
    "RecNetStatus": "",
    "Auth": "",
    "API": "",
    "WWW": "",
    "Notifications": "/no",
    "Images": "",
    "CDN": "",
    "Commerce": "/shop",
    "Matchmaking": "/m",
    "Storage": "",
    "Chat": "",
    "Leaderboard": "/leaderb",
    "Accounts": "/acct",
    "Link": "",
    "RoomComments": "",
    "Clubs": "/c",
    "Rooms": "/r",
    "PlatformNotifications": "/no",
    "Moderation": "",
    "DataCollection": "",
    "BugReporting": "",
    "Discovery": "/disco",
}


def _nameserver_payload(local_base: str) -> dict[str, str]:
    # Exact field names recovered from build 8751857's IL2CPP
    # FGCLJJMGLAI.NameServerResponse class.
    return {field: f"{local_base}{suffix}" for field, suffix in _SERVICE_SUFFIX.items()}


def _atomic_replace(path: Path, data: bytes, suffix: str) -> None:
    stat = path.stat()
    temp = path.with_name(path.name + f".{os.getpid()}.{suffix}")
    temp.write_bytes(data)
    os.chmod(temp, stat.st_mode)
    temp.replace(path)


def _patch_exact_metadata(root: Path, local_base: str) -> int:
    """Patch build 8751857's single RecNet-v2 bootstrap string.

    The exact May 19 2022 client does not embed the later api.rec.net/auth.rec.net
    service URLs. It asks https://ns.rec.net/?v=2 and receives those service
    addresses dynamically. Previous code walked the full ~6.8 GB client and then
    performed another full legacy-host scan on every Play request. The exact
    build fingerprint already guarantees this layout, so only the IL2CPP metadata
    file containing the bootstrap needs to be copied-on-write and changed.
    """

    target = f"{local_base}{LOCAL_NAMESERVER_PATH}"
    source_ascii = LEGACY_NAMESERVER_URL.encode("ascii")
    target_ascii = target.encode("ascii")
    default_ascii = f"{_DEFAULT_LOCAL_BASE}{LOCAL_NAMESERVER_PATH}".encode("ascii")
    if len(source_ascii) != len(target_ascii):
        raise RuntimeError(
            f"Legacy nameserver redirect is not length-safe: {LEGACY_NAMESERVER_URL!r} -> {target!r}."
        )

    path = root / _EXACT_METADATA_RELATIVE
    if not path.is_file():
        raise RuntimeError(
            "Exact May 2022 IL2CPP metadata is missing from the Wine sandbox; "
            f"expected {_EXACT_METADATA_RELATIVE.as_posix()}."
        )

    data = path.read_bytes()
    changed = 0
    prepared = 0

    # Fast path: the endpoint scan found the bootstrap at 0xB5028. Verify the
    # bytes before touching them so this can never corrupt a different build.
    if data[_EXACT_NAMESERVER_OFFSET:_EXACT_NAMESERVER_OFFSET + len(source_ascii)] == source_ascii:
        mutable = bytearray(data)
        mutable[_EXACT_NAMESERVER_OFFSET:_EXACT_NAMESERVER_OFFSET + len(source_ascii)] = target_ascii
        data = bytes(mutable)
        changed += 1
    else:
        # Defensive fallback within this one small metadata file only.
        count = data.count(source_ascii)
        if count:
            data = data.replace(source_ascii, target_ascii)
            changed += count

    # UTF-16 is not expected for the known build-8751857 occurrence, but keep a
    # tiny same-file fallback so a second encoded copy cannot escape routing.
    source_utf16 = LEGACY_NAMESERVER_URL.encode("utf-16le")
    target_utf16 = target.encode("utf-16le")
    default_utf16 = f"{_DEFAULT_LOCAL_BASE}{LOCAL_NAMESERVER_PATH}".encode("utf-16le")
    if len(source_utf16) != len(target_utf16):
        raise RuntimeError("Legacy RecNet nameserver redirect changed encoded length.")
    utf16_count = data.count(source_utf16)
    if utf16_count:
        data = data.replace(source_utf16, target_utf16)
        changed += utf16_count

    prepared += data.count(target_ascii)
    prepared += data.count(target_utf16)

    # If this sandbox was somehow cloned from a default-local prepared template,
    # retarget those bytes to its unique loopback address without a tree scan.
    if target_ascii != default_ascii:
        local_count = data.count(default_ascii)
        if local_count:
            data = data.replace(default_ascii, target_ascii)
            changed += local_count
    if target_utf16 != default_utf16:
        local_count = data.count(default_utf16)
        if local_count:
            data = data.replace(default_utf16, target_utf16)
            changed += local_count

    if changed:
        _atomic_replace(path, data, "nameserverpatch")

    total = max(changed, prepared)
    if total <= 0:
        raise RuntimeError(
            "Exact May 2022 RecNet v2 nameserver bootstrap was not found in global-metadata.dat."
        )
    return total


_original_patch_client = RecRoomWinePool._patch_client
_original_capability = RecRoomWinePool.capability


def _patch_client(self: RecRoomWinePool, root: Path, local_base: str) -> int:
    nameserver_count = _patch_exact_metadata(root, local_base)

    # Build 8751857 resolves services from the v2 nameserver. Its compact full
    # endpoint scan contains no later hardcoded api.rec.net/auth.rec.net family,
    # so rescanning gigabytes for them only makes Play sit at 46%. Keep an opt-in
    # diagnostic escape hatch, disabled in production.
    direct_count = 0
    if os.environ.get("RECROOM_MAY2022_SCAN_DIRECT_SERVICE_URLS", "0") == "1":
        try:
            direct_count = int(_original_patch_client(self, root, local_base) or 0)
        except RuntimeError as exc:
            if nameserver_count <= 0 or "known rec.net service URLs" not in str(exc):
                raise

    return nameserver_count + direct_count


def _capability_with_redirect_marker(self: RecRoomWinePool) -> dict[str, Any]:
    payload = dict(_original_capability(self))
    payload["recNetRedirectPatch"] = _PATCH_REVISION
    payload["recNetRedirectFile"] = _EXACT_METADATA_RELATIVE.as_posix()
    payload["recNetDirectUrlScan"] = os.environ.get("RECROOM_MAY2022_SCAN_DIRECT_SERVICE_URLS", "0") == "1"
    return payload


def _start_proxy(self: RecRoomWinePool, instance: WineInstance, session_token: str) -> None:
    gateway = self.gateway_url
    normalize = self._normalize_path
    local_base = f"http://{instance.loopback_ip}:81"
    nameserver_body = json.dumps(_nameserver_payload(local_base), separators=(",", ":")).encode("utf-8")

    class ProxyHandler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args) -> None:
            return

        def _json(self, status: int, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.send_header("cache-control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def _proxy(self) -> None:
            raw = self.path or "/"
            path_only = raw.split("?", 1)[0]
            if path_only == LOCAL_NAMESERVER_PATH:
                # v2 response contract recovered directly from the exact
                # build-8751857 IL2CPP NameServerResponse type.
                self._json(200, nameserver_body)
                return
            if raw == "/flux/local-health":
                payload = json.dumps({
                    "ok": True,
                    "provider": "wine",
                    "targetBuild": "recroom-2022-05-19",
                    "nameserver": True,
                    "redirectPatch": _PATCH_REVISION,
                }).encode()
                self._json(200, payload)
                return

            normalized = normalize(raw)
            target_url = urllib.parse.urljoin(gateway.rstrip("/") + "/", normalized.lstrip("/"))
            length = int(self.headers.get("content-length", "0") or "0")
            body = self.rfile.read(min(length, 32 * 1024 * 1024)) if length else None
            blocked = {"authorization", "connection", "content-length", "host", "transfer-encoding", "upgrade"}
            headers = {k: v for k, v in self.headers.items() if k.lower() not in blocked}
            headers["Authorization"] = f"Bearer {session_token}"
            headers["X-Flux-RecRoom-Host-Proxy"] = "wine"
            request = urllib.request.Request(target_url, data=body, method=self.command, headers=headers)
            try:
                response = urllib.request.urlopen(request, timeout=30)
                status = response.status
                payload = response.read()
                response_headers = response.headers
            except urllib.error.HTTPError as exc:
                status = exc.code
                payload = exc.read()
                response_headers = exc.headers
            except Exception as exc:
                status = 502
                payload = json.dumps({"ok": False, "error": str(exc)}).encode()
                response_headers = {"content-type": "application/json"}

            self.send_response(status)
            content_type = response_headers.get("content-type") if hasattr(response_headers, "get") else None
            if content_type:
                self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(payload)))
            self.send_header("cache-control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        do_GET = _proxy
        do_HEAD = _proxy
        do_POST = _proxy
        do_PUT = _proxy
        do_PATCH = _proxy
        do_DELETE = _proxy

    server = ThreadingHTTPServer((instance.loopback_ip, 81), ProxyHandler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever,
        name=f"recroom-wine-proxy-{instance.host_id[-6:]}",
        daemon=True,
    )
    thread.start()
    instance.proxy_server = server
    instance.proxy_thread = thread


RecRoomWinePool._patch_client = _patch_client  # type: ignore[method-assign]
RecRoomWinePool._start_proxy = _start_proxy  # type: ignore[method-assign]
RecRoomWinePool.capability = _capability_with_redirect_marker  # type: ignore[method-assign]
print(f"Rec Room May 2022 RecNet redirect patch loaded: {_PATCH_REVISION}")

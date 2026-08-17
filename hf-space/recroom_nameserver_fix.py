from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from recroom_wine_pool import LOCAL_SERVICE_PREFIXES, RecRoomWinePool, WineInstance


LEGACY_NAMESERVER_URL = "https://ns.rec.net/?v=2"
LOCAL_NAMESERVER_PATH = "/nsx"
_DEFAULT_LOCAL_BASE = "http://127.0.0.1:81"

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


def _patch_legacy_nameserver(root: Path, local_base: str) -> int:
    target = f"{local_base}{LOCAL_NAMESERVER_PATH}"
    if len(target.encode("ascii")) != len(LEGACY_NAMESERVER_URL.encode("ascii")):
        raise RuntimeError(
            f"Legacy nameserver redirect is not length-safe: {LEGACY_NAMESERVER_URL!r} -> {target!r}."
        )

    allowed_ext = {
        ".exe", ".dll", ".dat", ".bytes", ".json", ".txt", ".config", ".xml",
        ".assets", ".resource", ".ress", ".bin", ".manifest",
    }
    allowed_names = {"globalgamemanagers", "globalgamemanagers.assets"}
    max_size = 768 * 1024 * 1024
    changed_total = 0
    prepared_total = 0

    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in {".git", "Logs", "Crashes"}]
        for name in files:
            path = Path(dirpath) / name
            if name.endswith((".flux-backup", ".update-backup", ".update-new")):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size <= 0 or stat.st_size > max_size:
                continue
            if path.suffix.lower() not in allowed_ext and name.lower() not in allowed_names:
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue

            patched = bytearray(data)
            changed = False
            for encoding in ("ascii", "utf-16le"):
                source = LEGACY_NAMESERVER_URL.encode(encoding)
                destination = target.encode(encoding)
                default_local = f"{_DEFAULT_LOCAL_BASE}{LOCAL_NAMESERVER_PATH}".encode(encoding)
                if len(source) != len(destination):
                    raise RuntimeError("Legacy RecNet nameserver redirect changed encoded length.")
                for candidate in (source, default_local):
                    cursor = 0
                    while True:
                        index = bytes(patched).find(candidate, cursor)
                        if index < 0:
                            break
                        patched[index:index + len(candidate)] = destination
                        cursor = index + len(candidate)
                        changed = True
                        changed_total += 1
                prepared_total += bytes(patched).count(destination)

            if changed:
                # The session client tree is a hard-link clone of the immutable
                # base build. Replace the file atomically so only this sandbox's
                # copy is modified.
                temp = path.with_name(path.name + f".{os.getpid()}.nameserverpatch")
                temp.write_bytes(patched)
                os.chmod(temp, stat.st_mode)
                temp.replace(path)

    return max(changed_total, prepared_total)


_original_patch_client = RecRoomWinePool._patch_client


def _patch_client(self: RecRoomWinePool, root: Path, local_base: str) -> int:
    nameserver_count = _patch_legacy_nameserver(root, local_base)
    direct_count = 0
    try:
        direct_count = int(_original_patch_client(self, root, local_base) or 0)
    except RuntimeError as exc:
        # Build 8751857 bootstraps service URLs dynamically through
        # https://ns.rec.net/?v=2, so it legitimately may not contain any of the
        # later hardcoded https://api.rec.net / https://auth.rec.net strings.
        if nameserver_count <= 0 or "known rec.net service URLs" not in str(exc):
            raise
    if nameserver_count <= 0 and direct_count <= 0:
        raise RuntimeError("May 2022 RecNet nameserver URL was not found in the client.")
    return nameserver_count + direct_count


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


RecRoomWinePool._patch_client = _patch_client
RecRoomWinePool._start_proxy = _start_proxy

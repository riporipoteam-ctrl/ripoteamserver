from __future__ import annotations

import json
import os
import shutil
import ssl
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import recroom_black_viewport_fix as black_viewport
import recroom_nameserver_fix as nameserver_fix
from recroom_wine_pool import RecRoomWinePool, WineInstance


_PATCH_REVISION = "may2022-https-loopback-v2"
_TRANSPORT_REVISION = "https-local-tls12-v1"
_LOCAL_NAMESERVER_PATH = "/ns"
_TLS_LOCK = threading.Lock()
_ORIGINAL_NAMESERVER_PATCH = nameserver_fix._patch_client
_ORIGINAL_CAPABILITY = RecRoomWinePool.capability

# BestHTTP in build 8751857 rejects plain HTTP before the request is sent.
# Keep the exact bootstrap length at 23 bytes:
#   https://ns.rec.net/?v=2
#   https://127.0.0.1:81/ns
nameserver_fix.LOCAL_NAMESERVER_PATH = _LOCAL_NAMESERVER_PATH
nameserver_fix._DEFAULT_LOCAL_BASE = "https://127.0.0.1:81"
nameserver_fix._PATCH_REVISION = _PATCH_REVISION


def _https_base(local_base: str) -> str:
    parsed = urllib.parse.urlsplit(local_base)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 81
    base = f"https://{host}:{port}"
    target = f"{base}{_LOCAL_NAMESERVER_PATH}"
    if len(target.encode("ascii")) != len(nameserver_fix.LEGACY_NAMESERVER_URL.encode("ascii")):
        raise RuntimeError(
            f"HTTPS RecNet loopback redirect is not length-safe: {nameserver_fix.LEGACY_NAMESERVER_URL!r} -> {target!r}."
        )
    return base


def _patch_client_https(self: RecRoomWinePool, root: Path, local_base: str) -> int:
    return int(_ORIGINAL_NAMESERVER_PATCH(self, root, _https_base(local_base)) or 0)


def _tls_material(self: RecRoomWinePool, ip: str) -> tuple[Path, Path]:
    tls_dir = self.data_dir / "_recnet_tls"
    tls_dir.mkdir(parents=True, exist_ok=True)
    safe = ip.replace(".", "_")
    cert = tls_dir / f"{safe}.crt"
    key = tls_dir / f"{safe}.key"
    if cert.is_file() and key.is_file():
        return cert, key

    with _TLS_LOCK:
        if cert.is_file() and key.is_file():
            return cert, key
        openssl = shutil.which("openssl")
        if not openssl:
            raise RuntimeError("OpenSSL is unavailable for the local RecNet HTTPS bridge.")

        config = tls_dir / f"{safe}.cnf"
        config.write_text(
            "\n".join(
                [
                    "[req]",
                    "distinguished_name = dn",
                    "x509_extensions = v3_req",
                    "prompt = no",
                    "[dn]",
                    f"CN = {ip}",
                    "O = RipoTeam Local RecNet",
                    "[v3_req]",
                    f"subjectAltName = IP:{ip}",
                    "keyUsage = critical,digitalSignature,keyEncipherment",
                    "extendedKeyUsage = serverAuth",
                    "basicConstraints = critical,CA:FALSE",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        temp_key = key.with_suffix(".key.tmp")
        temp_cert = cert.with_suffix(".crt.tmp")
        for path in (temp_key, temp_cert):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

        result = subprocess.run(
            [
                openssl,
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-sha256",
                "-nodes",
                "-days",
                "30",
                "-config",
                str(config),
                "-extensions",
                "v3_req",
                "-keyout",
                str(temp_key),
                "-out",
                str(temp_cert),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0 or not temp_key.is_file() or not temp_cert.is_file():
            raise RuntimeError(
                "Could not create the local RecNet TLS certificate: "
                + " ".join((result.stderr or result.stdout or "unknown OpenSSL failure").split())[-1200:]
            )
        os.chmod(temp_key, 0o600)
        temp_key.replace(key)
        temp_cert.replace(cert)
        return cert, key


def _tls_context(self: RecRoomWinePool, ip: str) -> ssl.SSLContext:
    cert, key = _tls_material(self, ip)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    try:
        context.set_ciphers("ECDHE+AESGCM:ECDHE+AES:RSA+AESGCM:RSA+AES:!aNULL:!MD5")
    except ssl.SSLError:
        pass
    context.load_cert_chain(certfile=str(cert), keyfile=str(key))
    return context


def _start_proxy_https(self: RecRoomWinePool, instance: WineInstance, session_token: str) -> None:
    gateway = self.gateway_url
    normalize = self._normalize_path
    local_base = f"https://{instance.loopback_ip}:81"
    nameserver_body = json.dumps(
        nameserver_fix._nameserver_payload(local_base), separators=(",", ":")
    ).encode("utf-8")
    tls_context = _tls_context(self, instance.loopback_ip)

    black_viewport._trace(
        instance,
        "proxy-start",
        listen=f"{instance.loopback_ip}:81",
        scheme="https",
        tls="TLSv1.2",
        gateway=gateway,
        nameserverPath=_LOCAL_NAMESERVER_PATH,
        nameserverBytes=len(nameserver_body),
    )

    class ProxyHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _json(self, status: int, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(payload)))
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
            self.close_connection = True

        def _proxy(self) -> None:
            raw = self.path or "/"
            path_only = raw.split("?", 1)[0]

            if path_only == _LOCAL_NAMESERVER_PATH:
                body = nameserver_fix._nameserver_payload(local_base)
                black_viewport._trace(
                    instance,
                    "nameserver",
                    method=self.command,
                    raw=raw,
                    status=200,
                    scheme="https",
                    body=body,
                )
                self._json(200, nameserver_body)
                return

            if raw == "/flux/local-health":
                payload = json.dumps(
                    {
                        "ok": True,
                        "provider": "wine",
                        "targetBuild": "recroom-2022-05-19",
                        "nameserver": True,
                        "redirectPatch": _PATCH_REVISION,
                        "transport": _TRANSPORT_REVISION,
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
                black_viewport._trace(instance, "local-health", method=self.command, raw=raw, status=200)
                self._json(200, payload)
                return

            normalized = normalize(raw)
            target_url = urllib.parse.urljoin(gateway.rstrip("/") + "/", normalized.lstrip("/"))
            length = int(self.headers.get("content-length", "0") or "0")
            body = self.rfile.read(min(length, 32 * 1024 * 1024)) if length else None
            blocked = {
                "authorization",
                "connection",
                "content-length",
                "host",
                "transfer-encoding",
                "upgrade",
            }
            headers = {k: v for k, v in self.headers.items() if k.lower() not in blocked}
            headers["Authorization"] = f"Bearer {session_token}"
            headers["X-Flux-RecRoom-Host-Proxy"] = "wine-https"
            request = urllib.request.Request(target_url, data=body, method=self.command, headers=headers)

            error_text = ""
            try:
                response = urllib.request.urlopen(request, timeout=30)
                status = response.status
                payload = response.read()
                response_headers = response.headers
            except urllib.error.HTTPError as exc:
                status = exc.code
                payload = exc.read()
                response_headers = exc.headers
                error_text = f"HTTPError:{exc.code}"
            except Exception as exc:
                status = 502
                error_text = f"{type(exc).__name__}:{exc}"
                payload = json.dumps({"ok": False, "error": str(exc)}).encode("utf-8")
                response_headers = {"content-type": "application/json"}

            black_viewport._trace(
                instance,
                "request",
                method=self.command,
                raw=raw[:1000],
                normalized=normalized[:1000],
                target=target_url[:1200],
                status=status,
                responseBytes=len(payload),
                error=error_text[:800],
            )

            self.send_response(status)
            content_type = response_headers.get("content-type") if hasattr(response_headers, "get") else None
            if content_type:
                self.send_header("content-type", content_type)
            self.send_header("content-length", str(len(payload)))
            self.send_header("cache-control", "no-store")
            self.send_header("connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
            self.close_connection = True

        do_GET = _proxy
        do_HEAD = _proxy
        do_POST = _proxy
        do_PUT = _proxy
        do_PATCH = _proxy
        do_DELETE = _proxy

    class TLSHTTPServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

        def get_request(server_self):  # type: ignore[no-untyped-def]
            raw_socket, address = super(TLSHTTPServer, server_self).get_request()
            raw_socket.settimeout(12)
            try:
                tls_socket = tls_context.wrap_socket(raw_socket, server_side=True)
                tls_socket.settimeout(None)
                black_viewport._trace(
                    instance,
                    "tls-handshake",
                    peer=f"{address[0]}:{address[1]}",
                    protocol=tls_socket.version() or "",
                    cipher=(tls_socket.cipher() or ("", "", 0))[0],
                )
                return tls_socket, address
            except Exception as exc:
                black_viewport._trace(
                    instance,
                    "tls-handshake-error",
                    peer=f"{address[0]}:{address[1]}",
                    error=f"{type(exc).__name__}:{exc}"[:1000],
                )
                try:
                    raw_socket.close()
                except Exception:
                    pass
                raise

    server = TLSHTTPServer((instance.loopback_ip, 81), ProxyHandler)
    thread = threading.Thread(
        target=server.serve_forever,
        name=f"recroom-wine-https-proxy-{instance.host_id[-6:]}",
        daemon=True,
    )
    thread.start()
    instance.proxy_server = server
    instance.proxy_thread = thread


def _capability_https(self: RecRoomWinePool) -> dict[str, Any]:
    payload = dict(_ORIGINAL_CAPABILITY(self))
    payload["recNetRedirectPatch"] = _PATCH_REVISION
    payload["recNetTransport"] = _TRANSPORT_REVISION
    payload["recNetNameserverPath"] = _LOCAL_NAMESERVER_PATH
    payload["recNetSchemeAcceptedByClient"] = "https"
    payload["recNetTls12"] = True
    payload["recNetTlsOpenSSL"] = bool(shutil.which("openssl"))
    return payload


nameserver_fix._patch_client = _patch_client_https
RecRoomWinePool._patch_client = _patch_client_https  # type: ignore[method-assign]
RecRoomWinePool._start_proxy = _start_proxy_https  # type: ignore[method-assign]
RecRoomWinePool.capability = _capability_https  # type: ignore[method-assign]
print(f"Rec Room HTTPS RecNet compatibility patch loaded: {_PATCH_REVISION} / {_TRANSPORT_REVISION}")

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import recroom_nameserver_fix as nameserver_fix
import recroom_wine_runtime_fix as runtime_fix
from recroom_wine_pool import RecRoomWinePool, WineInstance


_PATCH_REVISION = "black-viewport-v1-glcore-recnet-trace"
_TRACE_NAME = "recnet-proxy.jsonl"
_ORIGINAL_CAPABILITY = RecRoomWinePool.capability


def _trace_path(instance: WineInstance) -> Path:
    return instance.work_dir / _TRACE_NAME


def _trace(instance: WineInstance, event: str, **fields: Any) -> None:
    payload = {"ts": round(time.time(), 3), "event": event, **fields}
    try:
        instance.work_dir.mkdir(parents=True, exist_ok=True)
        with _trace_path(instance).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, separators=(",", ":"), default=str) + "\n")
    except Exception:
        pass


def _trace_tail(instance: WineInstance, limit: int = 4200) -> str:
    try:
        text = _trace_path(instance).read_text(errors="replace")
        rows = [line.strip() for line in text.splitlines() if line.strip()]
        return " | ".join(rows[-18:])[-limit:]
    except Exception:
        return ""


def _start_proxy_traced(self: RecRoomWinePool, instance: WineInstance, session_token: str) -> None:
    gateway = self.gateway_url
    normalize = self._normalize_path
    local_base = f"http://{instance.loopback_ip}:81"
    nameserver_body = json.dumps(
        nameserver_fix._nameserver_payload(local_base), separators=(",", ":")
    ).encode("utf-8")

    _trace(
        instance,
        "proxy-start",
        listen=f"{instance.loopback_ip}:81",
        gateway=gateway,
        nameserverPath=nameserver_fix.LOCAL_NAMESERVER_PATH,
        nameserverBytes=len(nameserver_body),
    )

    class ProxyHandler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: Any) -> None:
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

            if path_only == nameserver_fix.LOCAL_NAMESERVER_PATH:
                _trace(
                    instance,
                    "nameserver",
                    method=self.command,
                    raw=raw,
                    status=200,
                    body=nameserver_fix._nameserver_payload(local_base),
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
                        "redirectPatch": nameserver_fix._PATCH_REVISION,
                        "blackViewportPatch": _PATCH_REVISION,
                    },
                    separators=(",", ":"),
                ).encode()
                _trace(instance, "local-health", method=self.command, raw=raw, status=200)
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
            headers["X-Flux-RecRoom-Host-Proxy"] = "wine"
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
                payload = json.dumps({"ok": False, "error": str(exc)}).encode()
                response_headers = {"content-type": "application/json"}

            _trace(
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


def _provision_visible(
    self: RecRoomWinePool,
    host_id: str,
    session_id: str,
    session_token: str,
    on_progress: Callable[[str, int], None],
    on_ready: Callable[[str], None],
    on_failed: Callable[[str], None],
) -> tuple[bool, str | None]:
    can_start, reason = self.can_provision()
    if not can_start:
        return False, reason
    try:
        display, port, loopback = self._slot()
    except Exception as exc:
        return False, str(exc)

    work_dir = self.data_dir / host_id
    instance = WineInstance(
        host_id=host_id,
        session_id=session_id,
        work_dir=work_dir,
        client_dir=work_dir / "client",
        prefix_dir=work_dir / "prefix",
        display_number=display,
        stream_port=port,
        loopback_ip=loopback,
        stream_token=__import__("secrets").token_urlsafe(32),
        sink_name=("rr_" + host_id.replace("-", "_")[-18:])[:28],
    )
    setattr(instance, "render_profile", "starting")
    setattr(instance, "render_metrics", "")
    with self.lock:
        self.instances[host_id] = instance

    def progress(phase: str, value: int) -> None:
        instance.phase = phase
        instance.progress = value
        on_progress(phase, value)

    def fail(message: str) -> None:
        trace = _trace_tail(instance)
        detail = message
        if trace:
            detail += " | RecNet trace: " + trace
        on_failed(detail[:7000])

    def worker() -> None:
        try:
            work_dir.mkdir(parents=True, exist_ok=True)
            _trace(instance, "session-start", hostId=host_id, sessionId=session_id)
            progress("creating-sandbox", 8)
            self._start_x(instance)
            progress("preparing-audio", 15)
            self._start_audio(instance)
            progress("preparing-windows-runtime", 24)
            self._ensure_base_prefix(instance.display_number)
            self._clone_prefix(instance.prefix_dir)
            progress("linking-game-image", 34)
            self._clone_tree_hardlinks(self.client_dir, instance.client_dir)
            progress("connecting-flux-account", 46)

            redirects = nameserver_fix._patch_client(
                self, instance.client_dir, f"http://{instance.loopback_ip}:81"
            )
            if redirects <= 0:
                raise RuntimeError("Rec Room service redirect preparation failed.")

            self._start_proxy(instance, session_token)
            progress("starting-browser-stream", 56)
            self._start_stream(instance)

            layout = self._client_layout()
            source_exe = Path(str(layout["exePath"]))
            exe = instance.client_dir / source_exe.relative_to(self.client_dir)
            if not exe.is_file():
                raise RuntimeError("Rec Room executable was not cloned into the Wine sandbox.")

            profiles: list[tuple[str, list[str], bool]] = [
                ("glcore-llvmpipe", ["-force-glcore", "-force-clamped"], True),
                (
                    "d3d11-bitblt-singlethreaded",
                    [
                        "-force-d3d11",
                        "-force-d3d11-bitblt-model",
                        "-force-d3d11-singlethreaded",
                        "-force-gfx-direct",
                    ],
                    False,
                ),
                (
                    "d3d11-bitblt",
                    ["-force-d3d11", "-force-d3d11-bitblt-model"],
                    False,
                ),
            ]

            diagnostics: list[str] = []
            base_env = self._wine_env(instance)
            progress("launching-game", 68)
            selected = False

            for profile_name, render_args, use_gl in profiles:
                if instance.destroying:
                    return
                setattr(instance, "render_profile", profile_name)
                _trace(instance, "render-attempt", profile=profile_name)

                attempt_env = dict(base_env)
                if use_gl:
                    attempt_env["LIBGL_ALWAYS_SOFTWARE"] = "1"
                    attempt_env["GALLIUM_DRIVER"] = "llvmpipe"

                glog_path = work_dir / f"wine-game-{profile_name}.log"
                glog = glog_path.open("ab", buffering=0)
                command = [
                    str(self.wine),
                    str(exe),
                    "-screen-fullscreen",
                    "0",
                    "-screen-width",
                    str(self.width),
                    "-screen-height",
                    str(self.height),
                    "-logFile",
                    "-",
                    *render_args,
                ]
                glog.write((f"\n[ripo] black-viewport-patch={_PATCH_REVISION} profile={profile_name} command={' '.join(command)}\n").encode())
                instance.game_process = subprocess.Popen(
                    command,
                    cwd=instance.client_dir,
                    env=attempt_env,
                    stdout=glog,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )

                window_deadline = time.time() + 45
                visible = False
                while time.time() < window_deadline:
                    if instance.destroying:
                        return
                    if instance.game_process.poll() is not None:
                        break
                    if self._window_visible(instance):
                        visible = True
                        break
                    time.sleep(0.75)

                if not visible:
                    code = instance.game_process.poll()
                    diagnostics.append(
                        f"{profile_name}: no visible window (exit={code}); "
                        f"log={runtime_fix._log_tail(glog_path, 1100)}"
                    )
                    _trace(instance, "render-no-window", profile=profile_name, exit=code)
                    runtime_fix._stop_wine_attempt(self, instance)
                    continue

                render_deadline = time.time() + (45 if use_gl else 30)
                last_metrics = "no frame"
                while time.time() < render_deadline:
                    if instance.destroying:
                        return
                    if instance.game_process.poll() is not None:
                        break
                    rendered, last_metrics = runtime_fix._has_rendered_content(instance)
                    setattr(instance, "render_metrics", last_metrics)
                    if rendered:
                        selected = True
                        _trace(instance, "render-ready", profile=profile_name, metrics=last_metrics)
                        runtime_fix._start_silence_feeder(self, instance)
                        progress("ready", 100)
                        on_ready(self.public_stream_url(instance))
                        break
                    time.sleep(1.25)

                if selected:
                    break

                diagnostics.append(
                    f"{profile_name}: viewport stayed black ({last_metrics}); "
                    f"log={runtime_fix._log_tail(glog_path, 1500)}"
                )
                _trace(
                    instance,
                    "render-black",
                    profile=profile_name,
                    metrics=last_metrics,
                    exit=instance.game_process.poll() if instance.game_process else None,
                )
                runtime_fix._stop_wine_attempt(self, instance)

            if not selected or not instance.game_process:
                trace = _trace_tail(instance, 3000)
                detail = " | ".join(diagnostics[-3:])
                if trace:
                    detail += " | RecNet trace: " + trace
                raise RuntimeError(
                    "Rec Room opened but no renderer produced a visible game viewport. " + detail
                )

            code = instance.game_process.wait()
            if not instance.destroying:
                fail(f"Rec Room exited under Wine with code {code}.")
        except Exception as exc:
            if not instance.destroying:
                fail(str(exc))
            self.destroy(host_id)

    threading.Thread(
        target=worker,
        name=f"recroom-visible-{host_id[-8:]}",
        daemon=True,
    ).start()
    return True, None


def _capability(self: RecRoomWinePool) -> dict[str, Any]:
    payload = dict(_ORIGINAL_CAPABILITY(self))
    payload["blackViewportPatch"] = _PATCH_REVISION
    payload["rendererOrder"] = [
        "glcore-llvmpipe",
        "d3d11-bitblt-singlethreaded",
        "d3d11-bitblt",
    ]
    payload["glcoreFallbackDefault"] = True
    payload["recNetProxyTrace"] = True
    payload["recNetTraceFile"] = _TRACE_NAME
    payload["recNetRedirectPatch"] = nameserver_fix._PATCH_REVISION
    payload["recNetDirectUrlScan"] = False
    return payload


RecRoomWinePool._patch_client = nameserver_fix._patch_client  # type: ignore[method-assign]
RecRoomWinePool._start_proxy = _start_proxy_traced  # type: ignore[method-assign]
RecRoomWinePool.provision = _provision_visible  # type: ignore[method-assign]
RecRoomWinePool.capability = _capability  # type: ignore[method-assign]
print(f"Rec Room black viewport fix loaded: {_PATCH_REVISION}")

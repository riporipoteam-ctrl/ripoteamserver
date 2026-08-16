from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import Response, StreamingResponse


TARGET_BUILD_ID = "recroom-2022-05-19"


@dataclass
class VmInstance:
    host_id: str
    work_dir: Path
    overlay_path: Path
    config_iso: Path
    stream_port: int
    process: subprocess.Popen[Any] | None = None
    created_at: float = field(default_factory=time.time)
    phase: str = "queued"
    progress: int = 0
    destroying: bool = False


class RecRoomVmPool:
    """Disposable Windows/KVM VM pool for one Rec Room session per VM.

    The provider intentionally refuses to pretend that an ordinary container is
    a VM. It only enables itself when /dev/kvm, QEMU, an ISO builder and the
    operator-provided Windows golden image are all present.

    The golden image is expected to contain Windows, the legally obtained May
    19 2022 Rec Room client, this repo's Windows host agent, Python, and a startup
    task that runs recroom-vm-guest-startup.ps1. Per-session configuration is
    mounted read-only as a tiny ISO; the Windows system disk is a qcow2 overlay
    over the immutable golden image and is deleted when the player leaves.
    """

    def __init__(self, data_dir: Path, public_base_url: str, host_key: str) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.public_base_url = public_base_url.rstrip("/")
        self.host_key = host_key
        self.provider = os.environ.get("RECROOM_VM_PROVIDER", "kvm").strip().lower() or "kvm"
        self.enabled = os.environ.get("RECROOM_VM_ENABLED", "1").strip() not in {"0", "false", "False"}
        self.base_image = Path(os.environ.get("RECROOM_WINDOWS_BASE_IMAGE", "/var/lib/ripoteam/recroom/windows-recroom-2022.qcow2"))
        self.base_format = os.environ.get("RECROOM_WINDOWS_BASE_FORMAT", "qcow2").strip() or "qcow2"
        self.qemu = shutil.which(os.environ.get("RECROOM_QEMU_BIN", "qemu-system-x86_64"))
        self.qemu_img = shutil.which(os.environ.get("RECROOM_QEMU_IMG_BIN", "qemu-img"))
        self.iso_builder = shutil.which("genisoimage") or shutil.which("mkisofs")
        self.kvm_path = Path("/dev/kvm")
        self.max_vms = max(1, min(32, int(os.environ.get("RECROOM_VM_MAX", "4"))))
        self.memory_mb = max(3072, int(os.environ.get("RECROOM_VM_MEMORY_MB", "6144")))
        self.vcpus = max(2, int(os.environ.get("RECROOM_VM_VCPUS", "2")))
        self.stream_port_start = max(1024, int(os.environ.get("RECROOM_VM_STREAM_PORT_START", "6200")))
        self.stream_port_end = max(self.stream_port_start, int(os.environ.get("RECROOM_VM_STREAM_PORT_END", "6399")))
        self.gpu_args = shlex.split(os.environ.get("RECROOM_VM_GPU_ARGS", ""))
        self.extra_args = shlex.split(os.environ.get("RECROOM_VM_QEMU_EXTRA_ARGS", ""))
        self.lock = threading.RLock()
        self.instances: dict[str, VmInstance] = {}

    def capability(self) -> dict[str, Any]:
        checks = {
            "enabled": self.enabled,
            "provider": self.provider,
            "kvm": self.kvm_path.exists() and os.access(self.kvm_path, os.R_OK | os.W_OK),
            "qemu": bool(self.qemu),
            "qemuImg": bool(self.qemu_img),
            "isoBuilder": bool(self.iso_builder),
            "baseImage": self.base_image.is_file(),
            "hostKey": bool(self.host_key),
            "gpuConfigured": bool(self.gpu_args),
        }
        supported = bool(
            self.enabled
            and self.provider == "kvm"
            and checks["kvm"]
            and checks["qemu"]
            and checks["qemuImg"]
            and checks["isoBuilder"]
            and checks["baseImage"]
            and checks["hostKey"]
        )
        reasons: list[str] = []
        if not self.enabled:
            reasons.append("RECROOM_VM_ENABLED is disabled")
        if self.provider != "kvm":
            reasons.append(f"unsupported VM provider {self.provider!r}")
        if not checks["kvm"]:
            reasons.append("/dev/kvm is not available to this Linux runtime")
        if not checks["qemu"]:
            reasons.append("qemu-system-x86_64 is not installed")
        if not checks["qemuImg"]:
            reasons.append("qemu-img is not installed")
        if not checks["isoBuilder"]:
            reasons.append("genisoimage/mkisofs is not installed")
        if not checks["baseImage"]:
            reasons.append(f"Windows golden image is missing at {self.base_image}")
        if not checks["hostKey"]:
            reasons.append("RECROOM_HOST_KEY is not configured")
        # A VM can technically boot with emulated VGA, but Rec Room needs real
        # 3D acceleration for acceptable gameplay. Treat missing GPU mapping as
        # a warning instead of lying that it is production-ready.
        warning = None if checks["gpuConfigured"] else (
            "No RECROOM_VM_GPU_ARGS are configured. Windows may boot with emulated VGA, "
            "but Rec Room will not be considered production-playable without a supported GPU/vGPU mapping."
        )
        with self.lock:
            running = sum(1 for item in self.instances.values() if item.process and item.process.poll() is None)
        return {
            "provider": "kvm",
            "supported": supported,
            "readyForGame": bool(supported and checks["gpuConfigured"]),
            "checks": checks,
            "reason": "; ".join(reasons) if reasons else None,
            "warning": warning,
            "runningVms": running,
            "maxVms": self.max_vms,
            "baseImage": str(self.base_image),
        }

    def can_provision(self) -> tuple[bool, str | None]:
        capability = self.capability()
        if not capability["supported"]:
            return False, str(capability.get("reason") or "KVM runtime is unavailable.")
        with self.lock:
            alive = [item for item in self.instances.values() if item.process and item.process.poll() is None and not item.destroying]
            if len(alive) >= self.max_vms:
                return False, f"All {self.max_vms} RipoTeamServer Windows VM slot(s) are busy."
        return True, None

    def _pick_stream_port(self) -> int:
        with self.lock:
            used = {item.stream_port for item in self.instances.values() if not item.destroying}
        for port in range(self.stream_port_start, self.stream_port_end + 1):
            if port in used:
                continue
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
            finally:
                sock.close()
        raise RuntimeError("No free Rec Room VM stream proxy port is available.")

    def _write_config_iso(self, instance: VmInstance) -> None:
        assert self.iso_builder
        seed = instance.work_dir / "seed"
        seed.mkdir(parents=True, exist_ok=True)
        public_stream_prefix = f"{self.public_base_url}/api/recroom-vm/stream/{urllib.parse.quote(instance.host_id, safe='')}"
        config = {
            "server": self.public_base_url,
            "hostId": instance.host_id,
            "hostKey": self.host_key,
            "name": f"RipoTeamServer VM {instance.host_id[-8:]}",
            "buildId": TARGET_BUILD_ID,
            "capacity": 1,
            "clientDir": os.environ.get("RECROOM_VM_GUEST_CLIENT_DIR", r"C:\RipoTeam\RecRoom2022"),
            "agentDir": os.environ.get("RECROOM_VM_GUEST_AGENT_DIR", r"C:\RipoTeam\RecRoomHost"),
            "streamStartCommand": (
                'powershell.exe -NoProfile -ExecutionPolicy Bypass -File '
                '"%RECROOM_AGENT_DIR%\\start-recroom-browser-stream.ps1" -LocalOnly'
            ),
            "streamStopCommand": (
                'powershell.exe -NoProfile -ExecutionPolicy Bypass -File '
                '"%RECROOM_AGENT_DIR%\\stop-recroom-browser-stream.ps1"'
            ),
            "vm": {
                "provider": "kvm",
                "streamProxyPrefix": public_stream_prefix,
                "hostForwardPort": instance.stream_port,
            },
        }
        (seed / "recroom-vm-config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        subprocess.run(
            [self.iso_builder, "-quiet", "-J", "-R", "-V", "RIPOREC", "-o", str(instance.config_iso), str(seed)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _build_qemu_command(self, instance: VmInstance) -> list[str]:
        assert self.qemu
        video_args = self.gpu_args if self.gpu_args else ["-vga", "std"]
        command = [
            self.qemu,
            "-name", f"ripo-recroom-{instance.host_id[-12:]}",
            "-enable-kvm",
            "-machine", "q35,accel=kvm",
            "-cpu", "host",
            "-smp", str(self.vcpus),
            "-m", str(self.memory_mb),
            "-drive", f"file={instance.overlay_path},if=virtio,format=qcow2,cache=writeback,discard=unmap",
            "-drive", f"file={instance.config_iso},media=cdrom,readonly=on",
            "-device", "ich9-intel-hda",
            "-device", "hda-duplex",
            "-netdev", f"user,id=net0,hostfwd=tcp:127.0.0.1:{instance.stream_port}-:6081",
            "-device", "virtio-net-pci,netdev=net0",
            "-display", "none",
            "-no-reboot",
        ]
        command.extend(video_args)
        command.extend(self.extra_args)
        return command

    def provision(
        self,
        host_id: str,
        on_progress: Callable[[str, int], None],
        on_failed: Callable[[str], None],
    ) -> tuple[bool, str | None]:
        can_start, reason = self.can_provision()
        if not can_start:
            return False, reason
        if not self.qemu_img:
            return False, "qemu-img is unavailable."

        work_dir = self.data_dir / host_id
        overlay = work_dir / "windows-overlay.qcow2"
        config_iso = work_dir / "session-config.iso"
        try:
            stream_port = self._pick_stream_port()
        except Exception as exc:
            return False, str(exc)
        instance = VmInstance(
            host_id=host_id,
            work_dir=work_dir,
            overlay_path=overlay,
            config_iso=config_iso,
            stream_port=stream_port,
        )
        with self.lock:
            self.instances[host_id] = instance

        def worker() -> None:
            try:
                work_dir.mkdir(parents=True, exist_ok=True)
                instance.phase = "creating-overlay"
                instance.progress = 10
                on_progress("creating-overlay", 10)
                subprocess.run(
                    [
                        self.qemu_img,
                        "create",
                        "-f", "qcow2",
                        "-F", self.base_format,
                        "-b", str(self.base_image),
                        str(overlay),
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )

                instance.phase = "creating-session-media"
                instance.progress = 20
                on_progress("creating-session-media", 20)
                self._write_config_iso(instance)

                instance.phase = "booting-windows"
                instance.progress = 35
                on_progress("booting-windows", 35)
                log = (work_dir / "qemu.log").open("ab", buffering=0)
                process = subprocess.Popen(
                    self._build_qemu_command(instance),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                instance.process = process

                # Give QEMU a moment to fail fast on invalid KVM/GPU/device
                # configuration. After this point the Windows guest startup task
                # registers itself with the broker and pulls the queued game job.
                time.sleep(2.0)
                if process.poll() is not None:
                    raise RuntimeError(f"Windows VM exited during boot with code {process.returncode}.")
                instance.phase = "waiting-for-windows-agent"
                instance.progress = 45
                on_progress("waiting-for-windows-agent", 45)

                code = process.wait()
                if not instance.destroying and code != 0:
                    on_failed(f"Windows VM exited unexpectedly with code {code}.")
            except Exception as exc:
                on_failed(str(exc)[:500])
                self.destroy(host_id)

        threading.Thread(target=worker, name=f"recroom-vm-{host_id[-8:]}", daemon=True).start()
        return True, None

    def progress(self, host_id: str) -> dict[str, Any] | None:
        with self.lock:
            instance = self.instances.get(host_id)
            if not instance:
                return None
            return {
                "phase": instance.phase,
                "progress": instance.progress,
                "streamPort": instance.stream_port,
                "createdAt": instance.created_at,
                "running": bool(instance.process and instance.process.poll() is None),
                "destroying": instance.destroying,
            }

    def rewrite_stream_url(self, host_id: str, stream_url: str) -> str:
        with self.lock:
            instance = self.instances.get(host_id)
        if not instance:
            return stream_url
        parsed = urllib.parse.urlsplit(stream_url)
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            return stream_url
        path = parsed.path.lstrip("/")
        base = f"{self.public_base_url}/api/recroom-vm/stream/{urllib.parse.quote(host_id, safe='')}"
        rewritten = f"{base}/{path}" if path else f"{base}/"
        if parsed.query:
            rewritten += f"?{parsed.query}"
        return rewritten

    def proxy_target(self, host_id: str, path: str, query: str = "") -> str:
        with self.lock:
            instance = self.instances.get(host_id)
            if not instance or instance.destroying:
                raise HTTPException(status_code=404, detail="Rec Room VM stream is not available.")
            port = instance.stream_port
        suffix = "/" + path.lstrip("/") if path else "/"
        url = f"http://127.0.0.1:{port}{suffix}"
        return f"{url}?{query}" if query else url

    def destroy(self, host_id: str) -> None:
        with self.lock:
            instance = self.instances.get(host_id)
            if not instance or instance.destroying:
                return
            instance.destroying = True
            instance.phase = "destroying"

        def worker() -> None:
            process = instance.process
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=8)
                except Exception:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except Exception:
                        pass
            shutil.rmtree(instance.work_dir, ignore_errors=True)
            with self.lock:
                self.instances.pop(host_id, None)

        threading.Thread(target=worker, name=f"recroom-vm-destroy-{host_id[-8:]}", daemon=True).start()


def install_recroom_vm_routes(app: Any, pool: RecRoomVmPool) -> None:
    @app.get("/api/recroom-vm/capabilities")
    async def recroom_vm_capabilities() -> dict[str, Any]:
        return {"ok": True, **pool.capability()}

    async def proxy(request: Request, host_id: str, path: str) -> Response:
        target = pool.proxy_target(host_id, path, request.url.query)
        method = request.method.upper()
        body = await request.body() if method in {"POST", "PUT", "PATCH"} else None
        headers: dict[str, str] = {}
        content_type = request.headers.get("content-type")
        if content_type:
            headers["content-type"] = content_type

        client = httpx.AsyncClient(timeout=httpx.Timeout(connect=3.0, read=None, write=10.0, pool=3.0))
        upstream = await client.send(
            client.build_request(method, target, headers=headers, content=body),
            stream=True,
        )
        response_headers = {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        }
        for name in ("content-length", "content-disposition"):
            if upstream.headers.get(name):
                response_headers[name] = upstream.headers[name]
        media_type = upstream.headers.get("content-type")

        async def iterator():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            iterator(),
            status_code=upstream.status_code,
            media_type=media_type,
            headers=response_headers,
        )

    @app.api_route("/api/recroom-vm/stream/{host_id}/", methods=["GET", "POST"])
    async def recroom_vm_stream_root(request: Request, host_id: str) -> Response:
        return await proxy(request, host_id, "")

    @app.api_route("/api/recroom-vm/stream/{host_id}/{path:path}", methods=["GET", "POST"])
    async def recroom_vm_stream_path(request: Request, host_id: str, path: str) -> Response:
        return await proxy(request, host_id, path)

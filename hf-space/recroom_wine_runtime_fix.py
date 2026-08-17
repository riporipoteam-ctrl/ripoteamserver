from __future__ import annotations

import io
import os
import shutil
import signal
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageStat

from recroom_wine_pool import RecRoomWinePool, WineInstance


_RUNTIME_REVISION = "render-audio-v2-bitblt"
_ORIGINAL_START_AUDIO = RecRoomWinePool._start_audio
_ORIGINAL_DESTROY = RecRoomWinePool.destroy
_ORIGINAL_PROGRESS = RecRoomWinePool.progress
_ORIGINAL_CAPABILITY = RecRoomWinePool.capability


def _terminate_process(process: subprocess.Popen[Any] | None, timeout: float = 4.0) -> None:
    if not process or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=timeout)
    except Exception:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass


def _start_audio_with_clock(self: RecRoomWinePool, instance: WineInstance) -> None:
    """Create and unsuspend the per-session Pulse sink without extra startup load."""
    _ORIGINAL_START_AUDIO(self, instance)
    if not self.pactl:
        return

    source = f"{instance.sink_name}.monitor"
    for command in (
        [self.pactl, "suspend-sink", instance.sink_name, "0"],
        [self.pactl, "suspend-source", source, "0"],
        [self.pactl, "set-sink-mute", instance.sink_name, "0"],
        [self.pactl, "set-source-mute", source, "0"],
    ):
        try:
            subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False)
        except Exception:
            pass
    setattr(instance, "silence_process", None)


def _start_silence_feeder(self: RecRoomWinePool, instance: WineInstance) -> None:
    """Clock the monitor only after the game has produced a real frame."""
    existing = getattr(instance, "silence_process", None)
    if existing and existing.poll() is None:
        return

    log = (instance.work_dir / "audio-silence.log").open("ab", buffering=0)
    pacat = shutil.which("pacat")
    process: subprocess.Popen[Any] | None = None
    if pacat:
        process = subprocess.Popen(
            [
                pacat,
                "--playback",
                f"--device={instance.sink_name}",
                "--format=s16le",
                "--rate=48000",
                "--channels=2",
                "/dev/zero",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        time.sleep(0.35)
        if process.poll() is not None:
            process = None

    if process is None and self.ffmpeg:
        process = subprocess.Popen(
            [
                self.ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-re",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=stereo",
                "-ac",
                "2",
                "-ar",
                "48000",
                "-f",
                "pulse",
                "-device",
                instance.sink_name,
                f"ripo-silence-{instance.host_id[-8:]}",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        time.sleep(0.5)

    setattr(instance, "silence_process", process)


def _frame_metrics(instance: WineInstance) -> tuple[float, int, float]:
    token = urllib.parse.quote(instance.stream_token, safe="")
    url = f"http://127.0.0.1:{instance.stream_port}/frame.jpg?token={token}&t={time.time_ns()}"
    request = urllib.request.Request(url, headers={"Cache-Control": "no-store"})
    with urllib.request.urlopen(request, timeout=5) as response:
        image = Image.open(io.BytesIO(response.read())).convert("RGB")
    width, height = image.size
    inner = image.crop(
        (
            max(80, int(width * 0.08)),
            max(70, int(height * 0.10)),
            min(width - 80, int(width * 0.92)),
            min(height - 60, int(height * 0.90)),
        )
    )
    stat = ImageStat.Stat(inner)
    mean = float(sum(stat.mean) / 3.0)
    extrema = inner.getextrema()
    spread = int(max(high - low for low, high in extrema))
    small = inner.resize((160, 90))
    nonblack = sum(1 for pixel in small.getdata() if max(pixel) > 12)
    ratio = float(nonblack / (160 * 90))
    return mean, spread, ratio


def _has_rendered_content(instance: WineInstance) -> tuple[bool, str]:
    try:
        mean, spread, ratio = _frame_metrics(instance)
    except Exception as exc:
        return False, f"frame-error={type(exc).__name__}:{exc}"
    ok = bool(mean > 2.0 and spread > 12 and ratio > 0.005)
    return ok, f"mean={mean:.3f} spread={spread} nonblack={ratio:.6f}"


def _log_tail(path: Path, limit: int = 2600) -> str:
    try:
        text = path.read_text(errors="replace")
        return " ".join(text[-limit:].split())
    except Exception:
        return ""


def _stop_wine_attempt(self: RecRoomWinePool, instance: WineInstance) -> None:
    _terminate_process(instance.game_process)
    instance.game_process = None
    if self.wineserver:
        try:
            env = self._wine_env(instance)
            subprocess.run(
                [self.wineserver, "-k"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except Exception:
            pass
    time.sleep(1.0)


def _provision_render_checked(
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
    with self.lock:
        self.instances[host_id] = instance

    def progress(phase: str, value: int) -> None:
        instance.phase = phase
        instance.progress = value
        on_progress(phase, value)

    def worker() -> None:
        try:
            work_dir.mkdir(parents=True, exist_ok=True)
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
            redirects = self._patch_client(instance.client_dir, f"http://{instance.loopback_ip}:81")
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

            # Unity's D3D11 BitBlt swapchain is the conservative Windows 7-style
            # presentation path. Wine/Xvfb does not provide a normal Windows DWM,
            # so prefer BitBlt over a modern flip-model swapchain. OpenGL is kept
            # opt-in because the software GL path can recycle a small Space under
            # load before the broker can report a useful failure.
            profiles: list[tuple[str, list[str]]] = [
                (
                    "d3d11-bitblt-singlethreaded",
                    [
                        "-force-d3d11",
                        "-force-d3d11-bitblt-model",
                        "-force-d3d11-singlethreaded",
                        "-force-gfx-direct",
                    ],
                ),
                (
                    "d3d11-bitblt-no-singlethreaded",
                    [
                        "-force-d3d11",
                        "-force-d3d11-bitblt-model",
                        "-force-d3d11-no-singlethreaded",
                    ],
                ),
                (
                    "d3d11-bitblt",
                    ["-force-d3d11", "-force-d3d11-bitblt-model"],
                ),
                (
                    "d3d11-singlethreaded",
                    ["-force-d3d11", "-force-d3d11-singlethreaded"],
                ),
            ]
            if os.environ.get("RECROOM_ENABLE_GLCORE_FALLBACK", "0") == "1":
                profiles.append(("glcore", ["-force-glcore", "-force-clamped"]))

            diagnostics: list[str] = []
            env = self._wine_env(instance)
            progress("launching-game", 68)

            selected = False
            for profile_name, render_args in profiles:
                if instance.destroying:
                    return
                setattr(instance, "render_profile", profile_name)
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
                glog.write((f"\n[ripo] render-profile={profile_name} command={' '.join(command)}\n").encode())
                instance.game_process = subprocess.Popen(
                    command,
                    cwd=instance.client_dir,
                    env=env,
                    stdout=glog,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )

                window_deadline = time.time() + 40
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
                    diagnostics.append(f"{profile_name}: no visible window (exit={code}); log={_log_tail(glog_path, 900)}")
                    _stop_wine_attempt(self, instance)
                    continue

                render_deadline = time.time() + 28
                last_metrics = "no frame"
                while time.time() < render_deadline:
                    if instance.destroying:
                        return
                    if instance.game_process.poll() is not None:
                        break
                    rendered, last_metrics = _has_rendered_content(instance)
                    if rendered:
                        selected = True
                        setattr(instance, "render_metrics", last_metrics)
                        _start_silence_feeder(self, instance)
                        progress("ready", 100)
                        on_ready(self.public_stream_url(instance))
                        break
                    time.sleep(1.5)

                if selected:
                    break

                diagnostics.append(f"{profile_name}: viewport stayed black ({last_metrics}); log={_log_tail(glog_path, 1200)}")
                _stop_wine_attempt(self, instance)

            if not selected or not instance.game_process:
                detail = " | ".join(diagnostics[-4:])
                raise RuntimeError("Rec Room opened but no renderer produced a visible game viewport. " + detail)

            code = instance.game_process.wait()
            if not instance.destroying:
                on_failed(f"Rec Room exited under Wine with code {code}.")
        except Exception as exc:
            if not instance.destroying:
                on_failed(str(exc)[:3500])
            self.destroy(host_id)

    threading.Thread(target=worker, name=f"recroom-wine-{host_id[-8:]}", daemon=True).start()
    return True, None


def _progress_with_runtime(self: RecRoomWinePool, host_id: str) -> dict[str, Any] | None:
    payload = _ORIGINAL_PROGRESS(self, host_id)
    if not payload:
        return payload
    with self.lock:
        instance = self.instances.get(host_id)
        if instance:
            payload["renderProfile"] = str(getattr(instance, "render_profile", ""))
            payload["renderMetrics"] = str(getattr(instance, "render_metrics", ""))
            silence = getattr(instance, "silence_process", None)
            payload["audioClockRunning"] = bool(silence and silence.poll() is None)
    return payload


def _capability_with_runtime_marker(self: RecRoomWinePool) -> dict[str, Any]:
    payload = dict(_ORIGINAL_CAPABILITY(self))
    payload["runtimePatch"] = _RUNTIME_REVISION
    payload["renderReadyCheck"] = True
    payload["audioClock"] = "pulse-null-sink-silence-after-visible-frame"
    payload["glcoreFallbackDefault"] = False
    return payload


def _destroy_with_audio_clock(self: RecRoomWinePool, host_id: str) -> None:
    with self.lock:
        instance = self.instances.get(host_id)
        silence = getattr(instance, "silence_process", None) if instance else None
    _terminate_process(silence)
    _ORIGINAL_DESTROY(self, host_id)


RecRoomWinePool._start_audio = _start_audio_with_clock  # type: ignore[method-assign]
RecRoomWinePool.provision = _provision_render_checked  # type: ignore[method-assign]
RecRoomWinePool.progress = _progress_with_runtime  # type: ignore[method-assign]
RecRoomWinePool.capability = _capability_with_runtime_marker  # type: ignore[method-assign]
RecRoomWinePool.destroy = _destroy_with_audio_clock  # type: ignore[method-assign]
print(f"Rec Room Wine runtime patch loaded: {_RUNTIME_REVISION}")

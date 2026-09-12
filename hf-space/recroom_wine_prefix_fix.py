from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from recroom_wine_pool import RecRoomWinePool


_BOOTSTRAP_REVISION = "hardened-v3"
_ORIGINAL_CAPABILITY = RecRoomWinePool.capability


def _compact(value: str, limit: int = 900) -> str:
    text = " ".join((value or "").strip().split())
    return text[-limit:] if text else ""


def _wait_for_display(pool: RecRoomWinePool, display: int, log: Any) -> None:
    """Give the freshly-started Xvfb a deterministic readiness check."""
    if not pool.xdotool:
        time.sleep(1.0)
        return
    env = os.environ.copy()
    env["DISPLAY"] = f":{display}"
    for attempt in range(30):
        try:
            probe = subprocess.run(
                [pool.xdotool, "getdisplaygeometry"],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3,
                check=False,
            )
            if probe.returncode == 0:
                log.write(
                    f"[ripo] Xvfb ready on :{display}: {_compact(probe.stdout, 200)}\n".encode()
                )
                return
            if attempt in {0, 9, 19, 29}:
                log.write(
                    f"[ripo] Xvfb readiness attempt {attempt + 1}: "
                    f"rc={probe.returncode} stderr={_compact(probe.stderr, 300)}\n".encode()
                )
        except Exception as exc:
            if attempt in {0, 9, 19, 29}:
                log.write(f"[ripo] Xvfb readiness error: {type(exc).__name__}: {exc}\n".encode())
        time.sleep(0.2)
    raise RuntimeError(f"Xvfb display :{display} did not become ready for Wine.")


def _ensure_base_prefix(self: RecRoomWinePool, display: int) -> None:
    system_reg = self.base_prefix / "system.reg"
    if system_reg.is_file():
        return

    with self.prefix_lock:
        if system_reg.is_file():
            return
        if not self.wine:
            raise RuntimeError("Wine is unavailable.")

        building = self.base_prefix.with_name(self.base_prefix.name + ".building")
        runtime = Path(f"/tmp/ripo-recroom-runtime-{os.getuid()}")
        runtime.mkdir(parents=True, exist_ok=True, mode=0o700)

        env = os.environ.copy()
        env.update(
            {
                "DISPLAY": f":{display}",
                "WINEPREFIX": str(building),
                "WINEARCH": "win64",
                "WINEDEBUG": "-all",
                "XDG_RUNTIME_DIR": str(runtime),
                "WINEDLLOVERRIDES": env.get(
                    "WINEDLLOVERRIDES",
                    "winemenubuilder.exe=d;mscoree,mshtml=",
                ),
            }
        )

        log_path = self.data_dir / "wine-base-prefix.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # Debian's generic wineboot wrapper can depend on a 32-bit loader even
        # when the actual game is 64-bit. Invoke Wine's built-in wineboot.exe
        # through the selected engine first, then keep wrapper fallbacks.
        commands: list[list[str]] = [
            [self.wine, "wineboot.exe", "--init"],
            [self.wine, "wineboot.exe", "--update"],
            [self.wine, "wineboot", "--init"],
        ]
        if self.wineboot:
            commands.extend(
                [
                    [self.wineboot, "--init"],
                    [self.wineboot, "--update"],
                ]
            )

        diagnostics: list[str] = []
        with log_path.open("ab", buffering=0) as log:
            log.write(
                f"\n[ripo] Rec Room Wine prefix bootstrap revision={_BOOTSTRAP_REVISION} "
                f"wine={self.wine!r} wineboot={self.wineboot!r} wineserver={self.wineserver!r}\n".encode()
            )
            _wait_for_display(self, display, log)

            seen: set[tuple[str, ...]] = set()
            for command in commands:
                key = tuple(command)
                if key in seen:
                    continue
                seen.add(key)

                shutil.rmtree(building, ignore_errors=True)
                building.mkdir(parents=True, exist_ok=True)
                log.write(f"[ripo] trying: {' '.join(command)}\n".encode())
                try:
                    result = subprocess.run(
                        command,
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        timeout=120,
                        check=False,
                    )
                    out = _compact(result.stdout, 500)
                    err = _compact(result.stderr, 1200)
                    diagnostic = (
                        f"{' '.join(command)} rc={result.returncode}; "
                        f"stderr={err or '<empty>'}; stdout={out or '<empty>'}"
                    )
                    diagnostics.append(diagnostic)
                    log.write((f"[ripo] {diagnostic}\n").encode())
                except subprocess.TimeoutExpired as exc:
                    diagnostic = (
                        f"{' '.join(command)} timed out; "
                        f"stderr={_compact(str(exc.stderr or ''), 900) or '<empty>'}"
                    )
                    diagnostics.append(diagnostic)
                    log.write((f"[ripo] {diagnostic}\n").encode())
                except Exception as exc:
                    diagnostic = f"{' '.join(command)} {type(exc).__name__}: {exc}"
                    diagnostics.append(diagnostic)
                    log.write((f"[ripo] {diagnostic}\n").encode())

                if self.wineserver:
                    try:
                        wait = subprocess.run(
                            [self.wineserver, "-w"],
                            env=env,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            timeout=45,
                            check=False,
                        )
                        log.write(
                            f"[ripo] wineserver -w rc={wait.returncode} "
                            f"stderr={_compact(wait.stderr, 500) or '<empty>'}\n".encode()
                        )
                    except subprocess.TimeoutExpired:
                        log.write(b"[ripo] wineserver -w timed out; checking registry anyway.\n")
                    except Exception as exc:
                        log.write(f"[ripo] wineserver -w error: {type(exc).__name__}: {exc}\n".encode())

                deadline = time.time() + 12
                while time.time() < deadline and not (building / "system.reg").is_file():
                    time.sleep(0.5)

                if (building / "system.reg").is_file():
                    log.write(b"[ripo] shared win64 prefix created successfully.\n")
                    if self.wineserver:
                        try:
                            subprocess.run(
                                [self.wineserver, "-k"],
                                env=env,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                timeout=12,
                                check=False,
                            )
                        except Exception:
                            pass
                    shutil.rmtree(self.base_prefix, ignore_errors=True)
                    building.replace(self.base_prefix)
                    return

                if self.wineserver:
                    try:
                        subprocess.run(
                            [self.wineserver, "-k"],
                            env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=12,
                            check=False,
                        )
                    except Exception:
                        pass

        files: list[str] = []
        try:
            files = sorted(path.name for path in building.iterdir())[:40]
        except Exception:
            pass
        shutil.rmtree(building, ignore_errors=True)
        detail = " | ".join(diagnostics[-3:])
        raise RuntimeError(
            f"Wine prefix bootstrap {_BOOTSTRAP_REVISION} failed. Prefix files: {files}. "
            + (detail or "No Wine bootstrap command created system.reg.")
        )


def _capability_with_bootstrap_marker(self: RecRoomWinePool) -> dict[str, Any]:
    payload = dict(_ORIGINAL_CAPABILITY(self))
    payload["prefixBootstrap"] = _BOOTSTRAP_REVISION
    payload["prefixBootstrapBound"] = getattr(type(self)._ensure_base_prefix, "__module__", "") == __name__
    payload["wineBinary"] = self.wine or ""
    payload["winebootBinary"] = self.wineboot or ""
    return payload


# Patch the class before app_server_v2 constructs the production pool. The
# capability marker makes it externally verifiable that this module executed.
RecRoomWinePool._ensure_base_prefix = _ensure_base_prefix  # type: ignore[method-assign]
RecRoomWinePool.capability = _capability_with_bootstrap_marker  # type: ignore[method-assign]
print(f"Rec Room Wine prefix bootstrap patch loaded: {_BOOTSTRAP_REVISION}")

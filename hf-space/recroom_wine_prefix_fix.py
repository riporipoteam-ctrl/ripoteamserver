from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from recroom_wine_pool import RecRoomWinePool


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
        shutil.rmtree(building, ignore_errors=True)
        building.mkdir(parents=True, exist_ok=True)

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
                "WINEDLLOVERRIDES": env.get("WINEDLLOVERRIDES", "winemenubuilder.exe=d"),
            }
        )

        log_path = self.data_dir / "wine-base-prefix.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        command = [self.wineboot, "-i"] if self.wineboot else [self.wine, "wineboot", "-i"]

        with log_path.open("ab", buffering=0) as log:
            log.write(f"\n[ripo] bootstrapping Rec Room base prefix with: {' '.join(command)}\n".encode())
            try:
                completed = subprocess.run(
                    command,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=120,
                    check=False,
                )
                log.write(f"[ripo] wineboot exit={completed.returncode}\n".encode())
            except subprocess.TimeoutExpired:
                log.write(b"[ripo] wineboot --init timed out; waiting for wineserver completion.\n")

            # wineboot can return before its child processes have finished
            # writing system.reg/user.reg. Wait for that work instead of
            # declaring a good prefix broken immediately.
            if self.wineserver:
                try:
                    completed = subprocess.run(
                        [self.wineserver, "-w"],
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=45,
                        check=False,
                    )
                    log.write(f"[ripo] wineserver -w exit={completed.returncode}\n".encode())
                except subprocess.TimeoutExpired:
                    log.write(b"[ripo] wineserver -w timed out; checking registry files anyway.\n")

        deadline = time.time() + 25
        while time.time() < deadline and not (building / "system.reg").is_file():
            time.sleep(1)

        if not (building / "system.reg").is_file():
            # Some Debian/Ubuntu Wine wrappers behave better when wineboot is
            # invoked through wine itself. Give that path one clean retry.
            retry = [self.wine, "wineboot", "-i"]
            with log_path.open("ab", buffering=0) as log:
                log.write(f"[ripo] retrying Rec Room base prefix with: {' '.join(retry)}\n".encode())
                try:
                    subprocess.run(
                        retry,
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=120,
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    log.write(b"[ripo] wine wineboot --init retry timed out.\n")
                if self.wineserver:
                    try:
                        subprocess.run(
                            [self.wineserver, "-w"],
                            env=env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                            timeout=45,
                            check=False,
                        )
                    except subprocess.TimeoutExpired:
                        pass

            deadline = time.time() + 20
            while time.time() < deadline and not (building / "system.reg").is_file():
                time.sleep(1)

        if not (building / "system.reg").is_file():
            tail = ""
            try:
                tail = log_path.read_text(errors="replace")[-2200:]
            except Exception:
                pass
            files: list[str] = []
            try:
                files = sorted(path.name for path in building.iterdir())[:50]
            except Exception:
                pass
            raise RuntimeError(
                "Wine could not finish creating the shared 64-bit Rec Room prefix. "
                f"Prefix files: {files}."
                + (f" wineboot: {tail}" if tail else "")
            )

        # Shut down only this prefix's wineserver before cloning its filesystem.
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
            except subprocess.TimeoutExpired:
                pass

        shutil.rmtree(self.base_prefix, ignore_errors=True)
        building.replace(self.base_prefix)


RecRoomWinePool._ensure_base_prefix = _ensure_base_prefix

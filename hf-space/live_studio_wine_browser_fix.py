from __future__ import annotations

import subprocess
import time

from live_studio_wine import LiveStudioWine

_OLD_WORKER = LiveStudioWine._worker


def _worker(self: LiveStudioWine) -> None:
    try:
        if not self.connector.status().get("browser_running"):
            self.phase = "starting-server-browser"
            self.connector._write_profile_prefs()
            self.connector.browser = subprocess.Popen(
                [
                    self.connector._firefox(),
                    "--no-remote",
                    "--profile",
                    str(self.connector.profile_dir),
                    "--new-window",
                    self.DOWNLOAD_PAGE,
                ],
                env=self.connector._env(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            deadline = time.time() + 15
            while time.time() < deadline:
                if self.connector.status().get("browser_running"):
                    break
                time.sleep(0.5)
            if not self.connector.status().get("browser_running"):
                raise RuntimeError("Server Firefox could not start for the LIVE Studio download.")
        _OLD_WORKER(self)
    except Exception as exc:
        self.phase = "wine-failed"
        self.last_error = str(exc)[:3500]
        probe = self._vm_probe()
        if probe.get("kvm_access"):
            self.last_note = "Wine failed. KVM is available for a Windows VM fallback."
        else:
            self.last_note = "Wine failed. This Space does not expose KVM, so a Windows VM here would be slow software emulation."


LiveStudioWine._worker = _worker

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from live_studio_wine import LiveStudioWine

_UPDATE_URL = "https://tron-sg.bytelemon.com/api/sdk/check_update?pid=7393277106664249610&uid=&branch=studio/release/stable&buildId="
_ALLOWED_PREFIX = "https://www.tiktok.com/tos-live-studio/releases/"


def _resolve_package(self: LiveStudioWine) -> tuple[str, str, str]:
    self.phase = "resolving-live-studio"
    try:
        with httpx.Client(follow_redirects=True, timeout=30.0, headers={"User-Agent": "TikTok LIVE Studio"}) as client:
            response = client.get(_UPDATE_URL)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        raise RuntimeError(f"TikTok LIVE Studio package resolver failed: {exc}") from exc

    try:
        win32 = payload["data"]["manifest"]["win32"]
        version = str(win32.get("version") or "").strip()
        expected_md5 = str(((win32.get("extra") or {}).get("downloaderConfig") or {}).get("md5") or "").strip().lower()
        candidates: list[str] = []
        preferred = str(((win32.get("extra") or {}).get("downloaderConfig") or {}).get("url") or "").strip()
        if preferred:
            candidates.append(preferred)
        for row in win32.get("urls") or []:
            url = str(((row or {}).get("path") or {}).get("x64") or "").strip()
            if url:
                candidates.append(url)
    except Exception as exc:
        raise RuntimeError("TikTok LIVE Studio package resolver returned an unexpected manifest.") from exc

    package_url = next((u for u in candidates if u.startswith(_ALLOWED_PREFIX) and u.lower().endswith(".exe")), "")
    if not package_url:
        raise RuntimeError("TikTok resolver did not return an allowed www.tiktok.com LIVE Studio x64 package URL.")
    parsed = urlparse(package_url)
    if parsed.scheme != "https" or parsed.hostname != "www.tiktok.com" or "/tos-live-studio/releases/" not in parsed.path:
        raise RuntimeError("TikTok LIVE Studio package URL failed the server safety allowlist.")
    return package_url, version, expected_md5


def _download_package(self: LiveStudioWine, package_url: str, version: str, expected_md5: str) -> Path:
    self.phase = "downloading-live-studio"
    safe_version = "".join(ch for ch in version if ch.isalnum() or ch in ".-_") or "current"
    destination = self.downloads / f"tiktok-live-studio-{safe_version}-x64.exe"
    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        partial.unlink(missing_ok=True)
    except Exception:
        pass

    digest = hashlib.md5()  # TikTok's updater manifest publishes MD5 for package integrity.
    total = 0
    try:
        with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0), headers={"User-Agent": "TikTok LIVE Studio"}) as client:
            with client.stream("GET", package_url) as response:
                response.raise_for_status()
                final = response.url
                if final.host not in {"www.tiktok.com", "lf16-live-studio.tiktokcdn.com"}:
                    raise RuntimeError(f"TikTok package redirected to an unexpected host: {final.host}")
                with partial.open("wb") as handle:
                    for chunk in response.iter_bytes(1024 * 1024):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        digest.update(chunk)
                        total += len(chunk)
                        if total > 2_500_000_000:
                            raise RuntimeError("TikTok LIVE Studio package exceeded the 2.5 GB safety limit.")
    except Exception as exc:
        try:
            partial.unlink(missing_ok=True)
        except Exception:
            pass
        raise RuntimeError(f"Downloading TikTok LIVE Studio failed: {exc}") from exc

    if total < 5_000_000:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"TikTok LIVE Studio download was unexpectedly small ({total} bytes).")
    actual_md5 = digest.hexdigest().lower()
    if expected_md5 and actual_md5 != expected_md5:
        partial.unlink(missing_ok=True)
        raise RuntimeError("TikTok LIVE Studio package checksum did not match TikTok's update manifest.")
    partial.replace(destination)
    self.installer = str(destination)
    self.last_note = f"Downloaded TikTok LIVE Studio {version or 'current'} directly from TikTok and verified the package checksum."
    return destination


def _rank_extracted_exe(path: Path) -> int:
    name = path.name.lower()
    rel = path.as_posix().lower()
    bad = (
        "uninstall", "unins", "setup", "installer", "update", "updater", "crash", "dump",
        "helper", "elevat", "inject", "vc_redist", "dxsetup", "cefclient", "notification_helper",
    )
    if any(word in name for word in bad):
        return -10000
    score = 0
    if name == "tiktok live studio launcher.exe":
        score += 1000
    elif name == "tiktok live studio.exe":
        score += 950
    elif "tiktok" in name and "live" in name and "studio" in name:
        score += 700
    elif "livestudio" in name:
        score += 550
    if "win32-x64" in rel or "x64" in rel:
        score += 120
    if "resources" not in rel:
        score += 20
    try:
        size = path.stat().st_size
        if size > 5_000_000:
            score += 30
    except OSError:
        pass
    return score


def _extract_package(self: LiveStudioWine, installer: Path, version: str) -> Path:
    seven = shutil.which("7z") or shutil.which("7zz")
    if not seven:
        raise RuntimeError("7-Zip is not installed on the Space, so the TikTok x64 package cannot be extracted without its 32-bit bootstrapper.")

    safe_version = "".join(ch for ch in version if ch.isalnum() or ch in ".-_") or "current"
    root = self.data_dir / f"extracted-live-studio-{safe_version}"
    if root.exists():
        try:
            shutil.rmtree(root)
        except Exception as exc:
            raise RuntimeError(f"Could not reset LIVE Studio extraction directory: {exc}") from exc
    root.mkdir(parents=True, exist_ok=True)

    self.phase = "extracting-live-studio"
    extract_log = self.data_dir / "live-studio-extract.log"
    try:
        with extract_log.open("wb") as log:
            proc = subprocess.run(
                [seven, "x", "-y", f"-o{root}", str(installer)],
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=240,
                check=False,
            )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Extracting TikTok LIVE Studio exceeded four minutes.") from exc

    if proc.returncode not in {0, 1}:
        tail = ""
        try:
            tail = extract_log.read_text(errors="replace")[-2500:]
        except Exception:
            pass
        raise RuntimeError(f"7-Zip could not extract TikTok LIVE Studio (exit {proc.returncode})." + ((" Log: " + tail) if tail else ""))

    choices: list[tuple[int, int, Path]] = []
    sample: list[str] = []
    try:
        for path in root.rglob("*.exe"):
            try:
                rel = path.relative_to(root).as_posix()
                sample.append(rel)
                score = _rank_extracted_exe(path)
                size = path.stat().st_size
                if score > 0:
                    choices.append((score, size, path))
            except Exception:
                continue
    except Exception:
        pass

    choices.sort(key=lambda row: (row[0], row[1]), reverse=True)
    if not choices:
        listed = ", ".join(sample[:35]) or "no .exe files"
        raise RuntimeError(f"TikTok package extracted, but no LIVE Studio application executable was found. Extracted EXEs: {listed}")

    chosen = choices[0][2]
    self.installed_exe = str(chosen)
    self.last_note = f"Extracted TikTok LIVE Studio {version or 'current'} directly and selected {chosen.name}, bypassing the 32-bit installer bootstrapper."
    return chosen


def _worker(self: LiveStudioWine) -> None:
    try:
        wine = self._wine()
        if not wine:
            raise RuntimeError("Wine is not installed on the Space.")

        self._init_prefix()
        exes = self._candidate_exes()
        if exes:
            self.installed_exe = str(exes[0])
        else:
            package_url, version, expected_md5 = _resolve_package(self)
            installer = _download_package(self, package_url, version, expected_md5)
            _extract_package(self, installer, version)

        if not self.installed_exe:
            raise RuntimeError("LIVE Studio executable is missing after package preparation.")

        self.phase = "launching-live-studio"
        self.logs.parent.mkdir(parents=True, exist_ok=True)
        log = self.logs.open("ab", buffering=0)
        self.process = subprocess.Popen(
            [wine, self.installed_exe],
            env=self._env(),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        time.sleep(30)
        if self.process.poll() is not None and not self._has_live_studio_window():
            tail = ""
            try:
                tail = self.logs.read_text(errors="replace")[-3500:]
            except Exception:
                pass
            raise RuntimeError("Extracted TikTok LIVE Studio exited under Wine before opening a usable window." + ((" Log: " + tail) if tail else ""))
        self.phase = "running-wine"
        self.last_error = ""
        self.last_note = "TikTok LIVE Studio is running on the Linux server through 64-bit Wine from the verified extracted package."
    except Exception as exc:
        self.phase = "wine-failed"
        self.last_error = str(exc)[:6000]
        probe = self._vm_probe()
        if probe.get("kvm_access"):
            self.last_note = "Wine failed. KVM is available for a Windows VM fallback."
        else:
            self.last_note = "Wine failed. This Hugging Face Space does not expose KVM; a Windows VM here would use slow software emulation."


LiveStudioWine._resolve_package = _resolve_package
LiveStudioWine._download_package = _download_package
LiveStudioWine._extract_package = _extract_package
LiveStudioWine._worker = _worker

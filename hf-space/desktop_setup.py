from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import psutil


def _write(path: Path, content: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def configure_full_desktop(data_dir: Path, home: Path) -> None:
    """Create a normal, computer-like desktop before the X session starts."""
    desktop_dir = home / "Desktop"
    desktop_dir.mkdir(parents=True, exist_ok=True)

    wallpaper = data_dir / "ripo-team-wallpaper.svg"
    _write(
        wallpaper,
        """<svg xmlns="http://www.w3.org/2000/svg" width="1366" height="768" viewBox="0 0 1366 768">
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#07101f"/>
    <stop offset="0.48" stop-color="#172d65"/>
    <stop offset="1" stop-color="#090b17"/>
  </linearGradient>
  <radialGradient id="glow" cx="0.28" cy="0.22" r="0.72">
    <stop offset="0" stop-color="#6d8dff" stop-opacity="0.42"/>
    <stop offset="1" stop-color="#6d8dff" stop-opacity="0"/>
  </radialGradient>
</defs>
<rect width="1366" height="768" fill="url(#bg)"/>
<rect width="1366" height="768" fill="url(#glow)"/>
<g fill="none" stroke="#a9bbff" stroke-opacity="0.10">
  <circle cx="1080" cy="155" r="260"/><circle cx="1080" cy="155" r="210"/><circle cx="1080" cy="155" r="160"/>
</g>
<text x="80" y="620" fill="#f5f7ff" font-family="DejaVu Sans, sans-serif" font-size="64" font-weight="700">Ripo Team</text>
<text x="84" y="665" fill="#b8c5ef" font-family="DejaVu Sans, sans-serif" font-size="25">Cloud Linux Desktop</text>
</svg>\n""",
    )

    pcmanfm_config = home / ".config/pcmanfm/LXDE/desktop-items-0.conf"
    _write(
        pcmanfm_config,
        f"""[*]
wallpaper_mode=fit
wallpaper_common=1
wallpaper={wallpaper}
desktop_bg=#0b1020
desktop_fg=#ffffff
desktop_shadow=#000000
show_wm_menu=0
sort=mtime;ascending;
show_documents=0
show_trash=1
show_mounts=1
""",
    )

    launchers = {
        "Files.desktop": (
            "Files",
            "Open your Linux files",
            "pcmanfm",
            "system-file-manager",
        ),
        "Browser.desktop": (
            "Web Browser",
            "Browse the web with Firefox",
            "firefox-esr --no-remote --new-window https://www.google.com",
            "firefox-esr",
        ),
        "Terminal.desktop": (
            "Terminal",
            "Open the Linux terminal",
            "lxterminal",
            "utilities-terminal",
        ),
        "System-Info.desktop": (
            "System Info",
            "View the container resource limits",
            "lxterminal -e bash -lc 'echo Ripo Team Cloud Linux; echo; echo CPU:; nproc; echo; echo Memory:; free -h; echo; echo Disk view:; df -h /; echo; exec bash'",
            "computer",
        ),
    }
    for filename, (name, comment, command, icon) in launchers.items():
        _write(
            desktop_dir / filename,
            f"""[Desktop Entry]
Version=1.0
Type=Application
Name={name}
Comment={comment}
Exec={command}
Icon={icon}
Terminal=false
StartupNotify=true
""",
            executable=True,
        )

    # Ensure the panel and desktop manager start on the minimal image.
    _write(
        home / ".config/lxsession/LXDE/autostart",
        """@lxpanel --profile LXDE
@pcmanfm --desktop --profile LXDE
""",
    )


def _read_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _finite_limit(raw: str | None) -> int | None:
    if not raw or raw == "max":
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    # cgroup v1 sometimes exposes an enormous sentinel instead of "max".
    if value <= 0 or value >= (1 << 60):
        return None
    return value


def _memory_limits() -> tuple[int, int, str]:
    host = psutil.virtual_memory()
    limit = _finite_limit(_read_text("/sys/fs/cgroup/memory.max"))
    current = _finite_limit(_read_text("/sys/fs/cgroup/memory.current"))

    if limit is None:
        limit = _finite_limit(_read_text("/sys/fs/cgroup/memory/memory.limit_in_bytes"))
        current = _finite_limit(_read_text("/sys/fs/cgroup/memory/memory.usage_in_bytes"))

    if limit is not None and limit < host.total:
        used = current if current is not None else 0
        return limit, max(0, limit - used), "container-cgroup"
    return host.total, host.available, "host-visible"


def _cpu_limit() -> tuple[float, str]:
    host_count = float(psutil.cpu_count() or 1)
    raw = _read_text("/sys/fs/cgroup/cpu.max")
    if raw:
        parts = raw.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                quota, period = int(parts[0]), int(parts[1])
                if quota > 0 and period > 0:
                    return max(0.1, min(host_count, quota / period)), "container-cgroup"
            except ValueError:
                pass

    quota = _finite_limit(_read_text("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"))
    period = _finite_limit(_read_text("/sys/fs/cgroup/cpu/cpu.cfs_period_us"))
    if quota and period:
        return max(0.1, min(host_count, quota / period)), "container-cgroup"
    return host_count, "host-visible"


def detected_resources() -> dict[str, Any]:
    memory_total, memory_available, memory_source = _memory_limits()
    cpu_count, cpu_source = _cpu_limit()
    rounded_cpu: int | float = (
        int(cpu_count)
        if math.isclose(cpu_count, round(cpu_count), abs_tol=0.01)
        else round(cpu_count, 2)
    )
    return {
        "cpu_count": rounded_cpu,
        "cpu_source": cpu_source,
        "memory_total": memory_total,
        "memory_available": memory_available,
        "memory_source": memory_source,
        # statvfs/psutil sees the shared host filesystem, not the user's quota.
        "disk_total": None,
        "disk_free": None,
        "disk_note": "Ephemeral Hugging Face Space storage; the shared host filesystem size is not your personal disk quota.",
    }

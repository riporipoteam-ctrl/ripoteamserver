from __future__ import annotations

from pathlib import Path

from live_studio_wine import LiveStudioWine


def _candidate_exes(self: LiveStudioWine) -> list[Path]:
    drive = self.prefix / "drive_c"
    if not drive.exists():
        return []

    rows: list[tuple[int, int, Path]] = []
    try:
        for p in drive.rglob("*.exe"):
            try:
                rel = p.relative_to(drive).as_posix().lower()
                name = p.name.lower()
                size = p.stat().st_size
            except Exception:
                continue

            # Never treat Wine/Windows system utilities or installer helpers as
            # TikTok LIVE Studio merely because the outer prefix directory has
            # "tiktok-live-studio" in its name.
            if rel.startswith("windows/") or any(
                bad in name
                for bad in (
                    "winedbg",
                    "wineboot",
                    "explorer.exe",
                    "regedit.exe",
                    "rundll32.exe",
                    "unins",
                    "uninstall",
                    "setup",
                    "installer",
                    "update",
                    "crash",
                )
            ):
                continue

            score = 0
            if "tiktok" in name:
                score += 120
            if "live studio" in name or "livestudio" in name:
                score += 120
            if "tiktok" in rel:
                score += 60
            if "live studio" in rel or "livestudio" in rel:
                score += 60
            if "program files" in rel:
                score += 10

            if score < 100 or size < 100_000:
                continue
            rows.append((score, size, p))
    except Exception:
        pass

    rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return [row[2] for row in rows]


LiveStudioWine._candidate_exes = _candidate_exes

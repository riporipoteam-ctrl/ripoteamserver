from __future__ import annotations

from pathlib import Path

from live_studio_wine import LiveStudioWine


def _find_downloaded_installer(self: LiveStudioWine, after: float) -> Path | None:
    roots = [
        self.downloads,
        Path.home() / "Downloads",
        getattr(self.connector, "download_dir", self.connector.profile_dir / "downloads"),
        self.connector.profile_dir / "downloads",
    ]
    choices: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        root = Path(root)
        marker = str(root)
        if marker in seen or not root.exists():
            continue
        seen.add(marker)
        try:
            for p in root.rglob("*.exe"):
                try:
                    stat = p.stat()
                    if stat.st_mtime >= after - 3 and stat.st_size > 1_000_000:
                        choices.append(p)
                except OSError:
                    pass
        except Exception:
            pass
    if not choices:
        return None
    choices.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return choices[0]


LiveStudioWine._find_downloaded_installer = _find_downloaded_installer

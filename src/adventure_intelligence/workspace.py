"""Workspace layout; persistent state lives outside the git checkout."""
from __future__ import annotations
from pathlib import Path
import os
import sys


def root_at(value: str | Path | None) -> Path:
    candidate = Path(value or os.environ.get("ADVENTURE_HOME", "~/AdventureAI")).expanduser()
    # Avoid silently writing onto the system drive under /Volumes when an SSD is absent.
    if sys.platform == "darwin" and len(candidate.parts) >= 4 and candidate.parts[:2] == ("/", "Volumes"):
        mount = Path("/Volumes") / candidate.parts[2]
        if not mount.is_mount():
            raise ValueError(f"External workspace volume is not mounted: {mount}")
    return candidate.resolve()


def init(root: Path) -> Path:
    for name in ("originals", "sidecars", "staging", "features", "reels", "exports", "logs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def asset_path(root: Path, digest: str, suffix: str) -> Path:
    return root / "originals" / digest[:2] / f"{digest}{suffix.lower()}"

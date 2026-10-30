"""Prevent user-selected output artifacts from being written into source libraries.

This is defense in depth: all processing already reads originals without altering
them, and the workspace must be disjoint from the indexed source. The guard also
protects unindexed videos and other media files sharing a source directory.
"""
from __future__ import annotations

from pathlib import Path


def protect_source_output(db, output: Path) -> Path:
    """Validate an output against indexed input directories before any writes.

    Both sides are resolved so symlink aliases cannot escape the safeguard.
    The registered source parent is conservatively treated as read-only.
    """
    target = Path(output).expanduser().resolve()
    if target.is_dir():
        raise ValueError("Output must be a file, not a directory")
    paths = db.execute("SELECT original_path FROM imports").fetchall()
    for row in paths:
        source = Path(row[0]).expanduser().resolve()
        if target == source or target.is_relative_to(source.parent):
            raise ValueError(
                "Output cannot overwrite a source video or write inside an indexed source-media directory. "
                "Save generated files in ~/AdventureAI/exports or ~/AdventureAI/reels."
            )
    return target

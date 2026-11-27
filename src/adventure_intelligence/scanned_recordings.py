"""Every recording a discovery pass scanned, found the way the review app finds them.

A discovery folder's authority is experiment.json (the frozen list of recordings) and each
recording's source.json (verified identity: sport, duration, size). The path and file name
come from the folder's sources.json / judge-sources.json when the batch wrote one, else from
the catalog: folders run from a sources file kept elsewhere carry no path of their own.
First folder wins, as in the review app. Read-only.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3


def _read(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def catalog(workspace: Path) -> dict[str, dict]:
    """sha -> path, size and duration of each imported original."""
    db_path = Path(workspace) / "catalog.sqlite3"
    if db_path.is_symlink() or not db_path.is_file():
        return {}
    db = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("PRAGMA query_only=ON")
        return {sha: {"path": path, "sizeBytes": size,
                      "durationMs": round(duration * 1000) if duration else None}
                for sha, path, size, duration in
                db.execute("SELECT sha256,stored_path,size_bytes,duration FROM assets")}
    finally:
        db.close()


def proxies(record: Path) -> list[tuple[int, int, Path]]:
    out = []
    for p in sorted((record / "proxies").glob("*.mp4")) if (record / "proxies").is_dir() else []:
        try:
            a, b = (int(x) for x in p.stem.split("-"))
        except ValueError:
            continue
        out.append((a, b, p))
    return out


def scanned(reports: Path, cat: dict[str, dict] | None = None) -> dict[str, dict]:
    """sha -> {path, file, sport, folder, record, source, proxies, pathFrom}."""
    reports = Path(reports)
    cat = catalog(reports.parent) if cat is None else cat
    out: dict[str, dict] = {}
    for folder in sorted(reports.glob("discovery-*")):
        frozen = _read(folder / "experiment.json")
        rows = {}
        for name in ("sources.json", "judge-sources.json"):   # relabels override
            for row in _read(folder / name) or []:
                if isinstance(row, dict) and row.get("sourceSha256"):
                    rows[row["sourceSha256"]] = {**rows.get(row["sourceSha256"], {}), **row}
        listed = (frozen or {}).get("sources") or list(rows)
        for sha in listed:
            if sha in out:
                continue
            source = _read(folder / sha / "source.json")
            if source is None:
                continue        # never verified: absent, not negative
            row = rows.get(sha, {})
            path, origin = row.get("path"), "batch"
            if not path and sha in cat:
                path, origin = cat[sha]["path"], "catalog"
            out[sha] = {"path": path, "file": Path(path).name if path else None,
                        "pathFrom": origin if path else None,
                        "sport": row.get("sport") or source.get("sport"),
                        "folder": folder, "record": folder / sha, "source": source,
                        "proxies": proxies(folder / sha)}
    return out

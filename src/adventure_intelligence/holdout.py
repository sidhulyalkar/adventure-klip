"""A frozen held-out test set: capture days nobody tunes on.

Thresholds, prompts, windows, examples and judge weights are adjusted on development days
only. Held-out days are scored once a method is settled, so a claim about them is not
inflated by tuning on the same footage.

Unit: one capture day (from the DJI file name) of one sport. Days of the same trip are
correlated; trip-level hold-out is what `evaluate` does on top of this. The file is
REPORTS/holdout-v1.json, written once (by the owner) and replaced only with a reason, the
earlier version kept under REPORTS/holdout-history/.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re

VERSION = "klip-holdout-v1"
FILE = "holdout-v1.json"
_DAY = re.compile(r"DJI_(\d{4})(\d{2})(\d{2})")


def day_of(name: str) -> str | None:
    m = _DAY.search(name or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def inventory(reports: Path) -> dict[tuple[str, str], dict]:
    """(day, sport) -> recordings and review-segment count, across discovery folders."""
    out: dict[tuple[str, str], dict] = defaultdict(lambda: {"recordings": set(), "reviewSegments": 0,
                                                            "folders": set()})
    for folder in sorted(Path(reports).glob("discovery-*")):
        rows = _read(folder / "judge-sources.json") or _read(folder / "sources.json") or []
        for row in rows:
            day = day_of(Path(row["path"]).name)
            if day is None or row["sport"] in ("unknown",):
                continue
            plan = _read(folder / row["sourceSha256"] / "segments.json") or {"segments": []}
            unit = out[(day, row["sport"])]
            unit["recordings"].add(row["sourceSha256"])
            unit["folders"].add(folder.name)
            unit["reviewSegments"] += sum(s["role"] != "not_reviewed" for s in plan["segments"])
    return dict(out)


def propose(reports: Path, *, sports=("ski", "snorkeling"), min_segments: int = 8,
            target_share: float = 0.2, avoid_folders=("discovery-011",)) -> list[dict]:
    """For each sport, the day whose share of that sport's review segments is closest to
    target_share (enough to test on, without starving development), with at least
    min_segments, avoiding days already used in pilots. A proposal only; nothing is written."""
    inv = inventory(reports)
    picks = []
    for sport in sports:
        total = sum(u["reviewSegments"] for (d, sp), u in inv.items() if sp == sport)
        days = [(d, u) for (d, sp), u in inv.items()
                if sp == sport and u["reviewSegments"] >= min_segments
                and not (u["folders"] & set(avoid_folders))]
        if not days or not total:
            continue
        day, unit = min(days, key=lambda x: (abs(x[1]["reviewSegments"] / total - target_share),
                                             x[0]))
        picks.append({"day": day, "sport": sport, "recordings": len(unit["recordings"]),
                      "reviewSegments": unit["reviewSegments"],
                      "shareOfSport": round(unit["reviewSegments"] / total, 3)})
    return picks


def load(reports: Path) -> dict | None:
    return _read(Path(reports) / FILE)


def held_out(reports: Path) -> set[str]:
    """Source SHA-256 of every held-out recording (empty when nothing is frozen)."""
    doc = load(reports)
    return set(doc["recordings"]) if doc else set()


def freeze(reports: Path, units: list[tuple[str, str]], *, reason: str | None = None,
           replace: bool = False) -> dict:
    reports = Path(reports)
    if not units:
        raise ValueError("Name at least one day:sport to hold out")
    current = load(reports)
    if current and not replace:
        raise ValueError(f"A held-out set is already frozen ({FILE}); replacing it needs "
                         "--replace and a --reason")
    if current and replace and not reason:
        raise ValueError("Replacing the held-out set needs a --reason")
    inv = inventory(reports)
    chosen, shas = [], set()
    for day, sport in units:
        unit = inv.get((day, sport))
        if unit is None:
            raise ValueError(f"No indexed {sport} recordings on {day}")
        chosen.append({"day": day, "sport": sport, "recordings": sorted(unit["recordings"]),
                       "reviewSegments": unit["reviewSegments"]})
        shas |= unit["recordings"]
    if current:
        history = reports / "holdout-history"
        history.mkdir(exist_ok=True)
        n = len(list(history.glob("holdout-*.json"))) + 1
        (history / f"holdout-{n:03d}.json").write_text(json.dumps(current, indent=2),
                                                        encoding="utf-8")
    doc = {"version": VERSION, "frozenAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "frozenBy": "owner", "units": chosen, "recordings": sorted(shas),
           "replaces": current["frozenAt"] if current else None, "reason": reason,
           "rule": "Never tune, label for tuning, or pick examples from these recordings. "
                   "Score them only for a settled method."}
    tmp = reports / (FILE + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    tmp.replace(reports / FILE)
    return doc


def exclude(items, reports: Path, key=lambda x: x["sourceSha256"]) -> tuple[list, int]:
    """(items not held out, how many were removed): for decks, examples and tuning."""
    banned = held_out(reports)
    kept = [x for x in items if key(x) not in banned]
    return kept, len(items) - len(kept)

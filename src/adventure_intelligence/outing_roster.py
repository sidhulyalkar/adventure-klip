"""Outing roster: which recordings belong to the same day out.

Every held-out comparison in Klip splits by outing, so the roster has to be
right, and only the owner knows it. `propose` groups recordings by gaps in
capture time as a starting point. The result stays provisional until the owner
checks it, fixes merges and splits, and sets "status" to "owner_confirmed".
Camera clocks can be wrong; a proposed roster is never evidence of independence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

VERSION = "klip-outing-roster-v1"
STATUSES = ("provisional", "owner_confirmed")
SPORTS = ("ski", "snorkeling", "bike", "mixed")
_HEX = set("0123456789abcdef")


def _clock(value) -> tuple[datetime | None, bool]:
    """(naive time, had a zone). Aware times are converted to UTC before the zone is dropped."""
    if not isinstance(value, str) or not value:
        return None, False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    if parsed.tzinfo is None:
        return parsed, False
    return parsed.astimezone(timezone.utc).replace(tzinfo=None), True


def catalog_rows(workspace: Path) -> list[dict]:
    """Read recording identity and capture time. The catalog is opened read-only."""
    database = Path(workspace) / "catalog.sqlite3"
    if database.is_symlink() or not database.is_file():
        raise ValueError("No catalog in this workspace")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        return [{"sourceSha256": r[0], "captureTime": r[1], "durationSeconds": r[2],
                 "activity": r[3]}
                for r in db.execute("SELECT id,capture_time,duration,activity FROM assets "
                                    "WHERE duration>0 ORDER BY id")]


def propose(rows: list[dict], *, gap_hours: float = 6.0) -> dict:
    """Start a new outing whenever consecutive recordings are more than gap_hours apart."""
    if not 0 < gap_hours <= 72:
        raise ValueError("gap_hours must be in (0, 72]")
    dated, undated, zones = [], [], set()
    for row in rows:
        when, zoned = _clock(row.get("captureTime"))
        if when is None:
            undated.append(row)
        else:
            dated.append((when, row))
            zones.add(zoned)
    dated.sort(key=lambda pair: (pair[0], pair[1]["sourceSha256"]))
    outings: list[dict] = []
    previous_end = None
    per_day: dict[str, int] = {}
    for when, row in dated:
        if previous_end is None or (when - previous_end).total_seconds() > gap_hours * 3600:
            day = when.date().isoformat()
            per_day[day] = per_day.get(day, 0) + 1
            outings.append({"outing": f"{day}-{per_day[day]}", "sport": None, "sources": []})
        outings[-1]["sources"].append(row)
        previous_end = when
    # An undated recording gets its own outing: grouping it would be a guess,
    # and leaving it alone can only make a held-out split look harder, not easier.
    outings += [{"outing": f"undated-{row['sourceSha256'][:8]}", "sport": None, "sources": [row]}
                for row in sorted(undated, key=lambda r: r["sourceSha256"])]
    for outing in outings:
        # The catalog's tag when every tagged recording agrees; otherwise left for the owner.
        tags = {r.get("activity") for r in outing["sources"]} & {"ski", "snorkeling", "bike"}
        outing["sport"] = next(iter(tags)) if len(tags) == 1 else None
        outing["recordings"] = len(outing["sources"])
    return {
        "version": VERSION, "status": "provisional", "gapHours": gap_hours,
        "outings": outings,
        "undatedSources": len(undated),
        "mixedClockBases": len(zones) > 1,
        "ownerAction": "Check every outing, merge or split as needed, set each sport "
                       "(ski, snorkeling, bike or mixed), then set status to owner_confirmed.",
    }


def validate(doc: dict) -> dict:
    if (not isinstance(doc, dict) or doc.get("version") != VERSION
            or doc.get("status") not in STATUSES or not isinstance(doc.get("outings"), list)):
        raise ValueError("Not an outing roster")
    seen_sources, seen_names = set(), set()
    for outing in doc["outings"]:
        name = outing.get("outing") if isinstance(outing, dict) else None
        if (not isinstance(name, str) or not 1 <= len(name) <= 80 or name in seen_names
                or name.startswith("source:") or not isinstance(outing.get("sources"), list)
                or not outing["sources"]):
            raise ValueError("Each outing needs a unique name and at least one source")
        seen_names.add(name)
        if outing.get("sport") not in (None, *SPORTS):
            raise ValueError(f"Outing {name}: sport must be one of {', '.join(SPORTS)} or null")
        for row in outing["sources"]:
            sha = row.get("sourceSha256") if isinstance(row, dict) else None
            if not isinstance(sha, str) or len(sha) != 64 or not set(sha) <= _HEX:
                raise ValueError("Roster sources need a full SHA-256")
            if sha in seen_sources:
                raise ValueError("A recording appears in two outings")
            seen_sources.add(sha)
    return doc


def outing_by_source(doc: dict) -> dict[str, str]:
    """{source SHA: outing}, the mapping evidence_ledger.with_outings expects."""
    return {row["sourceSha256"]: outing["outing"]
            for outing in validate(doc)["outings"] for row in outing["sources"]}


def sport_by_source(doc: dict) -> dict[str, str]:
    """{source SHA: sport} from outings the owner tagged with one sport ('mixed' gives none)."""
    return {row["sourceSha256"]: outing["sport"]
            for outing in validate(doc)["outings"] if outing.get("sport") in SPORTS[:3]
            for row in outing["sources"]}


def group_of(mapping: dict[str, str], source_sha: str) -> str:
    """Held-out group for a recording. Without an outing it stands alone as its own group."""
    return mapping.get(source_sha) or f"source:{source_sha[:12]}"

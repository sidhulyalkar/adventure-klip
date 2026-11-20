"""Outing roster: trips, the days (outings) inside them, and each recording's activity.

Two levels, two jobs:
- a day (an "outing") is what gets sorted: the best moments of each day;
- a trip is what a model is tested on: it is always scored on a whole trip it
  never trained on, because days of one trip share places and people.

`propose` groups by gaps in capture time: a new day after a gap of more than
gap_hours, a new trip after more than trip_gap_hours. Gaps, not calendar dates,
so a camera clock set to the wrong time zone does not split a day at midnight.
Each recording carries its own activity; "accidental" recordings stay in their
day but are left out of review, ranking and evaluation. Everything stays
provisional until the owner confirms it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3

VERSION = "klip-outing-roster-v1"
STATUSES = ("provisional", "owner_confirmed")
SPORTS = ("ski", "snorkeling", "bike", "mixed")  # legacy outing-level tag
ACTIVITIES = ("ski", "snorkeling", "kayak", "bike", "hike", "surf", "other", "accidental")
UNLABELLED = "unknown"
EXCLUDED = "accidental"
JUDGED_SPORTS = ("ski", "snorkeling", "bike")  # activities the judge panel has questions for
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


def _activity(value) -> str:
    return value if value in ACTIVITIES else UNLABELLED


def propose(rows: list[dict], *, gap_hours: float = 6.0, trip_gap_hours: float = 96.0) -> dict:
    """New day after gap_hours without a recording; new trip after trip_gap_hours
    (4 days by default: trips have rest days without filming)."""
    if not 0 < gap_hours <= 72 or not gap_hours <= trip_gap_hours <= 24 * 60:
        raise ValueError("Need 0 < gap_hours <= 72 and gap_hours <= trip_gap_hours")
    rows = [{**r, "activity": _activity(r.get("activity"))} for r in rows]
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
    doc = {
        "version": VERSION, "status": "provisional", "gapHours": gap_hours,
        "tripGapHours": trip_gap_hours, "outings": outings,
        "undatedSources": len(undated),
        "mixedClockBases": len(zones) > 1,
        "ownerAction": "Check trips and days, merge or split as needed, set each "
                       "recording's activity, then confirm.",
    }
    return with_trips(doc)


def _start(outing: dict) -> datetime | None:
    times = [t for t in (_clock(r.get("captureTime"))[0] for r in outing["sources"]) if t]
    return min(times) if times else None


def _end(outing: dict) -> datetime | None:
    times = [t for t in (_clock(r.get("captureTime"))[0] for r in outing["sources"]) if t]
    return max(times) if times else None


def with_trips(doc: dict) -> dict:
    """Give every outing without a trip one, by the trip gap rule. Owner choices are kept."""
    gap = float(doc.get("tripGapHours") or 96.0)
    outings = doc["outings"]
    order = sorted(range(len(outings)), key=lambda k: (_start(outings[k]) is None,
                                                       _start(outings[k]) or datetime.min, k))
    trip, previous_end = None, None
    for k in order:
        o = outings[k]
        start = _start(o)
        if start is None:
            o.setdefault("trip", f"trip-{o['outing']}")
            continue
        if trip is None or previous_end is None or (start - previous_end).total_seconds() > gap * 3600:
            trip = f"trip-{start.date().isoformat()}"
        o.setdefault("trip", trip)
        if o["trip"] == trip:
            previous_end = max(previous_end or start, _end(o) or start)
        o.setdefault("place", None)
        for row in o["sources"]:
            row["activity"] = _activity(row.get("activity"))
    doc.setdefault("tripGapHours", gap)
    return doc


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
        trip, place = outing.get("trip"), outing.get("place")
        if trip is not None and (not isinstance(trip, str) or not 1 <= len(trip) <= 80):
            raise ValueError(f"Outing {name}: trip must be a short name")
        if place is not None and (not isinstance(place, str) or len(place) > 120):
            raise ValueError(f"Outing {name}: place must be text up to 120 characters")
        _text(outing.get("title"), 120, f"Outing {name}: title")
        _text(outing.get("note"), 2000, f"Outing {name}: note")
        for row in outing["sources"]:
            sha = row.get("sourceSha256") if isinstance(row, dict) else None
            if not isinstance(sha, str) or len(sha) != 64 or not set(sha) <= _HEX:
                raise ValueError("Roster sources need a full SHA-256")
            if sha in seen_sources:
                raise ValueError("A recording appears in two outings")
            if row.get("activity", UNLABELLED) not in (*ACTIVITIES, UNLABELLED):
                raise ValueError(f"Activity must be one of {', '.join(ACTIVITIES)} or unknown")
            _text(row.get("note"), 2000, "Recording note")
            _tags(row.get("tags"))
            seen_sources.add(sha)
    trips = doc.get("trips", {})
    if not isinstance(trips, dict) or len(trips) > 500:
        raise ValueError("Trip details must be a mapping")
    for key, info in trips.items():
        if not isinstance(key, str) or not isinstance(info, dict):
            raise ValueError("Trip details must be a mapping")
        _text(info.get("title"), 120, "Trip title")
        _text(info.get("place"), 120, "Trip place")
        _text(info.get("note"), 2000, "Trip note")
    return doc


def _text(value, limit: int, what: str) -> None:
    if value is not None and (not isinstance(value, str) or len(value) > limit):
        raise ValueError(f"{what} must be text up to {limit} characters")


def _tags(value) -> None:
    """Owner tags on a recording: people, animals, places, anything. Short free text."""
    if value is None:
        return
    if (not isinstance(value, list) or len(value) > 30
            or any(not isinstance(t, str) or not 1 <= len(t) <= 40 for t in value)):
        raise ValueError("Tags must be up to 30 short labels")


def outing_by_source(doc: dict) -> dict[str, str]:
    """{source SHA: outing}, the mapping evidence_ledger.with_outings expects."""
    return {row["sourceSha256"]: outing["outing"]
            for outing in validate(doc)["outings"] for row in outing["sources"]}


def activity_by_source(doc: dict) -> dict[str, str]:
    """{source SHA: activity}. A recording's own tag first, then a legacy one-sport outing tag."""
    out = {}
    for outing in validate(doc)["outings"]:
        for row in outing["sources"]:
            own = _activity(row.get("activity"))
            if own == UNLABELLED and outing.get("sport") in JUDGED_SPORTS:
                own = outing["sport"]
            out[row["sourceSha256"]] = own
    return out


# Marks a recording whose activity came from a model guess, not the owner. A roster
# holding any is for the judge panel only; evaluation commands refuse it.
GUESS_TAG = "activity-guessed"


def guessed_sources(doc: dict) -> set[str]:
    """Recordings whose activity is a model guess the owner has not confirmed."""
    return {row["sourceSha256"] for outing in validate(doc)["outings"]
            for row in outing["sources"] if GUESS_TAG in (row.get("tags") or [])}


def refuse_guesses(doc: dict) -> dict:
    """The roster, unless it holds model-guessed activities (which are not owner labels)."""
    guessed = guessed_sources(doc)
    if guessed:
        raise ValueError(f"This roster holds {len(guessed)} model-guessed activities (tag "
                         f"{GUESS_TAG}); use it only with klip_judge_batches.py, or confirm the "
                         "guesses (remove the tag) first")
    return doc


def sport_by_source(doc: dict) -> dict[str, str]:
    """{source SHA: sport} for recordings whose activity has judge questions."""
    return {sha: a for sha, a in activity_by_source(doc).items() if a in JUDGED_SPORTS}


def excluded_sources(doc: dict) -> set[str]:
    """Recordings the owner marked accidental: kept in the roster, left out of everything else."""
    return {sha for sha, a in activity_by_source(doc).items() if a == EXCLUDED}


def trip_by_outing(doc: dict) -> dict[str, str]:
    """{outing: trip}; an outing with no trip is its own trip."""
    return {o["outing"]: o.get("trip") or o["outing"] for o in validate(doc)["outings"]}


def group_of(mapping: dict[str, str], source_sha: str) -> str:
    """Held-out group for a recording. Without an outing it stands alone as its own group."""
    return mapping.get(source_sha) or f"source:{source_sha[:12]}"

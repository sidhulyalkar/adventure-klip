"""Conservative recording-session candidates, never proof of a continuous run.

Camera timestamps may be wrong or represent file completion instead of start.
This module uses timezone-aware timestamps only, emits review suggestions, and
never stitches files or asserts a shared event/physical route.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from .catalog import connect


def _utc(value: str | None) -> datetime | None:
    if not value or not isinstance(value,str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def group_session_candidates(assets: list[dict], gap_minutes: float = 15.0) -> dict:
    if not (0 < gap_minutes <= 180):
        raise ValueError("gap_minutes must be between 0 and 180")
    eligible=[]
    missing=[]
    for asset in assets:
        start = _utc(asset.get("capture_time"))
        activity = asset.get("activity")
        duration = asset.get("duration")
        try:
            duration = float(duration)
        except (ValueError,TypeError):
            duration = 0
        if (start is None or not (0 < duration <= 8 * 3600)
                or activity not in ("ski","bike","snorkeling")):
            missing.append(asset["id"])
            continue
        eligible.append({"asset_id":asset["id"],"activity":activity,
                         "start":start,"end":start+timedelta(seconds=duration)})
    eligible.sort(key=lambda x:(x["activity"],x["start"],x["asset_id"]))
    groups=[]
    for rec in eligible:
        if (groups and groups[-1][-1]["activity"] == rec["activity"]
                and rec["start"] <= max(r["end"] for r in groups[-1]) +
                    timedelta(minutes=gap_minutes)):
            groups[-1].append(rec)
        else:
            groups.append([rec])
    candidates=[]
    for group in groups:
        if len(group)<2:
            continue
        aids=[r["asset_id"] for r in group]
        uid=sha256("|".join(aids).encode()).hexdigest()[:16]
        candidates.append({"id":f"session-candidate-{uid}","activity":group[0]["activity"],
                           "assetIds":aids,"startUTC":min(r["start"] for r in group).isoformat(),
                           "endUTC":max(r["end"] for r in group).isoformat(),
                           "status":"requires_review",
                           "reason":"Nearby timezone-aware capture times, same labeled activity",
                           "warning":"Not proof of one continuous run or synchronized camera clocks"})
    return {"schemaVersion":1,"candidates":candidates,
            "ungroupedAssetIds":sorted(set(missing)),
            "otherSingletons":sum(len(g)==1 for g in groups)}


def session_candidates(workspace: Path, gap_minutes: float = 15.0) -> dict:
    with connect(workspace) as db:
        assets=[dict(r) for r in db.execute(
            "SELECT id,activity,capture_time,duration FROM assets ORDER BY id")]
    return group_session_candidates(assets,gap_minutes)

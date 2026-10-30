"""User-authored labels, grouping and explicit publication approvals."""
from __future__ import annotations
from hashlib import sha256
import json
import math
from pathlib import Path
from .catalog import connect, add_events, all_events, put_episodes
from .episodes import build_episodes
from .history import record_review

ALLOWED_ACTIVITIES={"unknown","ski","bike","snorkeling"}


def import_labels(workspace: Path, label_path: Path) -> dict:
    """Full replacement of manual event labels for one asset.

    Example JSON: {"asset_id":"<hash>", "activity":"ski", "events":[...]}
    """
    data=json.loads(label_path.read_text(encoding="utf-8"))
    asset_id=data["asset_id"]
    events=data["events"]
    if not isinstance(events,list):
        raise ValueError("events must be a list")
    with connect(workspace) as db:
        asset=db.execute("SELECT * FROM assets WHERE id=?",(asset_id,)).fetchone()
        if not asset:
            raise ValueError(f"Asset not found: {asset_id}")
        duration_ms=int((asset["duration"] or 0)*1000)
        activity=data.get("activity",asset["activity"])
        if activity not in ALLOWED_ACTIVITIES:
            raise ValueError(f"Invalid activity: {activity}")
        normalized=[]
        for i, e in enumerate(events):
            start,end=int(e["start_ms"]),int(e["end_ms"])
            if start<0 or end<=start or duration_ms and end>duration_ms:
                raise ValueError(f"Invalid range for event {i}: {start}..{end} (duration {duration_ms})")
            kind=str(e["kind"])
            if not kind or len(kind)>100:
                raise ValueError("Invalid event kind")
            score=float(e.get("score",0.8))
            confidence=float(e.get("confidence",1))
            if not all(math.isfinite(x) and 0<=x<=1 for x in (score,confidence)):
                raise ValueError("Score and confidence must be finite values between 0 and 1")
            evidence=e.get("evidence",{})
            if not isinstance(evidence,dict):
                raise ValueError("evidence must be a JSON object")
            eid="cur-"+sha256(f"{asset_id}|{kind}|{start}|{end}|{i}".encode()).hexdigest()[:20]
            normalized.append({"id":eid,"kind":kind,"start_ms":start,"end_ms":end,
                               "score":score,"confidence":confidence,"evidence":evidence})
        add_events(db,asset_id,normalized,"manual","manual-v1")
        db.execute("UPDATE assets SET activity=? WHERE id=?",(activity,asset_id))
        put_episodes(db,asset_id,build_episodes(asset_id,all_events(db,asset_id),duration_ms))
    return {"asset_id":asset_id,"manual_events":len(normalized),
            "warning":"Changed or removed event approvals were archived; identical events retain approvals"}


def bind_event(workspace: Path, event_id: str, world_entity_id: str,
               match_type: str = "exact", approve: bool = False) -> dict:
    if match_type not in {"exact","behavior","habitat","thematic"}:
        raise ValueError("Invalid match type")
    if not world_entity_id or not all(c.isascii() and (c.isalnum() or c in '._:-')
                                       for c in world_entity_id):
        raise ValueError("world_entity_id must be a nonempty stable identifier")
    with connect(workspace) as db:
        event=db.execute("SELECT * FROM events WHERE id=?",(event_id,)).fetchone()
        if not event:
            raise ValueError(f"Unknown event ID: {event_id}")
        if event["origin"]!="manual":
            raise ValueError("WorldLink bindings require manually reviewed events in v0.1")
        state="approved" if approve else "proposed"
        bid="binding-"+sha256(f"{event_id}|{world_entity_id}|{match_type}".encode()).hexdigest()[:20]
        previous=db.execute('SELECT * FROM bindings WHERE id=?',(bid,)).fetchone()
        if previous is not None and previous['review_state']!=state:
            record_review(db,previous,event,'superseded_by_explicit_review')
        db.execute("""INSERT INTO bindings(id,event_id,world_entity_id,match_type,review_state)
           VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET review_state=excluded.review_state""",
           (bid,event_id,world_entity_id,match_type,state))
        binding=db.execute('SELECT * FROM bindings WHERE id=?',(bid,)).fetchone()
        record_review(db,binding,event,'explicit_review')
    return {"id":bid,"state":state,"world_entity_id":world_entity_id}



def append_review_label(workspace: Path, asset_id: str, kind: str,
                        start_seconds: float, end_seconds: float,
                        *, note: str = "") -> dict:
    """Append one human-authored label without replacing previous manual work.

    Labels are declarations by the reviewer, never zero-shot predictions.
    Existing manual events, reviews and unrelated videos are preserved.
    """
    from .review_signals import MANUAL_INCIDENTS, MANUAL_NEGATIVES
    from .editorial_quality import POSITIVE_LABELS
    kinds = MANUAL_INCIDENTS | MANUAL_NEGATIVES | set().union(*POSITIVE_LABELS.values()) | {
        "tree_weave", "carve", "turn", "jump", "drop", "berm", "animal_presence",
        "technical_feature", "rock_garden", "good_run", "smooth_flow",
    }
    if kind not in kinds:
        raise ValueError("Unknown review label; use a documented activity/incident kind")
    if not all(math.isfinite(v) for v in (start_seconds, end_seconds)) \
            or start_seconds < 0 or end_seconds <= start_seconds:
        raise ValueError("Invalid review time interval")
    if len(note) > 240 or "\x00" in note:
        raise ValueError("Note must be 240 characters or fewer")
    start = round(start_seconds * 1000)
    end = round(end_seconds * 1000)
    eid = "cur-" + sha256(f"{asset_id}|{kind}|{start}|{end}".encode()).hexdigest()[:20]
    with connect(workspace) as db:
        asset = db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if asset is None:
            raise ValueError("Unknown asset SHA-256 ID")
        if end > int((asset["duration"] or 0)*1000):
            raise ValueError("Review label ends beyond the recording")
        earlier = [dict(r) for r in db.execute(
            "SELECT * FROM events WHERE asset_id=? AND origin='manual'",
            (asset_id,))]
        if any(r["id"] == eid or (r["kind"]==kind and
                                 r["start_ms"]==start and r["end_ms"]==end)
               for r in earlier):
            raise ValueError("This exact review interval is already labeled")
        preserved = [{
            "id": r["id"], "kind": r["kind"],
            "start_ms": r["start_ms"], "end_ms": r["end_ms"],
            "score": r["score"], "confidence": r["confidence"],
            "evidence": json.loads(r["evidence_json"] or "{}"),
        } for r in earlier]
        fresh = {"id": eid, "kind": kind, "start_ms": start, "end_ms": end,
                 "score": .8, "confidence": 1.0,
                 "evidence": {"reviewerNote": note, "reviewed": True,
                              "origin": "explicit_human_label"}}
        add_events(db, asset_id, preserved + [fresh], "manual", "manual-v1")
        put_episodes(db,asset_id,build_episodes(
            asset_id,all_events(db,asset_id),int((asset["duration"] or 0)*1000)))
    return {"assetId": asset_id, "eventId": eid, "kind": kind,
            "startMs": start, "endMs": end,
            "manualEventsPreserved": len(earlier),
            "status": "human_labeled"}

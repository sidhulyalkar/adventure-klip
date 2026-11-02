"""Human-reviewed, revisioned clip examples for learning and re-evaluation.

Example decisions are append-only and tied to immutable source SHA-256 and
video-relative timestamps. Confirmed means that a human reviewed a label,
not that a model scientifically verified it.
"""
from __future__ import annotations

from hashlib import sha256
import math
import re
from uuid import uuid4

from .catalog import connect

DECISIONS = {"confirmed", "rejected", "uncertain"}
HIGHLIGHTS = {"keep", "skip", "neutral"}
_LABEL = re.compile(r"^[a-z][a-z0-9_.:-]{0,79}$")

LATEST = """SELECT r.* FROM example_revisions r WHERE NOT EXISTS (
 SELECT 1 FROM example_revisions next
 WHERE next.example_id=r.example_id AND next.rowid>r.rowid
)"""


def latest_examples(db, *, asset_id: str | None = None) -> list[dict]:
    rows = [dict(r) for r in db.execute(LATEST+" ORDER BY r.asset_id,r.start_ms,r.label")]
    return [r for r in rows if asset_id is None or r["asset_id"] == asset_id]


def review_example(workspace, *, asset_id: str, start_seconds: float, end_seconds: float,
                   label: str, decision: str, highlight: str = "neutral",
                   note: str = "", reviewer: str = "local_user",
                   proposal: dict | None = None) -> dict:
    if not _LABEL.fullmatch(label):
        raise ValueError("Example label must be lowercase snake-case or namespace token")
    if decision not in DECISIONS or highlight not in HIGHLIGHTS:
        raise ValueError("Invalid decision or highlight preference")
    if (not all(isinstance(v, (float,int)) and not isinstance(v,bool)
                and math.isfinite(v) for v in (start_seconds, end_seconds))
            or start_seconds < 0 or end_seconds <= start_seconds):
        raise ValueError("Invalid example time interval")
    if not isinstance(note,str) or len(note)>500 or "\x00" in note:
        raise ValueError("Reviewer note must be at most 500 characters")
    if not reviewer or len(reviewer)>80 or not reviewer.isascii():
        raise ValueError("Invalid reviewer provenance")
    start, end = round(start_seconds*1000), round(end_seconds*1000)
    example_id = "ex-"+sha256(f"{asset_id}|{start}|{end}|{label}".encode()).hexdigest()[:24]
    with connect(workspace) as db:
        asset = db.execute("SELECT duration FROM assets WHERE id=?", (asset_id,)).fetchone()
        if asset is None:
            raise ValueError("Asset not in catalog; index the original first")
        duration = int((asset["duration"] or 0)*1000)
        if end<=start or (duration>0 and end>duration):
            raise ValueError("Example exceeds source duration or is too short")
        provenance = None
        if proposal is not None:
            if not isinstance(proposal, dict):
                raise ValueError("Proposal provenance must be an object")
            source = proposal.get("source")
            if source not in {"queue","library","manual"}:
                raise ValueError("Invalid proposal source")
            pid = proposal.get("id")
            label_before = proposal.get("label")
            policy = proposal.get("policyVersion")
            if not isinstance(pid,str) or not 1<=len(pid)<=180 or not pid.isascii() or not pid.isprintable():
                raise ValueError("Invalid proposal ID")
            if not isinstance(label_before,str) or not _LABEL.fullmatch(label_before):
                raise ValueError("Invalid original proposal label")
            if policy is not None and (not isinstance(policy,str) or not 1<=len(policy)<=80
                                       or not policy.isascii() or not policy.isprintable()):
                raise ValueError("Invalid trim policy version")
            a, b = proposal.get("startMs"), proposal.get("endMs")
            if (type(a) is not int or type(b) is not int or a<0 or b<=a
                    or (duration>0 and b>duration)):
                raise ValueError("Invalid original proposal interval")
            suggested_a, suggested_b = proposal.get("suggestedStartMs"),proposal.get("suggestedEndMs")
            if (suggested_a is None) != (suggested_b is None):
                raise ValueError("Both proposed trim boundaries are required")
            if suggested_a is not None and (
                type(suggested_a) is not int or type(suggested_b) is not int or
                suggested_a<0 or suggested_b<=suggested_a or
                (duration>0 and suggested_b>duration)):
                raise ValueError("Invalid trim suggestion interval")
            provenance = (asset_id,source,pid,label_before,a,b,policy,suggested_a,suggested_b)
        previous = db.execute(
            "SELECT * FROM example_revisions WHERE example_id=? ORDER BY rowid DESC LIMIT 1",
            (example_id,)).fetchone()
        if previous and previous["decision"]==decision and \
                previous["highlight"]==highlight and previous["reviewer_note"]==note:
            return {"exampleId":example_id,"revisionId":previous["revision_id"],
                    "status":"unchanged","revisionsAdded":0}
        revision = "er-"+uuid4().hex
        db.execute("""INSERT INTO example_revisions
          (revision_id,example_id,previous_revision_id,asset_id,start_ms,end_ms,
           label,decision,highlight,reviewer_note,reviewer)
          VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
          (revision,example_id,previous["revision_id"] if previous else None,
           asset_id,start,end,label,decision,highlight,note,reviewer))
        if provenance is not None:
            db.execute("""INSERT INTO review_proposal_links
              (revision_id,asset_id,proposal_source,proposal_id,proposal_label,
               proposed_start_ms,proposed_end_ms,trim_policy_version,
               suggested_start_ms,suggested_end_ms)
              VALUES (?,?,?,?,?,?,?,?,?,?)""",(revision,*provenance))
    return {"exampleId":example_id,"revisionId":revision,"status":"reviewed",
            "revisionsAdded":1,"previousRevisionId":previous["revision_id"] if previous else None}


def review_inventory(workspace, *, asset_id: str | None = None) -> dict:
    with connect(workspace) as db:
        examples = latest_examples(db,asset_id=asset_id)
        history = [dict(r) for r in db.execute(
            "SELECT example_id,revision_id,previous_revision_id,decision,highlight "
            "FROM example_revisions ORDER BY rowid")]
        assets = {r["id"]:r["activity"] for r in db.execute("SELECT id,activity FROM assets")}
    by_id = {}
    for row in history:
        by_id.setdefault(row["example_id"],[]).append(row)
    conflicts = []
    for i,a in enumerate(examples):
        for b in examples[i+1:]:
            if a["asset_id"]!=b["asset_id"]:
                continue
            overlap = max(0,min(a["end_ms"],b["end_ms"])-max(a["start_ms"],b["start_ms"]))
            smaller = min(a["end_ms"]-a["start_ms"],b["end_ms"]-b["start_ms"])
            if overlap/smaller < .8:
                continue
            same_label = a["label"]==b["label"]
            same_label_conflict = same_label and (a["decision"]!=b["decision"]
                                                   or a["highlight"]!=b["highlight"])
            cross_label_conflict = (not same_label and
                                    a["decision"]==b["decision"]=="confirmed" and
                                    {a["highlight"],b["highlight"]}=={"keep","skip"})
            if same_label_conflict or cross_label_conflict:
                conflicts.append({"a":a["example_id"],"b":b["example_id"],
                                  "reason":("overlapping_contradictory_review"
                                   if same_label_conflict else
                                   "cross_label_editorial_conflict")})
    return {"schemaVersion":1,"examples":[{
        "exampleId":e["example_id"],"revisionId":e["revision_id"],
        "previousRevisionId":e["previous_revision_id"],
        "assetId":e["asset_id"],"activity":assets.get(e["asset_id"],"unknown"),
        "startMs":e["start_ms"],"endMs":e["end_ms"],
        "label":e["label"],"decision":e["decision"],
        "highlight":e["highlight"],"note":e["reviewer_note"],
        "reviewer":e["reviewer"],
        "revisionCount":len(by_id.get(e["example_id"],[])),
        "reviewSplit":("test" if int(sha256(e["asset_id"].encode()).hexdigest(),16)%5==0
                       else "development"),
    } for e in examples],
    "overlapConflicts":conflicts,
    "reviewed":len(examples),"revisions":len(history),
    "limitations":["A single human review does not prove model accuracy",
                   "Asset-hash group split is provisional; trip-level holdouts are required",
                   "No video content is exported from the source library"]}


def curated_intervals(examples: list[dict], aid: str) -> tuple[list[dict],list[dict]]:
    """Latest confirmed explicit keep/skip preference, never inferred negatives."""
    chosen = [e for e in examples if e["asset_id"]==aid and e["decision"]=="confirmed"]
    keep = [e for e in chosen if e["highlight"]=="keep"]
    skip = [e for e in chosen if e["highlight"]=="skip"]
    return keep, skip

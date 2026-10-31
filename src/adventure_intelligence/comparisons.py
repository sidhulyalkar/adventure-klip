"""Human pairwise clip preferences, source-anchored and revisioned."""
from __future__ import annotations
from hashlib import sha256
from uuid import uuid4
import math
from .catalog import connect

PREFERENCES={"left","right","tie","uncertain"}
REASONS={"action","continuity","composition","wildlife","variety",
         "clarity","technique","story","other"}


def compare_clips(workspace, *, left_asset_id, left_start, left_end,
                  right_asset_id, right_start, right_end,
                  preference, reason="other", note=""):
    if preference not in PREFERENCES or reason not in REASONS:
        raise ValueError("Unsupported preference or comparison reason")
    if not isinstance(note,str) or len(note)>500 or "\x00" in note:
        raise ValueError("Comparison note must be 500 characters or fewer")
    positions=(left_start,left_end,right_start,right_end)
    if any(not isinstance(t,(int,float)) or isinstance(t,bool) or
           not math.isfinite(t) for t in positions):
        raise ValueError("Comparison timestamps must be finite numbers")
    a,b,c,d=(int(round(t*1000)) for t in positions)
    if a<0 or b<=a or c<0 or d<=c:
        raise ValueError("Invalid comparison intervals")
    if left_asset_id==right_asset_id and a==c and b==d:
        raise ValueError("Cannot compare an interval with itself")
    pair_id="pair-"+sha256(
        f"{left_asset_id}:{a}:{b}|{right_asset_id}:{c}:{d}".encode()).hexdigest()[:24]
    with connect(workspace) as db:
        for aid,end in ((left_asset_id,b),(right_asset_id,d)):
            row=db.execute("SELECT duration FROM assets WHERE id=?",(aid,)).fetchone()
            if row is None or not row["duration"] or end>round(row["duration"]*1000):
                raise ValueError("Missing comparison asset or interval beyond source duration")
        previous=db.execute(
            "SELECT * FROM pairwise_revisions WHERE comparison_id=? ORDER BY rowid DESC LIMIT 1",
            (pair_id,)).fetchone()
        if previous and (previous["preference"],previous["reason"],previous["reviewer_note"])==(
                preference,reason,note):
            return {"comparisonId":pair_id,"revisionId":previous["revision_id"],
                    "status":"unchanged"}
        revision="pr-"+uuid4().hex
        db.execute("""INSERT INTO pairwise_revisions
          (revision_id,comparison_id,previous_revision_id,left_asset_id,right_asset_id,
           left_start_ms,left_end_ms,right_start_ms,right_end_ms,preference,reason,reviewer_note)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",(
            revision,pair_id,previous["revision_id"] if previous else None,
            left_asset_id,right_asset_id,a,b,c,d,preference,reason,note))
    return {"comparisonId":pair_id,"revisionId":revision,
            "status":"reviewed","previousRevisionId":(
                previous["revision_id"] if previous else None)}


def comparison_summary(workspace):
    with connect(workspace) as db:
        latest=[dict(x) for x in db.execute("""SELECT p.* FROM pairwise_revisions p
          WHERE p.rowid=(SELECT MAX(q.rowid) FROM pairwise_revisions q
                         WHERE q.comparison_id=p.comparison_id)
          ORDER BY p.rowid DESC""")]
        total=db.execute("SELECT COUNT(*) FROM pairwise_revisions").fetchone()[0]
    return {"comparisons":latest,"current":len(latest),"revisions":total,
            "warning":"Pairwise preferences are human editorial choices, not motion/skill ground truth"}

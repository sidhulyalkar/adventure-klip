"""Review-gated, versioned, read-only source edit assembly.

Video clips are referenced only by immutable source hashes and approved human
review examples. All sequence edits live in workspace SQLite; export is a
render-compatible JSON plan under workspace/exports, never inside DJI folders.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import json
import re

from .catalog import connect
from .examples import latest_examples
from .safety import protect_source_output

MAX_CLIPS=40
MAX_DURATION_MS=600_000
MAX_NAME=72
REASONS={"pacing","continuity","duplicate","activity_mismatch","visual_mismatch",
         "weak_transition","narrative","other"}
_ID=re.compile(r"^ex-[a-f0-9]{24}$")


def _current(db):
    return db.execute(
        "SELECT * FROM sequence_draft_revisions ORDER BY rowid DESC LIMIT 1"
    ).fetchone()


def _approved(db):
    return {x["example_id"]:dict(x) for x in latest_examples(db)
            if x["decision"]=="confirmed" and x["highlight"]=="keep"}


def sequence_state(workspace: Path) -> dict:
    with connect(workspace) as db:
        approved=_approved(db)
        current=_current(db)
        entries=json.loads(current["entries_json"]) if current else []
        names={r["id"]:Path(r["stored_path"]).name
               for r in db.execute("SELECT id,stored_path FROM assets")}
        feedback=[]
        if current:
            feedback=[dict(row) for row in db.execute(
                "SELECT left_example_id,right_example_id,verdict,reason,note,created_at "
                "FROM sequence_combination_feedback WHERE sequence_revision_id=? "
                "ORDER BY rowid", (current["revision_id"],))]
        bank=[{"exampleId":x["example_id"],"assetId":x["asset_id"],
               "startMs":x["start_ms"],"endMs":x["end_ms"],"label":x["label"],
               "note":x["reviewer_note"],"source":names.get(x["asset_id"],"Indexed video")}
              for x in sorted(approved.values(),key=lambda r:(r["asset_id"],r["start_ms"]))]
        return {"schemaVersion":1,"name":current["name"] if current else "My highlights",
                "revisionId":current["revision_id"] if current else None,
                "entries":entries,"bank":bank,"feedback":feedback,
                "totalSeconds":round(sum((e["endMs"]-e["startMs"]) for e in entries)/1000,3),
                "limits":{"maxClips":MAX_CLIPS,"maxSeconds":MAX_DURATION_MS/1000},
                "note":"Only explicitly confirmed Keep examples can be sequenced"}


def _validate_entries(entries,approved,assets):
    if not isinstance(entries,list) or len(entries)>MAX_CLIPS:
        raise ValueError("Sequence must contain at most 40 clips")
    normalized=[]
    seen=set()
    duration=0
    for row in entries:
        if not isinstance(row,dict) or set(row)!={"exampleId"}:
            raise ValueError("Sequence entries must reference one approved exampleId")
        eid=row["exampleId"]
        if not isinstance(eid,str) or not _ID.fullmatch(eid) or eid in seen:
            raise ValueError("Duplicate or malformed example in sequence")
        ex=approved.get(eid)
        if ex is None:
            raise ValueError("Only human-confirmed Keep examples can enter the sequence")
        aid=ex["asset_id"]
        source=assets.get(aid)
        if source is None or not source["duration"] or ex["end_ms"]>round(source["duration"]*1000):
            raise ValueError("Invalid or unavailable source interval")
        duration+=ex["end_ms"]-ex["start_ms"]
        if duration>MAX_DURATION_MS:
            raise ValueError("Sequence exceeds the 600-second review budget")
        normalized.append({"exampleId":eid,"sourceAssetId":aid,
                           "startMs":ex["start_ms"],"endMs":ex["end_ms"],
                           "kind":ex["label"]})
        seen.add(eid)
    return normalized


def save_sequence(workspace: Path, *, entries, expected_revision,
                  name="My highlights") -> dict:
    if not isinstance(name,str) or not 1<=len(name.strip())<=MAX_NAME or "\x00" in name:
        raise ValueError("Invalid reel name")
    if expected_revision is not None and not isinstance(expected_revision,str):
        raise ValueError("Invalid expected revision")
    with connect(workspace) as db:
        current=_current(db)
        actual=current["revision_id"] if current else None
        if actual!=expected_revision:
            raise ValueError("Sequence was edited in another tab; reload before saving")
        approved=_approved(db)
        assets={r["id"]:dict(r) for r in db.execute("SELECT id,duration FROM assets")}
        normalized=_validate_entries(entries,approved,assets)
        # No duplicate revisions on repeated saves of an unchanged board.
        if current and current["name"]==name.strip() and json.loads(current["entries_json"])==normalized:
            return {"revisionId":actual,"status":"unchanged","entries":len(normalized)}
        rev="seq-"+uuid4().hex
        db.execute("""INSERT INTO sequence_draft_revisions
            (revision_id,previous_revision_id,name,entries_json)
            VALUES (?,?,?,?)""",
            (rev,actual,name.strip(),json.dumps(normalized,separators=(",",":"))))
    return {"revisionId":rev,"status":"saved","entries":len(normalized)}


def review_combination(workspace: Path, *, sequence_revision,
                       left_example_id, right_example_id,
                       verdict,reason="other",note=""):
    if verdict not in {"approve","reject"} or reason not in REASONS:
        raise ValueError("Invalid combination review")
    if not isinstance(note,str) or len(note)>500 or "\x00" in note:
        raise ValueError("Invalid feedback note")
    if verdict=="reject" and len(note.strip())<5:
        raise ValueError("Explain what was wrong with this scene combination")
    with connect(workspace) as db:
        current=_current(db)
        if current is None or sequence_revision!=current["revision_id"]:
            raise ValueError("Sequence changed; reload before reviewing a transition")
        entries=json.loads(current["entries_json"])
        pairs=[(a["exampleId"],b["exampleId"]) for a,b in zip(entries,entries[1:])]
        if (left_example_id,right_example_id) not in pairs:
            raise ValueError("Only adjacent scenes in the current sequence can be reviewed")
        review_id="sf-"+uuid4().hex
        db.execute("""INSERT INTO sequence_combination_feedback
          (feedback_id,sequence_revision_id,left_example_id,right_example_id,
           verdict,reason,note) VALUES (?,?,?,?,?,?,?)""",
           (review_id,sequence_revision,left_example_id,right_example_id,
            verdict,reason,note.strip()))
    return {"status":"recorded","feedbackId":review_id}


def export_sequence(workspace: Path, *, expected_revision: str) -> dict:
    workspace=Path(workspace).expanduser().resolve()
    with connect(workspace) as db:
        current=_current(db)
        if current is None or current["revision_id"]!=expected_revision:
            raise ValueError("Sequence changed; reload before export")
        entries=json.loads(current["entries_json"])
        if not entries:
            raise ValueError("Add at least one approved clip before export")
        # Revalidate on export, since a previously approved review may have
        # been overturned after this draft was saved.
        checked=_validate_entries(
            [{"exampleId":e["exampleId"]} for e in entries],_approved(db),
            {r["id"]:dict(r) for r in db.execute("SELECT id,duration FROM assets")})
        plan={"schemaVersion":1,"recipe":"manual-sequence","status":"draft",
              "name":current["name"],"sequenceRevision":expected_revision,
              "maxSeconds":MAX_DURATION_MS/1000,
              "totalSeconds":round(sum(e["endMs"]-e["startMs"] for e in checked)/1000,3),
              "segments":[{
                  "episodeId":"review-"+e["exampleId"],"sourceAssetId":e["sourceAssetId"],
                  "kind":e["kind"],"startMs":e["startMs"],"endMs":e["endMs"],
                  "duration":round((e["endMs"]-e["startMs"])/1000,3),
                  "score":1.0,"reviewed":True
              } for e in checked],
              "disclaimer":"Manual source-time sequence; no confirmation of continuity across scenes"}
        target=workspace/"exports"/"sequences"/(expected_revision+".json")
        protect_source_output(db,target)
        target.parent.mkdir(parents=True,exist_ok=True)
        data=json.dumps(plan,indent=2,allow_nan=False)+"\n"
        temp=target.with_suffix(".tmp")
        temp.write_text(data,encoding="utf-8")
        temp.replace(target)
    return {"status":"exported","path":str(target),"clips":len(checked),
            "totalSeconds":plan["totalSeconds"],
            "renderCommand":f"klip --workspace {workspace} render {target} --output {workspace / 'exports' / 'sequences' / (expected_revision+'-reel.mp4')}"}

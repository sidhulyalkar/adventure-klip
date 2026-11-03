"""Explicit bounded promotion of high-consensus activity proposals.

A separate opt-in transaction, never performed by scanning. Checkpoint hashes,
catalog identity/duration, current video SHA-256, review bounds and a SQLite
online backup are required before any label mutation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import sqlite3
from pathlib import Path

from .activity_intelligence import (
    VERSION, _canonical, _hash, _load_records,
)
from .catalog import connect
from .sources import resolve_source


def apply_activity_proposals(workspace:Path, report_dir:Path, *,
                             activity:str="snorkeling",max_assets:int=2,
                             minimum_margin:float=.065,
                             minimum_votes:int=5, apply:bool=False) -> dict:
    """Pick the most conservative candidates; unreviewed model output remains fallible."""
    if activity not in {"snorkeling","ski","bike"}:
        raise ValueError("--activity must be snorkeling, ski or bike")
    if type(max_assets) is not int or not 1<=max_assets<=10:
        raise ValueError("--max-assets must be 1..10")
    if (not isinstance(minimum_margin,(float,int)) or isinstance(minimum_margin,bool)
            or not math.isfinite(minimum_margin) or not .05<=minimum_margin<=.25):
        raise ValueError("--minimum-margin must be 0.05..0.25")
    if type(minimum_votes) is not int or not 5<=minimum_votes<=9:
        raise ValueError("--minimum-votes must be 5..9")
    workspace=Path(workspace).expanduser().resolve()
    report_dir=Path(report_dir).expanduser().resolve()
    if (not report_dir.is_relative_to(workspace)
            or not report_dir.is_dir()
            or not (report_dir/"summary.json").is_file()):
        raise ValueError("Report directory must be a completed workspace activity scan")
    config=json.loads((report_dir/"config.json").read_text())
    summary=json.loads((report_dir/"summary.json").read_text())
    if summary.get("version")!=VERSION or summary.get("configHash")!=_hash(config):
        raise ValueError("Invalid or incompatible activity report")
    records=_load_records(report_dir/"records.jsonl",_hash(config))
    choices=[]
    for record in records.values():
        dec=record["decision"]
        samples=record["observations"]
        strong=(record.get("sourceVerifiedSha256")==record["assetId"]
                and dec["status"]=="strong_candidate"
                and dec["proposedActivity"]==activity
                and dec["validSamples"]>=minimum_votes
                and dec["winnerVotes"]>=minimum_votes
                and dec["otherStrongVotes"]==0
                and dec["medianWinningMargin"]>=minimum_margin
                and record.get("previousActivity")=="unknown"
                and record.get("durationMs",0)>=30000
                and record.get("modelRevision"))
        if strong:
            choices.append(record)
    choices.sort(key=lambda r:(-r["decision"]["medianWinningMargin"],
                               -r["decision"]["winnerVotes"],r["assetId"]))
    with connect(workspace) as db:
        catalog={row["id"]:dict(row) for row in db.execute(
            "SELECT id,duration,activity,stored_path FROM assets")}
        eligible=[r for r in choices if
                  (r["assetId"] in catalog
                   and catalog[r["assetId"]]["activity"]=="unknown"
                   and int(catalog[r["assetId"]]["duration"]*1000)==r["durationMs"])]
    eligible=eligible[:max_assets]
    result={"schemaVersion":1,"activity":activity,"dryRun":not apply,
            "candidatesMeetingGates":len(choices),
            "eligibleInCurrentCatalog":len(eligible),
            "selected":[{"assetId":r["assetId"],
                         "basename":r["basename"],
                         "medianMargin":r["decision"]["medianWinningMargin"],
                         "winnerVotes":r["decision"]["winnerVotes"]}
                        for r in eligible],
            "applied":[],"skipped":[],"backup":None,
            "warning":"Unreviewed zero-shot consensus is not verified sport ground truth."}
    if not apply or not eligible:
        return result
    # New, uniquely named SQLite backup within workspace, through SQLite's
    # online backup API (captures WAL consistently). If backup fails, no writes.
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup=workspace/"reports"/f"catalog.before-activity-{stamp}.sqlite3"
    backup.parent.mkdir(parents=True,exist_ok=True)
    with connect(workspace) as db:
        with sqlite3.connect(backup) as destination:
            db.backup(destination)
        result["backup"]=str(backup)
        for record in eligible:
            aid=record["assetId"]
            current=db.execute("SELECT * FROM assets WHERE id=?",(aid,)).fetchone()
            if current is None or current["activity"]!="unknown":
                result["skipped"].append({"assetId":aid,"reason":"catalog_label_changed"})
                continue
            # Hash source again. A path/stat match alone is never sufficient
            # for allowing a model to change a curated catalog label.
            try:
                resolved=resolve_source(db,current)
                if record["sourceVerifiedSha256"]!=aid:
                    raise ValueError("Classifier source identity mismatch")
            except (ValueError,OSError) as exc:
                result["skipped"].append({"assetId":aid,"reason":str(exc)[:120]})
                continue
            before=db.execute("SELECT activity FROM assets WHERE id=?",(aid,)).fetchone()[0]
            updated=db.execute(
                "UPDATE assets SET activity=? WHERE id=? AND activity='unknown'",
                (activity,aid)).rowcount
            if updated!=1:
                result["skipped"].append({"assetId":aid,"reason":"concurrent_label_change"})
                continue
            result["applied"].append({"assetId":aid,"oldActivity":before,
                                      "newActivity":activity,
                                      "sourcePath":str(resolved),
                                      "reviewStatus":"unreviewed_model_proposal"})
        db.commit()
    audit=workspace/"reports"/f"activity-apply-{stamp}.json"
    with audit.open("x",encoding="utf-8") as handle:
        handle.write(json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+"\n")
    result["audit"]=str(audit)
    return result

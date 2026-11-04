"""Opt-in recovery of per-frame semantic checkpoints without fabricating full coverage.

Read only completed, integrity-verified per-frame rows from a FAILED adaptive
semantic run. Verify exact plan/config, the full original source SHA-256, and
the checksums of every accepted frame before publishing a partial document.
No model loading, decoding, source modification, or silent status promotion.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from .adaptive_sampling import validate_plan
from .catalog import connect
from .safety import protect_source_output
from .semantic_jobs import digest, encoded
from .sources import resolve_source, verified_digest
from .tracking import associate_boxes


def recover_semantic_frames(workspace: Path, asset_id: str, plan_file: Path,
                            *, provider: str = "ecology-owlv2",
                            max_missing: int = 2, min_fraction: float = .80) -> dict:
    if (not isinstance(asset_id,str) or len(asset_id)!=64 or
            any(c not in "0123456789abcdef" for c in asset_id)):
        raise ValueError("Full lowercase source asset SHA-256 is required")
    if provider not in {"histogram","owlv2","ecology-owlv2","siglip2","context"}:
        raise ValueError("Unsupported semantic provider")
    if type(max_missing) is not int or not 1<=max_missing<=8:
        raise ValueError("max-missing must be 1..8")
    if (not isinstance(min_fraction,(float,int)) or isinstance(min_fraction,bool)
            or not math.isfinite(min_fraction) or not .70<=min_fraction<=.99):
        raise ValueError("min-fraction must be between .70 and .99")
    workspace=Path(workspace).expanduser().resolve()
    plan_file=Path(plan_file).expanduser().resolve()
    if not plan_file.is_relative_to(workspace):
        raise ValueError("Plan must be a file in the current workspace")
    raw=plan_file.read_bytes()
    if len(raw)>15_000_000:
        raise ValueError("Plan exceeds bounded input size")
    plan=json.loads(raw)
    schedule=validate_plan(plan)
    if asset_id not in schedule:
        raise ValueError("Asset absent from verified adaptive plan")
    times=schedule[asset_id]
    if len(times)<3:
        raise ValueError("At least three scheduled timestamps required for partial recovery")
    with connect(workspace) as db:
        asset=db.execute("SELECT * FROM assets WHERE id=?",(asset_id,)).fetchone()
        if asset is None:
            raise ValueError("Asset is not indexed")
        expected_duration=next(a["durationMs"] for a in plan["assets"]
                               if a["assetId"]==asset_id)
        # The adaptive planner uses int(float(duration)*1000), not round().
        # Rounding here can differ by 1 ms on genuine fractional-duration
        # recordings, incorrectly rejecting an unchanged signed plan.
        duration_seconds=float(asset["duration"])
        if not math.isfinite(duration_seconds) or duration_seconds<=0:
            raise ValueError("Invalid current indexed video duration")
        actual_duration=int(duration_seconds*1000)
        if actual_duration!=expected_duration:
            raise ValueError(
                "Video duration differs from original signed plan "
                f"(catalog={actual_duration}ms, plan={expected_duration}ms)")
        # There can be multiple failed configurations. Require exact provider,
        # adaptive plan and a unique config instead of guessing which run.
        matches=[]
        for row in db.execute(
                "SELECT * FROM semantic_jobs WHERE asset_id=? AND status IN ('failed','partial')",
                (asset_id,)):
            conf=json.loads(row["config_json"])
            if (conf.get("provider")==provider and
                    conf.get("adaptiveScheduleId")==plan["planId"] and
                    digest(encoded(conf))==row["config_id"]):
                matches.append((row,conf))
        if len(matches)!=1:
            raise ValueError("Expected exactly one failed or partial provider job for this plan")
        job,conf=matches[0]
        entries=db.execute(
            "SELECT t_ms,payload,sha256 FROM semantic_frames "
            "WHERE asset_id=? AND config_id=? ORDER BY t_ms",
            (asset_id,job["config_id"])).fetchall()
        frame_map={}
        expected_set=set(times)
        for entry in entries:
            t=entry["t_ms"]
            if t not in expected_set:
                # Stale/corrupt cache contents must never be adopted.
                raise ValueError("Checkpoint contains an unscheduled frame")
            if digest(entry["payload"])!=entry["sha256"]:
                raise ValueError("Checkpoint frame hash mismatch")
            frame=json.loads(entry["payload"])
            if (not isinstance(frame,dict) or type(frame.get("tMs")) is not int
                    or frame["tMs"]!=t):
                raise ValueError("Checkpoint frame timestamp mismatch")
            if frame.get("timestampBasis")!="decoded_frame_pts" or \
                    type(frame.get("decodedPtsMs")) is not int:
                raise ValueError("Checkpoint lacks verified source PTS")
            frame_map[t]=frame
        missing=[t for t in times if t not in frame_map]
        if (not missing or len(missing)>max_missing or
                len(frame_map)/len(times)<min_fraction):
            raise ValueError("Partial recovery fails minimum evidence/coverage limits")
        # Even with independent hashed cache rows, the actual original media
        # has to match the registered content before we publish a result.
        source=resolve_source(db,asset)
        frames=[frame_map[t] for t in times if t in frame_map]
        tracking=associate_boxes(frames)
        doc={
            "schemaVersion":3,"assetId":asset_id,"configId":job["config_id"],
            "provider":provider,"modelVersion":conf.get("modelVersion"),
            "config":conf,"sampleIntervalMs":None,
            "frames":frames,"tracking":tracking,
            "coverageStatus":"partial_verified_source",
            "scheduledFrames":len(times),
            "availableFrames":len(frames),
            "missingRequestedTimesMs":missing,
            "missingDoesNotMeanAbsent":True,
            "recoveredFrom":"hash_verified_per_frame_sqlite_checkpoints",
            "limitations":[
                "A missing requested frame is unknown, not negative object evidence.",
                "No source video frame decoding or new model inference was performed.",
                "Detector object labels and box tracks remain unverified proposals.",
                "The report is partial; completeness and sensitivity are not established.",
            ],
        }
        filename=f"{asset_id}-{job['config_id']}.partial.json"
        target=workspace/"semantics"/filename
        protect_source_output(db,target)
        text=encoded(doc)
        if target.exists():
            if target.read_text()!=text:
                raise ValueError("Existing partial document differs from recovered evidence")
        else:
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open("x",encoding="utf-8") as f:
                f.write(text)
        # Two checks provide source integrity at read and publication.
        if verified_digest(source)!=asset_id:
            target.unlink(missing_ok=True)
            raise RuntimeError("Source changed during partial recovery")
        db.execute(
            "UPDATE semantic_jobs SET status='partial',error=?,filename=?,sha256=? "
            "WHERE asset_id=? AND config_id=? AND status IN ('failed','partial')",
            (f"Partial source-verified recovery; {len(missing)} missing scheduled frame(s)",
             filename,digest(text),asset_id,job["config_id"]))
        db.commit()
        return {
            "status":"partial_verified_source",
            "sourceVerifiedSha256":asset_id,
            "provider":provider,"configId":job["config_id"],
            "availableFrames":len(frames),
            "scheduledFrames":len(times),
            "missingRequestedTimesMs":missing,
            "newInferenceCalls":0,"newFrameDecodes":0,
            "document":str(target),
            "warning":"Not a complete scan. Missing samples and negative evidence cannot be inferred.",
        }

"""Read-only diagnosis of frozen archive campaigns and source-PTS checkpoints.

Never decodes videos, hashes large original files, calls an AI model, or
changes the catalog. Frame-level completion is based on exact plan timestamps
and payload checksums; job-level completion also requires the signed completed
document. Explicit partial coverage is NOT a complete scan.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from html import escape
from pathlib import Path
import json
import re

from .adaptive_sampling import validate_plan
from .archive_pilot import _open_catalog
from .semantic_jobs import digest, verified_document

VERSION="archive-status-v1"
BATCH=re.compile(r"batch-(\d{4})$")


def _read_json(path:Path, cap:int=20_000_000):
    if not path.is_file() or path.stat().st_size>cap:
        raise ValueError(f"Missing or oversized campaign artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_campaign(workspace:Path,campaign:Path):
    if not campaign.is_relative_to(workspace) or campaign==workspace:
        raise ValueError("Campaign must be within the current workspace")
    if campaign.relative_to(workspace).parts[0] not in ("reports","exports"):
        raise ValueError("Campaign must be in workspace reports/ or exports/")
    roster=_read_json(campaign/"roster.json")
    if (roster.get("frozenSourceHashRoster") is not True
        or not isinstance(roster.get("orderedAssets"),list)
        or not isinstance(roster.get("config"),dict)):
        raise ValueError("Expected versioned frozen archive roster")
    items=roster["orderedAssets"]
    if any(not isinstance(a,dict) or
           not isinstance(a.get("assetId"),str) or
           len(a["assetId"])!=64 for a in items):
        raise ValueError("Malformed frozen source identities")
    if len({a["assetId"] for a in items})!=len(items):
        raise ValueError("Duplicate source identities in frozen campaign")
    return roster


def _job_status(db,workspace:Path,aid:str,plan_id:str,times:list[int],
                provider:str)->dict:
    jobs=[]
    for job in db.execute(
        "SELECT * FROM semantic_jobs WHERE asset_id=?",(aid,)):
        try:
            conf=json.loads(job["config_json"])
            if (conf.get("provider")!=provider or
                conf.get("adaptiveScheduleId")!=plan_id):
                continue
        except (ValueError,TypeError,AttributeError):
            continue
        jobs.append((job,conf))
    if not jobs:
        return {
            "status":"not_started","configuredJobCount":0,
            "validCheckpointFrames":0,
            "scheduledFrames":len(times),
            "missingRequestedTimesMs":times,
            "invalidCheckpointTimesMs":[],
            "sourcePTSVerifiedFrames":0,
            "error":None,
            "nearTailFailure":False,
            "recommendedAction":"Run the frozen plan; no matching job exists.",
        }
    if len(jobs)!=1:
        return {
            "status":"ambiguous_configs","configuredJobCount":len(jobs),
            "validCheckpointFrames":0,
            "scheduledFrames":len(times),
            "missingRequestedTimesMs":times,
            "invalidCheckpointTimesMs":[],
            "sourcePTSVerifiedFrames":0,
            "error":"Multiple model/runtime configurations match the same plan",
            "nearTailFailure":False,
            "recommendedAction":"Inspect conflicting configuration IDs before retry.",
        }
    job,conf=jobs[0]
    available=set()
    invalid=[]
    pts=0
    for row in db.execute(
        "SELECT t_ms,payload,sha256 FROM semantic_frames "
        "WHERE asset_id=? AND config_id=? ORDER BY t_ms",
        (aid,job["config_id"])):
        t=row["t_ms"]
        try:
            payload=json.loads(row["payload"])
            valid=(t in times and digest(row["payload"])==row["sha256"]
                   and isinstance(payload,dict)
                   and type(payload.get("tMs")) is int
                   and payload["tMs"]==t)
        except (ValueError,TypeError):
            valid=False
        if not valid:
            invalid.append(t)
            continue
        available.add(t)
        if (payload.get("timestampBasis")=="decoded_frame_pts"
            and type(payload.get("decodedPtsMs")) is int):
            pts+=1
    missing=[t for t in times if t not in available]
    # A completed document is required before claiming exact completed job.
    doc=verified_document(workspace,job,allow_partial=True)
    if job["status"]=="completed" and not missing and not invalid and doc is not None:
        status="completed"
    elif job["status"]=="partial" and doc is not None:
        status="verified_partial"
    elif job["status"]=="completed":
        status="inconsistent_completed"
    elif job["status"]=="failed":
        status="failed_with_checkpoints" if available else "failed_no_checkpoints"
    else:
        status=job["status"] or "unknown"
    can_recover=(
        status=="failed_with_checkpoints" and not invalid
        and 0<len(missing)<=2 and len(times)>=3
        and len(available)/len(times)>=.8 and pts==len(available)
    )
    # This is a diagnostic hint, NOT a root cause conclusion. An EOF seek
    # cannot be declared the cause without FFmpeg stderr/source inspection.
    near_tail=bool(missing and missing[-1]==max(times) and
                   job["error"] and "FFmpeg" in job["error"])
    return {
        "status":status,
        "configuredJobCount":1,
        "configId":job["config_id"],
        "provider":provider,
        "storedJobStatus":job["status"],
        "scheduledFrames":len(times),
        "validCheckpointFrames":len(available),
        "sourcePTSVerifiedFrames":pts,
        "invalidCheckpointTimesMs":invalid,
        "missingRequestedTimesMs":missing,
        "error":job["error"],
        "nearTailFailure":near_tail,
        "recoveryEligibleWithoutReinference":can_recover,
        "recommendedAction":(
            "Recover verified partial checkpoints, preserving missing frames."
            if can_recover else
            "Do not rerun the full batch; investigate missing timestamps first."
            if status.startswith("failed") else
            "Inspect cache consistency, source identity and artifact provenance."
            if status=="inconsistent_completed" else
            "No additional processing needed." if status=="completed"
            else "Partial evidence must remain marked incomplete."
        ),
    }


def inspect_archive_campaign(workspace:Path,campaign:Path,*,
                             provider:str="histogram")->dict:
    if provider not in ("histogram","siglip2","owlv2","ecology-owlv2","context"):
        raise ValueError("Unsupported analysis provider")
    root=Path(workspace).expanduser().resolve()
    campaign=Path(campaign).expanduser().resolve()
    roster=_validate_campaign(root,campaign)
    ordered=roster["orderedAssets"]
    config=roster["config"]
    batch_size=config.get("batchSize")
    if type(batch_size) is not int or not 1<=batch_size<=16:
        raise ValueError("Invalid frozen batch size")
    detail=[]
    completed_planned=set()
    with closing(_open_catalog(root)) as db:
        for folder in sorted(campaign.glob("batch-*")):
            found=BATCH.fullmatch(folder.name)
            if not folder.is_dir() or not found:
                continue
            number=int(found.group(1))
            manifest=_read_json(folder/"manifest.json")
            plan=_read_json(folder/"plan.json")
            scheduled=validate_plan(plan)
            selected=ordered[(number-1)*batch_size:number*batch_size]
            expected={x["assetId"] for x in selected}
            settings=manifest.get("settings",{})
            if (set(scheduled)!=expected or
                settings.get("planId")!=plan.get("planId") or
                settings.get("selectedAssetIds")!=[x["assetId"] for x in selected] or
                settings.get("batch")!=number):
                raise ValueError(f"Frozen roster/plan mismatch: {folder.name}")
            batch=[]
            for asset in selected:
                aid=asset["assetId"]
                j=_job_status(db,root,aid,plan["planId"],
                              scheduled[aid],provider)
                name=asset.get("basename","unknown")
                row={
                    "assetId":aid,"filename":name,
                    "selectionGroup":asset.get("selectionGroup","unresolved"),
                    "activityConfirmed":asset.get("activityConfirmed",False),
                    "durationMs":asset.get("durationMs"),
                    **j,
                }
                if row["status"]=="completed":
                    completed_planned.add(aid)
                if row.get("recoveryEligibleWithoutReinference"):
                    row["recoveryCommand"]=(
                        f'klip --workspace "{root}" semantic-recover '
                        f'--asset {aid} --provider {provider} '
                        f'--plan "{folder/"plan.json"}" '
                        "--max-missing 2 --min-fraction 0.80"
                    )
                batch.append(row)
            counts=Counter(a["status"] for a in batch)
            detail.append({
                "batch":number,"plan":str(folder/"plan.json"),
                "planId":plan["planId"],
                "scheduledFrames":sum(len(x) for x in scheduled.values()),
                "validCheckpointFrames":sum(a["validCheckpointFrames"] for a in batch),
                "statuses":dict(sorted(counts.items())),
                "assets":batch,
            })
    completed=sum(b["statuses"].get("completed",0) for b in detail)
    failed=sum(sum(n for status,n in b["statuses"].items()
                   if status.startswith("failed") or status in
                      ("inconsistent_completed","ambiguous_configs"))
               for b in detail)
    recovery=[a for b in detail for a in b["assets"]
              if a.get("recoveryEligibleWithoutReinference")]
    return {
        "schemaVersion":1,"version":VERSION,"provider":provider,
        "campaign":str(campaign),
        "frozenRosterCount":len(ordered),
        "plannedBatches":len(detail),
        "plannedAssets":sum(len(b["assets"]) for b in detail),
        "completedAssets":completed,
        "problemAssets":failed,
        "eligiblePartialRecoveries":len(recovery),
        "scheduledFrames":sum(b["scheduledFrames"] for b in detail),
        "validCheckpointFrames":sum(b["validCheckpointFrames"] for b in detail),
        "batches":detail,
        "sourceVideoBytesRead":0,"newVideoFramesDecoded":0,
        "newModelInferences":0,"originalMediaModified":False,
        "interpretation":[
            "Frame counts are exact-plan cache coverage, not wildlife/action recall.",
            "Completed requires a valid completed artifact and every scheduled checkpoint.",
            "Missing samples remain unknown, never negative evidence.",
            "FFmpeg failures near the last requested timestamp suggest a possible EOF seek issue, not proven causation.",
            "Source SHA is NOT reverified by this read-only status command.",
            "Previously measured source-verification/model timings are not reconstructed when absent.",
        ],
    }


def render_archive_status(report:dict)->str:
    cards=[]
    for batch in report["batches"]:
        rows=[]
        for a in batch["assets"]:
            gaps=", ".join(str(t/1000) for t in a["missingRequestedTimesMs"][:12])
            status=escape(a["status"])
            command=escape(a.get("recoveryCommand",""))
            rows.append(
                '<article><div class="head"><strong>'+
                escape(a["filename"])+'</strong><span>'+status+'</span></div>'
                '<p>'+escape(a["selectionGroup"])+f' · {a["validCheckpointFrames"]}/'
                f'{a["scheduledFrames"]} checkpoint frames'
                +(f' · missing at {escape(gaps)}s' if gaps else '')+'</p>'
                +(f'<p>Error: {escape(a["error"])}</p>' if a["error"] else '')
                +(f'<pre>{command}</pre>' if command else '')
                +'</article>')
        cards.append(f'<section><h2>Batch {batch["batch"]:04d}</h2>'+
                     ''.join(rows)+'</section>')
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Klip archive status</title><style>'
        'body{font:14px system-ui,sans-serif;background:#101923;color:#edf0f3;'
        'max-width:1100px;margin:30px auto;padding:0 16px;line-height:1.5}'
        'h1{font-size:1.6rem}h2{font-size:1.2rem;color:#f0b9aa}'
        '.head{display:flex;gap:12px;justify-content:space-between}'
        'article{border:1px solid #35485a;background:#1b2b37;'
        'border-radius:10px;padding:12px;margin:12px 0}'
        'p,span{color:#b0c0cb}pre{overflow-x:auto;white-space:pre-wrap;'
        'overflow-wrap:anywhere;color:#d1d9e2}'
        '</style></head><body><h1>Archive pilot · coverage and failure triage</h1>'
        f'<p>{report["completedAssets"]}/{report["plannedAssets"]} completed · '
        f'{report["problemAssets"]} problem assets · '
        f'{report["eligiblePartialRecoveries"]} possible partial recoveries.</p>'
        '<p>Read-only report. Missing evidence never means no event occurred. '
        'Source files were not opened or hashed by this inspection.</p>'
        +''.join(cards)+'</body></html>'
    )


def write_archive_status(workspace:Path,report:dict,output_dir:Path):
    root=Path(workspace).expanduser().resolve()
    folder=Path(output_dir).expanduser().resolve()
    if (not folder.is_relative_to(root) or folder==root or
        folder.relative_to(root).parts[0] not in ("reports","exports") or
        folder.exists()):
        raise ValueError("Output must be a new workspace reports/ or exports/ folder")
    folder.mkdir(parents=True,mode=0o700,exist_ok=False)
    with (folder/"status.json").open("x",encoding="utf-8") as file:
        json.dump(report,file,indent=2,sort_keys=True,allow_nan=False)
        file.write("\n")
    with (folder/"index.html").open("x",encoding="utf-8") as file:
        file.write(render_archive_status(report))
    return {"json":str(folder/"status.json"),"html":str(folder/"index.html")}

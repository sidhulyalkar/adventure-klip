"""One-command, cache-aware archive pilot: inventory, select, plan, optional cheap run.

Works inside Klip itself. Legacy catalogs are READ ONLY and are never
implicitly merged or processed. Unknown video activities stay unknown.
No source copies, auto-deletion, remote models, or expensive OWLv2 execution.
A stable batch path/plan makes the command safe to resume after interruptions.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
import json
import math
import sqlite3

from .activity_intelligence import _hash
from .adaptive_sampling import build_plan, validate_plan
from .catalog import connect
from .locking import lock_workspace
from .safety import protect_source_output
from .semantic import generate_semantics
from .progress import Progress

VERSION="archive-pilot-orchestrator-v1"
GROUPS=("snorkeling","ski","bike","unresolved")
SOURCE_POLICY="readonly_media_no_copy"
DEFAULT_OUTPUT="reports/archive-pilot-v1"


def _open_catalog(root:Path):
    catalog=root/"catalog.sqlite3"
    if not catalog.is_file():
        raise ValueError(f"Missing Klip catalog: {catalog}")
    uri="file:"+quote(str(catalog),safe="/")+"?mode=ro"
    db=sqlite3.connect(uri,uri=True,timeout=5)
    db.row_factory=sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _table(db,name):
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone() is not None


def inventory(root:Path)->tuple[dict,list[dict]]:
    """Metadata-only inventory, no workspaces initialized or modified."""
    root=Path(root).expanduser().resolve()
    with closing(_open_catalog(root)) as db:
        if not _table(db,"assets"):
            raise ValueError(f"Invalid workspace catalog (assets missing): {root}")
        org=_table(db,"asset_organization")
        sql=(
            "SELECT a.id,a.stored_path,a.duration,a.activity,a.size_bytes,"
            "COALESCE(NULLIF(o.activity_override,''),a.activity) AS effective_activity "
            "FROM assets a LEFT JOIN asset_organization o ON o.asset_id=a.id"
            if org else
            "SELECT id,stored_path,duration,activity,size_bytes,"
            "activity AS effective_activity FROM assets")
        assets=[dict(row) for row in db.execute(sql)]
        stages=defaultdict(set)
        stage_counts=Counter()
        if _table(db,"analyses"):
            for row in db.execute("SELECT asset_id,stage,status FROM analyses"):
                stage_counts[f"{row['stage']}:{row['status']}"]+=1
                if row["status"]=="completed":
                    stages[row["asset_id"]].add(row["stage"])
        sem=defaultdict(set)
        semantic_counts=Counter()
        if _table(db,"semantic_jobs"):
            for row in db.execute("SELECT asset_id,status,config_json FROM semantic_jobs"):
                try:
                    provider=json.loads(row["config_json"]).get("provider","unknown")
                except (TypeError,ValueError,AttributeError):
                    provider="unparseable"
                semantic_counts[f"{provider}:{row['status']}"]+=1
                if row["status"]=="completed":
                    sem[row["asset_id"]].add(provider)
        checkpoint_counts=Counter()
        if _table(db,"semantic_frames"):
            checkpoint_counts.update({
                row["asset_id"]:row["n"]
                for row in db.execute(
                    "SELECT asset_id,COUNT(*) n FROM semantic_frames GROUP BY asset_id")})
        reviewed=set()
        if _table(db,"example_revisions"):
            reviewed={r[0] for r in db.execute("SELECT DISTINCT asset_id FROM example_revisions")}
    available=[]
    outcomes=Counter()
    counts=Counter()
    for row in assets:
        activity=row["effective_activity"]
        counts[activity]+=1
        duration=row["duration"]
        valid=(type(duration) in (int,float) and math.isfinite(duration)
               and int(duration*1000)>=1)
        path=Path(row["stored_path"]) if isinstance(row["stored_path"],str) else None
        present=bool(path and path.is_file())
        if not valid:
            outcomes["invalidDuration"]+=1
        if not present:
            outcomes["sourceUnavailable"]+=1
        aid=row["id"]
        if valid and present and isinstance(aid,str) and len(aid)==64:
            available.append({
                "assetId":aid,"activity":activity,"durationMs":int(duration*1000),
                "basename":path.name,"sizeBytes":row["size_bytes"],
                "completedAnalyses":sorted(stages[aid]),
                "completedSemanticProviders":sorted(sem[aid]),
                "checkpointedFrames":checkpoint_counts[aid],
                "humanReviewed":aid in reviewed,
            })
        if checkpoint_counts[aid]:
            outcomes["assetsWithFrameCheckpoints"]+=1
    summary={
        "workspace":str(root),"assetCount":len(assets),
        "accessibleValidAssets":len(available),
        "activityCounts":dict(sorted(counts.items())),
        "analysisStates":dict(sorted(stage_counts.items())),
        "semanticJobStates":dict(sorted(semantic_counts.items())),
        "qualityFlags":dict(sorted(outcomes.items())),
        "sourceByteHashingPerformed":False,
        "sourceFramesDecoded":0,
        "catalogModified":False,
        "statusInterpretation":"SQLite metadata only; completed artifacts are not re-verified",
    }
    return summary,available


def _first_pass_proposals(root:Path)->dict[str,dict]:
    report=root/"reports"/"activity-overnight-v1"
    source=report/"records.jsonl"
    config=report/"config.json"
    if not source.is_file() or not config.is_file() or source.stat().st_size>40_000_000:
        return {}
    try:
        config_hash=_hash(json.loads(config.read_text()))
        result={}
        with source.open(encoding="utf-8") as handle:
            for line in handle:
                if len(line)>500_000:
                    continue
                record=json.loads(line)
                body=record.get("record")
                if (not isinstance(body,dict) or
                        body.get("configHash")!=config_hash or
                        record.get("sha256")!=_hash(body)):
                    continue
                decision=body.get("decision",{})
                group=decision.get("proposedActivity")
                if group not in ("snorkeling","ski","bike"):
                    continue
                result[body["assetId"]]={
                    "activityHypothesis":group,
                    "proposalStatus":decision.get("status","unknown"),
                    "catalogConfirmed":False,
                }
        return result
    except (ValueError,KeyError,TypeError,UnicodeError,OSError):
        return {}


def _order_assets(assets:list[dict],proposals:dict[str,dict])->list[dict]:
    buckets={key:[] for key in GROUPS}
    for a in assets:
        confirmed=a["activity"] if a["activity"] in GROUPS[:3] else None
        proposal=proposals.get(a["assetId"])
        group=confirmed or (proposal or {}).get("activityHypothesis") or "unresolved"
        status="confirmed" if confirmed else (proposal or {}).get("proposalStatus","unresolved")
        # This is selection STRATIFICATION, never an activity label change.
        entry={**a,"selectionGroup":group,"selectionEvidence":status,
               "activityConfirmed":bool(confirmed)}
        buckets[group].append(entry)
    # Include different levels of evidence in the pilot rather than picking
    # only high-confidence or only uncertain videos.
    level={"confirmed":0,"strong_candidate":1,
           "ambiguous_needs_review":2,"likely_other":3,"unresolved":4}
    for k in buckets:
        buckets[k].sort(key=lambda a:(
            level.get(a["selectionEvidence"],5),
            # Put already-analyzed, human-reviewed assets first for validation.
            not a["humanReviewed"],
            not bool(a["completedAnalyses"] or a["completedSemanticProviders"]),
            -a["checkpointedFrames"],
            a["assetId"]))
    ordered=[]
    while any(buckets.values()):
        for k in GROUPS:
            if buckets[k]:
                ordered.append(buckets[k].pop(0))
    return ordered


def _legacy_comparison(legacy:Path|None,primary_ids:set[str])->dict|None:
    if legacy is None:
        return None
    root=Path(legacy).expanduser().resolve()
    if not (root/"catalog.sqlite3").is_file():
        return {"workspace":str(root),"available":False}
    summary,items=inventory(root)
    ids=set()
    with closing(_open_catalog(root)) as db:
        ids={row[0] for row in db.execute("SELECT id FROM assets")}
    return {"workspace":str(root),"available":True,
            "assetCount":summary["assetCount"],
            "sharedSourceHashes":len(ids & primary_ids),
            "onlyLegacySourceHashes":len(ids-primary_ids),
            "onlyPrimarySourceHashes":len(primary_ids-ids),
            "legacyArtifactsCopied":False,
            "note":"Identical asset SHA does not prove compatible cached models or runtime revisions"}


def run_archive_pilot(workspace:Path,*,legacy:Path|None=None,
                      batch:int=1,batch_size:int=8,run:bool=False,
                      step_seconds:float=30,max_frames:int=24,
                      output_dir:Path|None=None)->dict:
    """Discover one stratified batch, materialize a signed plan, optionally run histogram."""
    if type(batch) is not int or not 1<=batch<=10000:
        raise ValueError("--batch must be 1..10000")
    if type(batch_size) is not int or not 1<=batch_size<=16:
        raise ValueError("--batch-size must be 1..16")
    if type(max_frames) is not int or not 8<=max_frames<=128:
        raise ValueError("--max-frames must be 8..128")
    if type(step_seconds) not in (float,int) or not 5<=step_seconds<=300:
        raise ValueError("--coverage-seconds must be between 5 and 300")
    root=Path(workspace).expanduser().resolve()
    primary,accessible=inventory(root)
    with closing(_open_catalog(root)) as db:
        primary_ids={row[0] for row in db.execute("SELECT id FROM assets")}
    comparison=_legacy_comparison(legacy,primary_ids)
    folder=(
        Path(output_dir).expanduser().resolve() if output_dir is not None else
        root/DEFAULT_OUTPUT/f"batch-{batch:04d}"
    )
    if (not folder.is_relative_to(root) or folder==root or
            folder.relative_to(root).parts[0] not in {"reports","exports"}):
        raise ValueError("Output directory must be under workspace reports/ or exports/")
    campaign=folder.parent
    roster_path=campaign/"roster.json"
    campaign_config={"version":VERSION,"batchSize":batch_size,
                     "coverageSeconds":step_seconds,"maxFrames":max_frames}
    if roster_path.is_file():
        roster=json.loads(roster_path.read_text(encoding="utf-8"))
        if (roster.get("config")!=campaign_config or
                not isinstance(roster.get("orderedAssets"),list)):
            raise ValueError(
                "Frozen archive campaign uses different settings; use the "
                "original batch size/frame budget or a NEW output campaign")
        ordered=roster["orderedAssets"]
    else:
        proposals=_first_pass_proposals(root)
        ordered=_order_assets(accessible,proposals)
        if not ordered:
            return {"version":VERSION,"status":"no_eligible_assets",
                    "inventory":primary,"legacyComparison":comparison}
        campaign.mkdir(parents=True,exist_ok=True)
        with roster_path.open("x",encoding="utf-8") as f:
            json.dump({"config":campaign_config,
                       "frozenSourceHashRoster":True,
                       "createdAtUtc":datetime.now(timezone.utc).isoformat(),
                       "orderedAssets":ordered,
                       "legacyComparisonAtCreation":comparison,
                       "note":"Never auto-label activities or merge legacy artifacts."},
                      f,indent=2,sort_keys=True,allow_nan=False)
            f.write("\n")
    start=(batch-1)*batch_size
    chosen=ordered[start:start+batch_size]
    if not chosen:
        return {"version":VERSION,"status":"no_more_eligible_assets",
                "inventory":primary,"legacyComparison":comparison,
                "batch":batch,"batchSize":batch_size,
                "frozenRoster":str(roster_path),
                "sourceMediaModified":False}
    ids=[a["assetId"] for a in chosen]
    live={x["assetId"]:x for x in accessible}
    if any(a["assetId"] not in live or
           a["durationMs"]!=live[a["assetId"]]["durationMs"] for a in chosen):
        raise ValueError("One or more frozen-batch sources are inaccessible or changed "
                         "duration; reconnect the SSD before proceeding")
    plan_path=folder/"plan.json"
    manifest_path=folder/"manifest.json"
    with connect(root) as db:
        for path in (plan_path,manifest_path,roster_path):
            protect_source_output(db,path)
    if folder.exists():
        if (not folder.is_dir() or not plan_path.is_file() or
                not manifest_path.is_file()):
            raise ValueError("Incomplete existing batch folder, refusing overwrite")
        saved=json.loads(manifest_path.read_text(encoding="utf-8"))
        plan=json.loads(plan_path.read_text(encoding="utf-8"))
        schedule=validate_plan(plan)
        settings=saved.get("settings")
        expected={"version":VERSION,"batch":batch,"batchSize":batch_size,
                  "coverageSeconds":step_seconds,"maxFrames":max_frames,
                  "selectedAssetIds":ids,"planId":plan["planId"]}
        if settings!=expected or set(schedule)!=set(ids):
            raise ValueError("Existing batch has a different frozen selection or plan")
        for asset in plan["assets"]:
            if asset["durationMs"]!=live[asset["assetId"]]["durationMs"]:
                raise ValueError("Indexed duration has changed since this batch was frozen")
    else:
        # Generate the signed plan ONCE. Later cache changes cannot silently
        # alter the sequence of sampled timestamps or the selected assets.
        plan=build_plan(root,coverage_seconds=step_seconds,interest_seconds=2,
                        max_frames=max_frames,limit=batch_size,
                        activity="all",asset_ids=ids)
        schedule=validate_plan(plan)
        if set(schedule)!=set(ids):
            raise ValueError("Adaptive planner did not preserve exact selected assets")
        settings={"version":VERSION,"batch":batch,"batchSize":batch_size,
                  "coverageSeconds":step_seconds,"maxFrames":max_frames,
                  "selectedAssetIds":ids,"planId":plan["planId"]}
        folder.mkdir(parents=True,mode=0o700,exist_ok=False)
        with plan_path.open("x",encoding="utf-8") as f:
            json.dump(plan,f,indent=2,sort_keys=True,allow_nan=False)
            f.write("\n")
        manifest={"settings":settings,"inventory":primary,
                  "legacyComparison":comparison,"selection":chosen,
                  "frozenRoster":str(roster_path),
                  "selectionGroups":dict(sorted(Counter(
                      x["selectionGroup"] for x in chosen).items())),
                  "budget":{"scheduledFrames":plan["totals"]["scheduledFrames"],
                            "sourceByteFootprint":sum(x["sizeBytes"] for x in chosen
                                                     if isinstance(x["sizeBytes"],int)),
                            "maxModelFramesPerAsset":max_frames},
                  "limitations":[
                      "Unconfirmed activity proposals only stratify sample selection.",
                      "Plan uses already cached evidence; missing intervals are unknown.",
                      "Hashing/decoding costs incurred only with --run.",
                      "This plan is a small first-pass sample, not a complete recall guarantee.",
                      "Legacy workspace is read only and not merged.",
                  ]}
        with manifest_path.open("x",encoding="utf-8") as f:
            json.dump(manifest,f,indent=2,sort_keys=True,allow_nan=False)
            f.write("\n")
    result={"version":VERSION,
            "status":"planned" if not run else "running",
            "batch":batch,"batchSize":len(ids),
            "selectedActivityStrata":dict(sorted(Counter(
                a["selectionGroup"] for a in chosen).items())),
            "selectedSourceNames":[a["basename"] for a in chosen],
            "plan":str(plan_path),"manifest":str(manifest_path),
            "frozenRoster":str(roster_path),
            "scheduledFrames":plan["totals"]["scheduledFrames"],
            "legacyComparison":comparison,
            "sourceMediaModified":False,
            "backend":"histogram" if run else None,
            "resumeHint":("Run this exact command again; Klip reuses matching per-frame "
                          "checkpoints. Advance with --batch "+str(batch+1)+".")}
    if not run:
        return result
    # Only a CHOSEN opt-in action calls the cheap histogram provider.
    # Existing semantic job checkpoint machinery handles resumed frames.
    with lock_workspace(root):
        run_result=generate_semantics(root,backend="histogram",
                        asset_ids=set(ids),sample_schedule=schedule,
                        schedule_id=plan["planId"],decode_mode="batched",
                        progress=Progress())
    result["run"]=run_result
    result["status"]="partial_failure" if run_result["errors"] else "completed"
    return result

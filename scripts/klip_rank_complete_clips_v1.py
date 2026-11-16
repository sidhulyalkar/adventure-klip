#!/usr/bin/env python3
"""Re-rank and extract playable Top Clips from frozen Klip preview evidence.

Reads 120 SHA-backed V2 preview clips, 108 successful Auto-Scout receipts
(and 12 failures), plus an optional reviewed complete ski run. Reuses cached
media, does not call a model or access the external DJI original archive.
This is not a comprehensive ranking across the full source-video library.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from adventure_intelligence import auto_scout_v2 as scout
from adventure_intelligence import complete_highlight_rerank as ranker
from adventure_intelligence import ski_event_scaling as scaling
from adventure_intelligence import ski_run_context as run_context
from adventure_intelligence.media import require_tool


def _read_json(path: Path, *, limit: int=18_000_000) -> tuple[dict, bytes]:
    if path.is_symlink() or not path.is_file() or not 2 <= path.stat().st_size <= limit:
        raise ValueError("Missing or unsafe frozen JSON")
    raw=path.read_bytes()
    doc=json.loads(raw)
    if not isinstance(doc,dict):
        raise ValueError("Expected JSON object")
    return doc,raw


def _verify_approved_run(ws: Path, context_dir: Path, owner_file: Path,
                         *, force_break: list[int], secondary: list[int]) -> tuple[list[dict],dict,Path,dict]:
    context=scout._bounded_workspace_path(ws,context_dir,existing=True)
    plan,plan_bytes=_read_json(context/"run-context-plan.json",limit=2_000_000)
    if plan.get("version")!=run_context.VERSION:
        raise ValueError("Unsupported frozen run context version")
    info=plan.get("previewRender")
    if not isinstance(info,dict) or info.get("sourceMediaChanged") is not False:
        raise ValueError("Missing verified source-safe run preview receipt")
    video=context/"media"/"run-context.mp4"
    checksum=info.get("videoSha256")
    if (video.is_symlink() or not video.is_file()
            or sha256(video.read_bytes()).hexdigest()!=checksum):
        raise ValueError("Reviewed full-run proxy SHA mismatch")
    owner,raw=_read_json(owner_file.expanduser(),limit=200_000)
    run_context.validate_owner_run(owner,plan)
    hints=scaling.hints_template()
    hints["forcedBreakAfterMs"]=force_break
    hints["secondaryFeatureMs"]=secondary
    draft=scaling.plan_event_lines(owner,plan,hints=hints)
    approved=ranker.reviewed_run_groups(owner,draft)
    metadata={
        "contextPlanSha256":sha256(plan_bytes).hexdigest(),
        "ownerRunSha256":sha256(raw).hexdigest(),
        "sourcePreviewSha256":checksum,
        "contextRelativeTimebaseApproximate":True,
        "ownerRunPreference":owner["preference"],
        "groupingBoundariesApproved":False,
        "ownerGroupingOverrideMs":force_break,
    }
    return approved, plan, video, metadata


def _probe_duration(path: Path) -> float:
    ffprobe=require_tool("ffprobe")
    p=subprocess.run(
        [ffprobe,"-hide_banner","-loglevel","error",
         "-show_entries","format=duration",
         "-of","default=noprint_wrappers=1:nokey=1",str(path)],
        capture_output=True,text=True,timeout=30,check=False,
    )
    if p.returncode:
        raise ValueError("New preview clip ffprobe failed")
    try:
        value=float(p.stdout.strip())
    except ValueError as exc:
        raise ValueError("Unparseable new preview clip duration") from exc
    if not 0.25<=value<=300:
        raise ValueError("New preview duration out of bounds")
    return value


def _source_safe_copy(src: Path, dst: Path, sha: str) -> dict:
    if dst.exists() or dst.is_symlink() or src.is_symlink():
        raise ValueError("Unsafe proxy staging path")
    if not src.is_file() or sha256(src.read_bytes()).hexdigest()!=sha:
        raise ValueError("Source proxy SHA no longer matches original verified manifest")
    # A standalone COPY, not a hardlink: changing an output cannot alter the input inode.
    shutil.copyfile(src,dst,follow_symlinks=False)
    if sha256(dst.read_bytes()).hexdigest()!=sha:
        raise ValueError("Output proxy changed during independent copy")
    return {
        "mediaSha256":sha,
        "bytes":dst.stat().st_size,
        "extractionMethod":"copy_unchanged_previously_verified_h264_proxy",
        "originalCameraVideoRead":False,
        "newEncodingPerformed":False,
    }


def _render_owner_clip(video:Path,dst:Path,context_start:int,
                       start:int,end:int)->dict:
    ffmpeg=require_tool("ffmpeg")
    relative=(start-context_start)/1000
    seconds=(end-start)/1000
    if not 0<=relative<=86400 or not 0.5<=seconds<=240:
        raise ValueError("Invalid owner-run edit source-relative bounds")
    if dst.exists() or dst.is_symlink():
        raise ValueError("Owner cut already exists")
    partial=dst.with_name(dst.stem+".partial.mp4")
    try:
        result=subprocess.run([
            ffmpeg,"-nostdin","-hide_banner","-loglevel","error",
            "-ss",f"{relative:.3f}","-i",str(video),
            "-t",f"{seconds:.3f}",
            "-map","0:v:0","-map","0:a:0?",
            "-vf","fps=24,scale=960:-2:flags=bicubic,format=yuv420p",
            "-c:v","libx264","-preset","veryfast","-crf","26",
            "-c:a","aac","-b:a","96k",
            "-movflags","+faststart",str(partial),
        ],capture_output=True,timeout=190,check=False)
        if result.returncode or not partial.is_file() or partial.stat().st_size<100:
            raise ValueError("Private owner run re-encoding failed")
        duration=_probe_duration(partial)
        if abs(duration-seconds)>1.6:
            raise ValueError("Rendered owner-run segment has unexpected duration")
        os.replace(partial,dst)
    finally:
        partial.unlink(missing_ok=True)
    return {
        "mediaSha256":sha256(dst.read_bytes()).hexdigest(),
        "bytes":dst.stat().st_size,
        "durationSeconds":round(duration,3),
        "extractionMethod":"bounded_reencode_of_existing_verified_run_context_proxy",
        "originalCameraVideoRead":False,
        "newEncodingPerformed":True,
        "nativePtsAligned":False,
    }


def execute(args)->dict:
    ws=args.workspace.expanduser().resolve(strict=True)
    pilot=scout._bounded_workspace_path(ws,args.pilot,existing=True)
    experiment=scout._bounded_workspace_path(ws,args.autoscout,existing=True)
    out=scout._bounded_workspace_path(ws,args.output,existing=False)
    if (out.exists() or out.is_symlink() or out==experiment
            or out.is_relative_to(experiment) or out==pilot or out.is_relative_to(pilot)):
        raise ValueError("Choose a new private output folder outside existing studies")
    rows,plan_sha=scout.load_pilot(ws,pilot)
    state,state_bytes=_read_json(experiment/"experiment-state.json",limit=2_000_000)
    if (state.get("version")!=scout.VERSION
            or state.get("pilot")!=str(pilot)
            or state.get("inputPlanSha256")!=plan_sha
            or state.get("policyFingerprint")!=scout.fingerprint(state.get("model",""))):
        raise ValueError("Auto-Scout is not the matching frozen pilot/model policy")
    receipts=scout.read_receipts(experiment,rows,plan_sha,state["policyFingerprint"])
    if len(receipts)!=len(rows):
        raise ValueError("Auto-Scout still has missing receipts")
    owner_groups=[]
    run_context_plan=None
    run_video=None
    run_provenance=None
    if bool(args.owner_run) != bool(args.context_dir):
        raise ValueError("--owner-run and --context-dir must be supplied together")
    if args.owner_run:
        owner_groups,run_context_plan,run_video,run_provenance=_verify_approved_run(
            ws,args.context_dir,args.owner_run,
            force_break=args.force_break_after_ms,
            secondary=args.secondary_feature_ms,
        )
        if run_context_plan["sourceAssetSha256"] not in {x["sourceSha256"] for x in rows}:
            raise ValueError("Approved run source is absent from frozen pilot")
    elif args.force_break_after_ms or args.secondary_feature_ms:
        raise ValueError("Owner edit overrides require --owner-run and --context-dir")
    suggestions=ranker.candidates(rows,receipts)
    selection=ranker.rank_review_queue(
        suggestions+owner_groups,top=args.top,
        per_sport=args.per_sport,
    )
    top_list=selection["top"]
    failed=[
        {
            "recordId":row["recordId"],
            "sourceSha256":row["sourceSha256"],
            "sourceStartMs":row["sourceStartMs"],
            "sourceEndMs":row["sourceEndMs"],
            "failureStage":receipts[row["recordId"]].get("failureStage","legacy_unknown"),
            "failureCode":receipts[row["recordId"]].get("failureCode","legacy_unknown"),
            "unknownNotNegative":True,
        }
        for row in rows if receipts[row["recordId"]]["status"]=="failed"
    ]
    fingerprint=sha256(json.dumps(
        {k:receipts[k] for k in sorted(receipts)},
        sort_keys=True,separators=(",",":"),allow_nan=False,
    ).encode()).hexdigest()
    report={
        "version":ranker.VERSION,
        "status":"review_ranking_dry_run" if not args.render else "playable_top_review_ready",
        "pilotPlanSha256":plan_sha,
        "autoscoutStateSha256":sha256(state_bytes).hexdigest(),
        "autoscoutReceiptsSha256":fingerprint,
        "totalFrozenWindows":len(rows),
        "modelAnalyzed":len(suggestions),"modelFailed":len(failed),
        "sourceCoverageOnlyNotFullLibrary":True,
        "fullLibraryRankingCompleted":False,
        "humanReviewedRunProvenance":run_provenance,
        "reviewedRunCandidateCount":len(owner_groups),
        "selection":{k:v for k,v in selection.items() if k!="top"},
        "failedModelControls":failed,
        "topRankedClips":top_list,
        "originalDJIVideoReads":0,
        "modelInferenceCalls":0,
        "originalFootageModified":False,
        "trainingPerformed":False,
        "published":False,
        "clipSelectionRule":"reviewed_run_tier_then_source_diverse_unverified_categories",
        "qualifications":[
            "Ranks after owner tier are uncalibrated discovery opportunities, not genuine highlight-quality judgments.",
            "The 120 proxy windows are a sample and cannot establish full-library clip recall.",
            "Model blur/shake flags are not used to lower ranking; prior flags were strongly overpredicted.",
            "Model failures remain unranked investigation controls, never treated as unwanted footage.",
            "Owner-run edits are from an approximate-seek H264 context preview, not a native master.",
        ],
    }
    if not args.render:
        return {
            "status":"validated_dry_run_no_files_created",
            "totalFrozenWindows":len(rows),"modelAnalyzed":len(suggestions),
            "modelFailed":len(failed),"ownerGroups":len(owner_groups),
            "proposedTop":len(top_list),
            "topPreview": [
                {"rank":x["rank"],"origin":x["origin"],
                 "sport":x["activity"],"source":x["sourceSha256"][:12],
                 "sourceStartMs":x["sourceStartMs"],
                 "sourceEndMs":x["sourceEndMs"],
                 "categoryProposals":x["categoryProposals"]}
                for x in top_list[:12]
            ],
            "mediaOpened":False,"outputsCreated":0,
        }
    temp=out.with_name(out.name+".partial")
    if temp.exists() or temp.is_symlink():
        raise ValueError("A partial output already exists; inspect before rerunning")
    temp.mkdir(parents=True,mode=0o700,exist_ok=False)
    media=temp/"media"
    media.mkdir(mode=0o700)
    by_record={row["recordId"]:row for row in rows}
    for i,item in enumerate(top_list,1):
        filename=f"{i:02d}-{item['id'][:32]}.mp4"
        dst=media/filename
        if item["ownerReviewed"]:
            if run_video is None:
                raise ValueError("Missing verified complete-run video")
            rendered=_render_owner_clip(
                run_video,dst,run_context_plan["contextSourceStartMs"],
                item["sourceStartMs"],item["sourceEndMs"],
            )
        else:
            original_row=by_record[item["previewRecordId"]]
            rendered=_source_safe_copy(
                original_row["video"],dst,original_row["proxySha256"]
            )
        item["mediaFile"]=filename
        item["renderReceipt"]=rendered
        item["rankExplanation"]=(
            "Human-reviewed complete run; proposed event grouping needs final cut approval."
            if item["ownerReviewed"] else
            "Provisional rank from specific category hypotheses and source diversity, not human preference."
        )
        print(
            f"[top-clips] {i}/{len(top_list)} {item['activity']} "
            f"{item['sourceSha256'][:12]} "
            f"{item['sourceStartMs']/1000:.2f}–{item['sourceEndMs']/1000:.2f}s",
            file=sys.stderr,flush=True,
        )
    scout.save_json(temp/"ranked-clips.json",report)
    scout.save_json(temp/"all-candidates.json",{
        "version":ranker.VERSION,
        "modelCandidates":suggestions,
        "ownerRunGroups":owner_groups,
        "rejectedResponseControls":failed,
        "notFullLibrary":True,
    })
    with (temp/"top-ranked.csv").open("w",encoding="utf-8",newline="") as stream:
        writer=csv.writer(stream)
        writer.writerow([
            "rank","activity","source_sha256","source_start_seconds",
            "source_end_seconds","evidence_tier","suggested_tags",
            "model_review_opportunity_score","local_video",
        ])
        for item in top_list:
            writer.writerow([
                item["rank"],item["activity"],item["sourceSha256"],
                f'{item["sourceStartMs"]/1000:.3f}',
                f'{item["sourceEndMs"]/1000:.3f}',
                item["rankClass"],
                ",".join(item["categoryProposals"]),
                (item["heuristic"] or {}).get("reviewOpportunityScore",""),
                "media/"+item["mediaFile"],
            ])
    (temp/"review.html").write_text(ranker.gallery_html(report),encoding="utf-8")
    os.replace(temp,out)
    return {
        "status":"playable_top_review_ready",
        "output":str(out),"reviewPage":str(out/"review.html"),
        "rankedJson":str(out/"ranked-clips.json"),
        "rankedCsv":str(out/"top-ranked.csv"),
        "count":len(top_list),"ownerBackedClips":len(owner_groups),
        "modelAnalyzed":len(suggestions),"modelFailed":len(failed),
        "scope":"verified_120_preview_windows_plus_one_reviewed_run",
        "fullLibraryBestVerified":False,
        "originalSourceReads":0,"modelCalls":0,
    }


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace",type=Path,required=True)
    p.add_argument("--pilot",type=Path,required=True)
    p.add_argument("--autoscout",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--context-dir",type=Path)
    p.add_argument("--owner-run",type=Path)
    p.add_argument("--force-break-after-ms",type=int,nargs="*",default=[])
    p.add_argument("--secondary-feature-ms",type=int,nargs="*",default=[])
    p.add_argument("--top",type=int,default=20)
    p.add_argument("--per-sport",type=int,default=None)
    p.add_argument("--render",action="store_true")
    args=p.parse_args()
    try:
        print(json.dumps(execute(args),indent=2,sort_keys=True,allow_nan=False))
        return 0
    except (ValueError,KeyError,TypeError,OSError,json.JSONDecodeError,
            subprocess.TimeoutExpired) as exc:
        print("Top Clips rerun refused safely: "+str(exc),file=sys.stderr)
        return 3


if __name__=="__main__":
    raise SystemExit(main())

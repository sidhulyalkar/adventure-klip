#!/usr/bin/env python3
"""Investigate a complete ski line beyond the frozen 20-second clip.

Default: JSON-only dry run, no original camera path read, no FFmpeg, no network.
--render-context --allow-mounted-ssd: explicit one-source SHA verification +
bounded local video preview. Does not change current Auto-Scout/Blind-Calib.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from adventure_intelligence.auto_scout_v2 import (
    _bounded_workspace_path, load_pilot, save_json
)
from adventure_intelligence.full_archive_campaign import (
    _hash_verified, _require_local_path
)
from adventure_intelligence.ski_run_context import (
    VERSION, find_qualified_audit, match_anchor, plan_context,
    owner_annotation_template, validate_owner_run,
)
from adventure_intelligence.ski_run_context_studio import page


def _probe_video(ffprobe:str,source:Path)->float:
    res=subprocess.run([
        ffprobe,"-v","error","-show_entries","format=duration",
        "-of","default=noprint_wrappers=1:nokey=1",str(source)
    ],capture_output=True,text=True,timeout=30,check=False)
    if res.returncode:
        raise ValueError("Qualified camera source FFprobe failed")
    try:
        seconds=float(res.stdout.strip())
    except ValueError as exc:
        raise ValueError("Qualified camera duration is invalid") from exc
    if not 1<=seconds<=86_400:
        raise ValueError("Camera source duration is outside supported bounds")
    return seconds


def _verified_source_for_expansion(ws:Path,asset:dict,args,plan:dict)->Path:
    if not args.allow_mounted_ssd or args.trusted_source_root is None:
        raise ValueError(
            "Camera source expansion requires explicit --allow-mounted-ssd "
            "and --trusted-source-root; inspection mode needs neither")
    trusted=Path(args.trusted_source_root).expanduser()
    path=asset["storedPathAsCatalogued"]
    local=_require_local_path(
        path,ws,trusted,allow_mounted=True)
    if local is None:
        raise ValueError("Catalogued original is not accessible in trusted source root")
    expected_bytes=asset["sourceBytesFromCatalog"]
    before=local.stat()
    if before.st_size!=expected_bytes or not local.is_file():
        raise ValueError("Original media size no longer matches frozen archive audit")
    started=time.monotonic()
    _hash_verified(local,plan["sourceAssetSha256"])
    after=local.stat()
    if (after.st_size!=before.st_size or after.st_mtime_ns!=before.st_mtime_ns
            or after.st_ino!=before.st_ino or after.st_dev!=before.st_dev):
        raise ValueError("Original changed during SHA verification")
    plan["sourceShaVerifiedForThisExpansion"]=True
    plan["sourceShaVerificationSeconds"]=round(time.monotonic()-started,2)
    return local


def _render_one_context(source:Path,plan:dict,dest:Path,
                        expected_duration_s:float,timeout_s:int=900)->dict:
    from adventure_intelligence.media import require_tool
    ffprobe=require_tool("ffprobe")
    ffmpeg=require_tool("ffmpeg")
    actual=_probe_video(ffprobe,source)
    if abs(actual-expected_duration_s)>1.5:
        raise ValueError("Camera duration differs from frozen audited duration")
    a=plan["contextSourceStartMs"]/1000
    duration=plan["contextDurationMs"]/1000
    target=dest/"media"/"run-context.mp4"
    temp=dest/"media"/"run-context.partial.mp4"
    if temp.exists() or temp.is_symlink() or target.exists() or target.is_symlink():
        raise ValueError("Context output path already exists")
    cmd=[
        ffmpeg,"-nostdin","-hide_banner","-loglevel","error",
        "-ss",f"{a:.3f}","-i",str(source),
        "-t",f"{duration:.3f}",
        "-map","0:v:0","-map","0:a:0?",
        "-vf","scale=960:-2:flags=bicubic,fps=24,format=yuv420p",
        "-c:v","libx264","-preset","veryfast","-crf","27",
        "-c:a","aac","-b:a","96k",
        "-movflags","+faststart","-y",str(temp),
    ]
    try:
        process=subprocess.run(
            cmd,stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,timeout=timeout_s,check=False,
        )
        if process.returncode!=0 or not temp.is_file():
            raise ValueError("Targeted FFmpeg context render failed; original unchanged")
        os.replace(temp,target)
        size=target.stat().st_size
        if size<100:
            raise ValueError("Targeted context rendered an empty video")
        preview=_probe_video(ffprobe,target)
        if not max(0.5,duration-4.0)<=preview<=duration+2.0:
            raise ValueError("Context preview duration differs substantially from requested")
        return {
            "videoPath":str(target),"videoBytes":size,
            "videoSha256":sha256(target.read_bytes()).hexdigest(),
            "renderedPreviewDurationSeconds":round(preview,3),
            "requestedSourceStartMs":plan["contextSourceStartMs"],
            "sourceAbsolutePtsVerified":False,
            "previewSeekingAndReencodingApproximate":True,
            "audioTrackIncludedIfPresent":True,
            "sourceMediaChanged":False,
        }
    finally:
        temp.unlink(missing_ok=True)


def execute(args)->dict:
    ws=args.workspace.expanduser().resolve(strict=True)
    pilot=_bounded_workspace_path(ws,args.pilot,existing=True)
    rows,plan_sha=load_pilot(ws,pilot)
    anchor=match_anchor(rows,args.source_prefix,args.anchor_start_ms,args.anchor_end_ms)
    asset,audit_meta,_=find_qualified_audit(
        ws,anchor["sourceSha256"],audit_file=args.audit)
    plan=plan_context(
        anchor,asset["durationMsFromCatalog"],
        before_s=args.before_s,after_s=args.after_s,max_s=args.max_s)
    plan["sourcePilot"]=str(pilot)
    plan["sourcePilotPlanSha256"]=plan_sha
    plan["auditProvenance"]=audit_meta
    plan["originalCataloguedLocationClass"]=asset["sourceLocationClass"]
    if not args.render_context:
        return {
            "status":"source_safe_run_context_plan_only",
            "sourceSha256":anchor["sourceSha256"],
            "anchorRecordId":anchor["recordId"],
            "previousPreviewSeconds":round((args.anchor_end_ms-args.anchor_start_ms)/1000,2),
            "sourceDurationSeconds":round(asset["durationMsFromCatalog"]/1000,2),
            "expandedStartSeconds":round(plan["contextSourceStartMs"]/1000,2),
            "expandedEndSeconds":round(plan["contextSourceEndMs"]/1000,2),
            "expandedDurationSeconds":round(plan["contextDurationMs"]/1000,2),
            "extensionAfterSeconds":round(plan["extensionAfterMs"]/1000,2),
            "priorSourceShaVerified":True,
            "freshOriginalReads":0,
            "actualRunBoundariesVerified":False,
            "nextStep":"Run with --render-context --allow-mounted-ssd --trusted-source-root to inspect actual line"
        }
    dest=_bounded_workspace_path(ws,args.output,existing=False)
    if (dest.exists() or dest.is_symlink() or dest==pilot
            or dest.is_relative_to(pilot)):
        raise ValueError("Use a new private reports/ directory for expanded context")
    # Read ONLY the one specifically approved source, never the archive.
    original=_verified_source_for_expansion(ws,asset,args,plan)
    dest.mkdir(parents=True,mode=0o700,exist_ok=False)
    (dest/"media").mkdir(mode=0o700)
    rendered=_render_one_context(
        original,plan,dest,asset["durationMsFromCatalog"]/1000)
    latest=original.stat()
    if latest.st_size!=asset["sourceBytesFromCatalog"]:
        raise ValueError("Camera original size changed during preview extraction")
    plan["previewRender"]=rendered
    save_json(dest/"run-context-plan.json",plan)
    save_json(dest/"owner-annotation-template.json",owner_annotation_template(plan))
    (dest/"run-review.html").write_text(page(plan),encoding="utf-8")
    return {
        "status":"long_single_source_context_preview_ready_not_an_auto_detected_run",
        "originalSourceSHAReverified":True,
        "sourceAssetSha256":plan["sourceAssetSha256"],
        "original20SecondAnchor":[plan["anchorSourceStartMs"],plan["anchorSourceEndMs"]],
        "extendedSourceSpan":[plan["contextSourceStartMs"],plan["contextSourceEndMs"]],
        "sourceMediaMutations":0,"externalUploads":0,
        "ownerRunBoundariesDetected":False,
        "reviewPage":str(dest/"run-review.html"),
        "runContextPlan":str(dest/"run-context-plan.json"),
        "ownerAnnotationTemplate":str(dest/"owner-annotation-template.json"),
    }


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace",type=Path,required=True)
    p.add_argument("--pilot",type=Path,required=True)
    p.add_argument("--source-prefix",required=True)
    p.add_argument("--anchor-start-ms",type=int,required=True)
    p.add_argument("--anchor-end-ms",type=int,required=True)
    p.add_argument("--audit",type=Path,help="Optional exact frozen audit path")
    p.add_argument("--before-s",type=int,default=40)
    p.add_argument("--after-s",type=int,default=180)
    p.add_argument("--max-s",type=int,default=240)
    p.add_argument("--output",type=Path,
                   help="New private reports/ output, required with --render-context")
    p.add_argument("--render-context",action="store_true")
    p.add_argument("--trusted-source-root",type=Path)
    p.add_argument("--allow-mounted-ssd",action="store_true")
    args=p.parse_args()
    if args.render_context and args.output is None:
        p.error("--output required when rendering source context")
    try:
        print(json.dumps(execute(args),indent=2,sort_keys=True,allow_nan=False))
        return 0
    except (ValueError,TypeError,KeyError,OSError,
            subprocess.TimeoutExpired,json.JSONDecodeError) as exc:
        print("Ski Run Context refused safely: "+str(exc),file=sys.stderr)
        return 3


if __name__=="__main__":
    raise SystemExit(main())

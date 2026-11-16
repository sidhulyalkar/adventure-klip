#!/usr/bin/env python3
"""Plan multi-scale ski edits, optionally render from ONE verified local context proxy.

Never reads camera originals. Source-linked owner markers stay immutable.
Outputs are non-destructive draft clips with approximate time alignment.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from html import escape
import json
import os
from pathlib import Path
import subprocess
import sys

from adventure_intelligence.auto_scout_v2 import (
    _bounded_workspace_path, save_json,
)
from adventure_intelligence.ski_run_context import (
    VERSION as CONTEXT_VERSION, validate_owner_run,
)
from adventure_intelligence.ski_event_scaling import (
    VERSION, hints_template, plan_event_lines,
)
from adventure_intelligence.media import require_tool


def _read_json(path: Path, limit: int) -> tuple[dict, bytes]:
    path=path.expanduser()
    if path.is_symlink() or not path.is_file() or not 2<=path.stat().st_size<=limit:
        raise ValueError("Missing, symlinked or oversized run annotation input")
    raw=path.read_bytes()
    value=json.loads(raw)
    if not isinstance(value,dict):
        raise ValueError("Expected run annotation JSON object")
    return value, raw


def _probe(path: Path) -> float:
    ffprobe=require_tool("ffprobe")
    result=subprocess.run([
        ffprobe,"-v","error","-show_entries","format=duration",
        "-of","default=noprint_wrappers=1:nokey=1",str(path),
    ],capture_output=True,text=True,check=False,timeout=30)
    if result.returncode:
        raise ValueError("Verified context preview could not be probed")
    try:
        duration=float(result.stdout.strip())
    except ValueError as exc:
        raise ValueError("Invalid context preview duration") from exc
    if not 0.5 <= duration <= 900:
        raise ValueError("Context preview duration outside bounded limit")
    return duration


def _verify_context(context: Path) -> tuple[dict,Path]:
    plan,_=_read_json(context/"run-context-plan.json",2_000_000)
    if (plan.get("version")!=CONTEXT_VERSION
            or not isinstance(plan.get("previewRender"),dict)
            or plan["previewRender"].get("sourceMediaChanged") is not False):
        raise ValueError("Source context is not qualified for local edit preview")
    video=context/"media"/"run-context.mp4"
    checksum=plan["previewRender"].get("videoSha256")
    if (video.is_symlink() or not video.is_file()
            or not isinstance(checksum,str) or len(checksum)!=64
            or sha256(video.read_bytes()).hexdigest()!=checksum):
        raise ValueError("Context preview SHA does not match frozen source receipt")
    return plan,video


def _encode_segment(video:Path, target:Path, context_start:int,
                    a:int,b:int)->dict:
    if target.exists() or target.is_symlink():
        raise ValueError("Edit preview target already exists")
    ffmpeg=require_tool("ffmpeg")
    start_seconds=(a-context_start)/1000
    duration_seconds=(b-a)/1000
    if start_seconds<0 or not 0.3<=duration_seconds<=900:
        raise ValueError("Invalid bounded proxy excerpt")
    temp=target.with_name(target.stem+".partial.mp4")
    if temp.exists() or temp.is_symlink():
        raise ValueError("Partial preview output exists")
    try:
        result=subprocess.run([
            ffmpeg,"-nostdin","-hide_banner","-loglevel","error",
            "-ss",f"{start_seconds:.3f}","-i",str(video),
            "-t",f"{duration_seconds:.3f}",
            "-map","0:v:0","-map","0:a:0?",
            "-vf","fps=24,scale=960:-2:flags=bicubic,format=yuv420p",
            "-c:v","libx264","-preset","veryfast","-crf","27",
            "-c:a","aac","-ar","48000","-b:a","96k",
            "-movflags","+faststart",str(temp),
        ],capture_output=True,timeout=180,check=False)
        if result.returncode or not temp.is_file() or temp.stat().st_size<100:
            raise ValueError("Segment proxy re-encode failed")
        actual=_probe(temp)
        if not max(0.25,duration_seconds-1.5)<=actual<=duration_seconds+1.0:
            raise ValueError("Rendered segment duration does not match edit plan")
        os.replace(temp,target)
    finally:
        temp.unlink(missing_ok=True)
    return {
        "name":target.name,
        "sha256":sha256(target.read_bytes()).hexdigest(),
        "sourceStartMs":a,"sourceEndMs":b,
        "durationSeconds":round(actual,3),
        "sourceFramePtsVerified":False,
        "cameraOriginalRead":False,
    }


def _montage(previews: list[dict], media_dir: Path) -> dict | None:
    if len(previews)<2:
        return None
    ffmpeg=require_tool("ffmpeg")
    playlist=media_dir/"concat.list"
    target=media_dir/"balanced-montage.mp4"
    temp=media_dir/"balanced-montage.partial.mp4"
    if playlist.exists() or target.exists() or temp.exists():
        raise ValueError("Montage output already exists")
    # Generated, validated names only, no user strings in concat directives.
    for info in previews:
        name=info["name"]
        if not name.startswith("balanced-") or not name.endswith(".mp4") or "/" in name:
            raise ValueError("Unexpected private preview filename")
    playlist.write_text(
        "".join("file '"+p["name"]+"'\\n" for p in previews),
        encoding="utf-8",
    )
    try:
        result=subprocess.run([
            ffmpeg,"-nostdin","-hide_banner","-loglevel","error",
            "-f","concat","-safe","1","-i",playlist.name,
            "-c","copy","-movflags","+faststart",temp.name,
        ],cwd=media_dir,capture_output=True,timeout=120,check=False)
        if result.returncode or not temp.is_file() or temp.stat().st_size<100:
            raise ValueError("Montage preview concat failed; individual clips remain available")
        os.replace(temp,target)
    finally:
        temp.unlink(missing_ok=True)
        playlist.unlink(missing_ok=True)
    return {
        "name":target.name,"sha256":sha256(target.read_bytes()).hexdigest(),
        "durationSeconds":round(_probe(target),3),
        "crossfadesApplied":False,
        "status":"quick_review_sequence_not_master_render",
        "originalAudioIncludedIfAvailable":True,
    }


def _html(plan:dict,clips:list[dict],montage:dict|None)->str:
    items=[]
    balanced=plan["alternatives"]["balanced"]["groups"]
    for index, (group,video) in enumerate(zip(balanced,clips),1):
        features=", ".join(
            x["kind"]+" at "+f'{x["sourceMs"]/1000:.3f}s'
            for x in group["features"]
        )
        status=(
            f"Post-event footage shortfall: {group['postRollShortfallMs']/1000:.3f}s. "
            "Review full context before confirming a landing."
            if group["postRollShortfallMs"] else
            "Suggested boundaries need playback confirmation."
        )
        items.append(
            "<article><h2>"+escape(f"Part {index}: {group['role'].replace('_',' ')}")
            +"</h2><video controls preload='metadata' playsinline src='media/"
            +escape(video["name"])+"'></video><p>"+escape(features)
            +"</p><p>"+escape(status)+"</p><p>Source: "
            +escape(f'{group["sourceStartMs"]/1000:.3f}–{group["sourceEndMs"]/1000:.3f}s')
            +"</p></article>"
        )
    montage_section=(
        "<section><h2>Suggested main line → separate extra</h2>"
        "<p>Simple sequential preview only, without crossfades or artistic cut approval.</p>"
        "<video controls preload='metadata' src='media/"
        +escape(montage["name"])+"'></video></section>"
    ) if montage else ""
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; media-src 'self' file:; style-src 'unsafe-inline'; script-src 'none'; connect-src 'none'; img-src 'none'">
<title>Klip / Temporal Ski Line Edits</title><style>
:root{color-scheme:dark;background:#0e171e;color:#edf5f4;font:14px/1.5 system-ui}
body{max-width:1150px;margin:auto;padding:23px}h1{font-size:24px}
.note{padding:12px;background:#253942;border-left:3px solid #70b9ad}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
article,section{padding:14px;background:#1b2b33;border:1px solid #415760;border-radius:11px}
video{width:100%;background:black;border-radius:8px;aspect-ratio:16/9}
</style></head><body><h1>Klip / Event-scaled Ski Edits</h1>
<p class="note">Separate editable clips derived from one verified local proxy.
These are NOT verified trick boundaries, not native-quality masters, and
not evidence of landings. No original camera video was opened.</p>
<div class="grid">""" + "\n".join(items) + "</div>"+montage_section+"</body></html>"


def execute(args)->dict:
    ws=args.workspace.expanduser().resolve(strict=True)
    context=_bounded_workspace_path(ws,args.context_dir,existing=True)
    out=_bounded_workspace_path(ws,args.output,existing=False)
    if (out.exists() or out.is_symlink() or out==context
            or out.is_relative_to(context)):
        raise ValueError("Choose a new private output folder outside context")
    context_plan,video=_verify_context(context)
    review,review_bytes=_read_json(args.owner_json,200_000)
    validate_owner_run(review,context_plan)
    hints=hints_template()
    hints["forcedBreakAfterMs"]=args.force_break_after_ms
    hints["forcedJoinAfterMs"]=args.force_join_after_ms
    hints["secondaryFeatureMs"]=args.secondary_feature_ms
    hints["preRollMs"]=args.pre_roll_ms
    hints["postRollMs"]=args.post_roll_ms
    plan=plan_event_lines(review,context_plan,hints=hints)
    plan["ownerAnnotationSha256"]=sha256(review_bytes).hexdigest()
    plan["contextVideoSha256"]=context_plan["previewRender"]["videoSha256"]
    plan["ownerReviewMediaSource"]="verified_h264_context_proxy_only"
    plan["originalDJIVideoReads"]=0
    # Dry-run previews plan only; no new files or video decodes.
    if not args.render_preview:
        return {
            "status":"validated_multiscale_dry_run",
            "runSourceBoundsMs":plan["runSourceBoundsMs"],
            "cadenceBaselineMs":plan["shortGapCadenceBaselineMs"],
            "featureGapsMs":plan["consecutiveFeatureGapsMs"],
            "scales":{k:{
                "joinThresholdMs":v["cadenceJoinThresholdMs"],
                "groups":[[f["sourceMs"] for f in g["features"]] for g in v["groups"]],
            } for k,v in plan["alternatives"].items()},
            "balancedGroups":[
                {"role":g["role"],"sourceBounds":[g["sourceStartMs"],g["sourceEndMs"]],
                 "postRollShortfallMs":g["postRollShortfallMs"]}
                for g in plan["alternatives"]["balanced"]["groups"]
            ],
            "filesCreated":0,"sourceOriginalRead":False,
            "plannedOutput":str(out),
        }
    actual=_probe(video)
    original_context_ms=context_plan["contextSourceEndMs"]-context_plan["contextSourceStartMs"]
    if abs(actual*1000-original_context_ms)>1800:
        raise ValueError("Context preview duration no longer matches frozen plan")
    out.mkdir(parents=True,mode=0o700,exist_ok=False)
    media=out/"media";media.mkdir(mode=0o700)
    clips=[]
    for group in plan["alternatives"]["balanced"]["groups"]:
        filename=group["groupId"]+".mp4"
        clips.append(_encode_segment(
            video,media/filename,context_plan["contextSourceStartMs"],
            group["sourceStartMs"],group["sourceEndMs"],
        ))
    # Checked SHA before and after all edits, original not read.
    if sha256(video.read_bytes()).hexdigest()!=context_plan["previewRender"]["videoSha256"]:
        raise ValueError("Context preview changed during editing")
    assembly=_montage(clips,media)
    plan["renderedSegments"]=clips
    plan["montagePreview"]=assembly
    save_json(out/"temporal-edit-plan.json",plan)
    (out/"review.html").write_text(_html(plan,clips,assembly),encoding="utf-8")
    return {
        "status":"non_destructive_multi_scale_preview_ready",
        "output":str(out),"reviewPage":str(out/"review.html"),
        "balancedGroups":len(clips),"montageCreated":bool(assembly),
        "cadenceBaselineMs":plan["shortGapCadenceBaselineMs"],
        "shortClipLimitRemoved":True,
        "sourceOriginalRead":False,"masterRenderCreated":False,
        "modelInferenceCalls":0,
    }


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace",required=True,type=Path)
    p.add_argument("--context-dir",required=True,type=Path)
    p.add_argument("--owner-json",required=True,type=Path)
    p.add_argument("--output",required=True,type=Path)
    p.add_argument("--force-break-after-ms",type=int,nargs="*",default=[])
    p.add_argument("--force-join-after-ms",type=int,nargs="*",default=[])
    p.add_argument("--secondary-feature-ms",type=int,nargs="*",default=[])
    p.add_argument("--pre-roll-ms",type=int,default=3000)
    p.add_argument("--post-roll-ms",type=int,default=3000)
    p.add_argument("--render-preview",action="store_true")
    a=p.parse_args()
    try:
        print(json.dumps(execute(a),indent=2,sort_keys=True,allow_nan=False))
        return 0
    except (ValueError,KeyError,TypeError,OSError,json.JSONDecodeError,
            subprocess.TimeoutExpired) as exc:
        print("Temporal Ski Line planner blocked safely: "+str(exc),file=sys.stderr)
        return 3


if __name__=="__main__":
    raise SystemExit(main())

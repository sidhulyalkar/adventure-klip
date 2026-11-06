"""Render optional shareable *preview-resolution* reel MP4s from LOCAL proxies.

This is a controlled, checksum-verified derivative workflow. It never
touches original DJI/SD-card recordings or automatically includes
unreviewed sporting hypotheses in a human-approved highlight montage.
Codec/fps/geometry are normalized per unique clip with bounded CPU
parallelism before concat, preventing stream-copy discontinuities.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import subprocess
import sys

from .highlight_curation_pilot import _safe_dest
from .highlight_feedback_discovery import _safe_input
from .highlight_reel_studio import VERSION as STUDIO_VERSION, _read_local
from .highlight_reel_feedback import _verify_private_proxy
from .media import require_tool

VERSION="klip-local-highlight-reel-render-v1"
_CATEGORIES={"human-selected","nature","cinematic","personal","athletic","sport-scout"}
_HASH=re.compile(r"^[a-f0-9]{24}$")


def _normalize_video(ffmpeg:str,studio:Path,dest:Path,clip:dict)->tuple[str,bool,str|None]:
    rid=clip["clipId"]
    if not _HASH.fullmatch(rid) or not _verify_private_proxy(studio,clip):
        return rid,False,"local_media_unverified_or_changed"
    src=studio/"media"/(rid+".mp4")
    out=dest/(rid+".mp4")
    part=dest/("."+rid+".partial.mp4")
    duration=min(60.0,max(1.0,clip["durationMs"]/1000))
    # 960x540 normalized H.264 preview, no audio; not a source-resolution master.
    filtergraph=("scale=960:540:force_original_aspect_ratio=decrease,"
                 "pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p")
    cmd=[ffmpeg,"-hide_banner","-loglevel","error","-nostdin","-y",
         "-i",str(src),"-t",f"{duration:.3f}","-map","0:v:0",
         "-an","-sn","-vf",filtergraph,
         "-c:v","libx264","-preset","veryfast","-crf","23",
         "-threads","2","-video_track_timescale","90000",
         "-movflags","+faststart",str(part)]
    try:
        result=subprocess.run(cmd,stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE,timeout=180,check=False)
        if result.returncode:
            return rid,False,"ffmpeg_normalization_failed"
        if not part.is_file() or not 100<=part.stat().st_size<=300*1024*1024:
            return rid,False,"normalized_preview_missing_or_oversized"
        os.replace(part,out)
        return rid,True,None
    except (OSError,subprocess.TimeoutExpired):
        return rid,False,"normalization_io_or_timeout"
    finally:
        part.unlink(missing_ok=True)


def _concat(ffmpeg:str,dest:Path,reel_id:str,clip_ids:list[str])->tuple[bool,str|None]:
    if not clip_ids:
        return False,"empty_reel"
    if not all(_HASH.fullmatch(rid) for rid in clip_ids):
        return False,"invalid_clip_identity"
    # All relative concat list entries are constructed from validated hashes.
    listing=dest/("."+reel_id+".concat.txt")
    listing.write_text("".join(f"file 'normalized/{rid}.mp4'\n" for rid in clip_ids),
                       encoding="utf-8")
    part=dest/("."+reel_id+".partial.mp4")
    try:
        result=subprocess.run(
            [ffmpeg,"-hide_banner","-loglevel","error","-nostdin","-y",
             "-f","concat","-safe","1","-i",str(listing),
             "-c","copy","-movflags","+faststart",str(part)],
            stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
            timeout=150,check=False)
        if result.returncode:
            return False,"ffmpeg_concat_failed"
        if not part.is_file() or not 100<=part.stat().st_size<=2*1024*1024*1024:
            return False,"stitched_reel_missing_or_oversized"
        os.replace(part,dest/(reel_id+".mp4"))
        return True,None
    except (OSError,subprocess.TimeoutExpired):
        return False,"concat_io_or_timeout"
    finally:
        part.unlink(missing_ok=True)
        listing.unlink(missing_ok=True)


def render_local_reels(workspace:Path,studio_dir:Path,output_dir:Path,*,
                       reel_id:str="all",workers:int=2,allow_scout:bool=False)->dict:
    if type(workers) is not int or not 1<=workers<=2:
        raise ValueError("Video CPU encoding workers must be 1..2")
    if reel_id not in _CATEGORIES|{"all"}:
        raise ValueError("Unknown reel collection")
    if reel_id=="sport-scout" and not allow_scout:
        raise ValueError("Sport scouting video is unreviewed; supply --allow-scout to render it")
    root=Path(workspace).expanduser().resolve()
    studio=_safe_input(root,studio_dir)
    dest=_safe_dest(root,output_dir)
    manifest,manifest_sha=_read_local(studio/"reel-manifest.json",10_000_000)
    if (manifest.get("version")!=STUDIO_VERSION or
            not isinstance(manifest.get("reels"),list)
            or not isinstance(manifest.get("clips"),list)):
        raise ValueError("Expected frozen verified local Reel Studio manifest")
    clips={x["clipId"]:x for x in manifest["clips"]}
    if len(clips)!=len(manifest["clips"]):
        raise ValueError("Duplicate clip identity")
    all_reels={r["reelId"]:r for r in manifest["reels"]}
    if any(k not in _CATEGORIES for k in all_reels):
        raise ValueError("Unknown reel category in frozen manifest")
    if reel_id=="all":
        target=[x for x in manifest["reels"] if
                x["reelId"]!="sport-scout" or allow_scout]
    else:
        if reel_id not in all_reels:
            raise ValueError("Requested reel not in frozen Studio")
        target=[all_reels[reel_id]]
    for reel in target:
        if not isinstance(reel.get("clipIds"),list) or len(reel["clipIds"])>200:
            raise ValueError("Invalid or unbounded reel")
        for rid in reel["clipIds"]:
            if (rid not in clips or not clips[rid].get("videoUrl")
                    or reel["reelId"]!="sport-scout" and not clips[rid]["humanApproved"]):
                raise ValueError("Reel contains non-verified or non-approved material")
    ids=sorted({rid for reel in target for rid in reel["clipIds"]})
    # Fail before any work or folder creation on absent FFmpeg.
    ffmpeg=require_tool("ffmpeg")
    dest.mkdir(parents=True,mode=0o700,exist_ok=False)
    normalized=dest/"normalized"
    normalized.mkdir(mode=0o700)
    verified={}
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix="klip-reel-encode") as pool:
        futures={pool.submit(_normalize_video,ffmpeg,studio,normalized,clips[rid]):rid
                 for rid in ids}
        for task in as_completed(futures):
            rid,success,error=task.result()
            verified[rid]=(success,error)
            print(f"[reel-render] normalized {len(verified)}/{len(ids)} "
                  f"{rid[:10]} {'ready' if success else error}",
                  file=sys.stderr,flush=True)
    result=[]
    for reel in target:
        rids=reel["clipIds"]
        missing=[rid for rid in rids if not verified.get(rid,(False,None))[0]]
        if missing:
            result.append({
                "reelId":reel["reelId"],"status":"not_rendered_missing_or_invalid_proxy",
                "missingClipIds":missing,"output":None})
            continue
        ready,error=_concat(ffmpeg,dest,reel["reelId"],rids)
        result.append({
            "reelId":reel["reelId"],
            "status":"rendered_preview_mp4" if ready else
                     ("empty_not_rendered" if error=="empty_reel"
                      else "render_failed"),
            "missingClipIds":[],"output":
                str(dest/(reel["reelId"]+".mp4")) if ready else None,
            "error":error,
            "sourceClipIds":rids,
        })
    report={
        "version":VERSION,"studioManifestSha256":manifest_sha,
        "sourcePreviewOnly":True,"fullResolutionSourceRender":False,
        "audioPreserved":False,"uniformVideoGeometry":"960x540",
        "videoFps":30,"codec":"H.264","videoEncodingWorkers":workers,
        "unreviewedScoutingExplicitlyAuthorized":allow_scout,
        "normalizedClipsReady":sum(1 for ok,_ in verified.values() if ok),
        "normalizedClipsFailed":sum(1 for ok,_ in verified.values() if not ok),
        "reels":result,
        "originalVideoReads":0,"originalVideoDecodes":0,
        "sourceFilesModified":False,"filesDeleted":0,
        "modelTrainingPerformed":False,"externalUploads":0,
        "limitations":[
            "Only source-verified locally cached preview clips are used.",
            "Reels from human-Keep windows are subjective and may need improved source-time boundaries.",
            "This export is preview-resolution, no original audio, not a publishable quality master.",
            "No unreviewed sport clip enters a human-approved reel.",
        ]
    }
    report_file=dest/"render-report.json"
    with report_file.open("x",encoding="utf-8") as handle:
        json.dump(report,handle,indent=2,sort_keys=True,allow_nan=False)
        handle.write("\n")
    return {
        "status":"local_reel_render_finished",
        "report":str(report_file),
        "renderedReels":sum(r["status"]=="rendered_preview_mp4" for r in result),
        "emptyReels":sum(r["status"]=="empty_not_rendered" for r in result),
        "failedReels":sum(r["status"] in
            ("render_failed","not_rendered_missing_or_invalid_proxy") for r in result),
        "videoEncodingWorkers":workers,
        "originalSourceReads":0,"sourceFilesModified":False,
    }

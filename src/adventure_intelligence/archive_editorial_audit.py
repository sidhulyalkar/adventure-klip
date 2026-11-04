"""Cache-only action intensity and recording-quality audit of frozen review intervals.

This does NOT estimate absolute skiing speed, confirm a jump or wildlife event,
infer roll from pixels, or authorize deleting an original. Missing features
abstain; human recording-defect tags and model suggestions stay separate.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from contextlib import closing
from html import escape
import json
import math
from pathlib import Path
from statistics import median

from .acoustics import AUDIO_VERSION
from .archive_pilot import _open_catalog
from .artifacts import read_artifact
from .benchmark_feedback import load_benchmark, benchmark_feedback_status
from .editorial_quality import raw_motion_for_asset
from .studio_preview_judge import preview_paths, _check_cached, valid_local_model
from .adaptive_judge_v2 import cached_adaptive_judgment, context_hint

VERSION="archive-editorial-evidence-v1"


def _finite(value):
    return type(value) in (float,int) and math.isfinite(value)


def _rank(value,reference):
    if value is None or len(reference)<12:
        return None
    return round(bisect_right(sorted(reference),value)/len(reference),4)


def _series_window(series, start, end, key):
    out=[]
    for row in series:
        t=row.get("t_ms")
        value=row.get(key)
        if (_finite(t) and start<=t<end and _finite(value)
                and value>=0):
            out.append(float(value))
    return out


def action_evidence(motion:list[dict],audio:list[dict]|None,
                    start_ms:int,end_ms:int,*,activity:str,
                    scene:str|None=None)->dict:
    """Use relative within-recording cues, never a false absolute speed."""
    motion_all=_series_window(motion,0,2**60,"change")
    motion_here=_series_window(motion,start_ms,end_ms,"change")
    motion_pct=_rank(median(motion_here) if len(motion_here)>=4 else None,motion_all)
    acoustic_all=_series_window(audio or [],0,2**60,"rms")
    acoustic_here=_series_window(audio or [],start_ms,end_ms,"rms")
    acoustic_pct=_rank(median(acoustic_here) if len(acoustic_here)>=4 else None,
                       acoustic_all)
    valid_audio=[r for r in (audio or [])
                 if _finite(r.get("t_ms")) and start_ms<=r["t_ms"]<end_ms]
    clip=[float(r["clippingFraction"]) for r in valid_audio
          if _finite(r.get("clippingFraction"))]
    low=[float(r["lowBandFraction"]) for r in valid_audio
         if _finite(r.get("lowBandFraction"))]
    clipping=median(clip) if clip else None
    wind_like=(median(low)>=.35 if len(low)>=4 else None)
    contaminated=clipping is not None and clipping>=.02
    # These are distinct observations, not a calibrated estimate of velocity.
    corroborated=(motion_pct is not None and motion_pct>=.7
                  and acoustic_pct is not None and acoustic_pct>=.7
                  and wind_like is True and not contaminated)
    context_supported=scene in ("ski_descent","ski_terrain_feature")
    is_ski=activity=="ski"
    # Allow editorial timing proposals, not an event claim, only with
    # evidence of a descent, good within-source motion, and secondary sound.
    recommend_longer=bool(is_ski and context_supported and corroborated)
    return {
        "visualMotionPercentileWithinRecording":motion_pct,
        "audioRmsPercentileWithinRecording":acoustic_pct,
        "windLikeLowBandFractionPresent":wind_like,
        "audioClippingLikely":contaminated if clipping is not None else None,
        "motionSamplesWithinWindow":len(motion_here),
        "audioSamplesWithinWindow":len(acoustic_here),
        "sceneContextFromThreeStills":scene,
        "candidateHighActionWithCorroboration":corroborated if
            motion_pct is not None and acoustic_pct is not None and wind_like is not None
            else None,
        "longerSkiWindowSuggested":recommend_longer,
        "speedMph":None,
        "speedKph":None,
        "interpretation":"Camera motion and wind-like noise are not ground speed or confirmed technique",
        "limitations":[
            "Within-recording percentiles cannot compare different mounts or outings",
            "Wind direction, microphone gain and processing confound RMS",
            "Strong image motion may be camera shake, a pan, an impact or scene change",
        ],
    }


def extended_window(start:int,end:int,duration:int,*,desired_ms:int=26000)->dict:
    if not 0<=start<end<=duration:
        raise ValueError("Invalid recorded interval")
    if type(desired_ms) is not int or not 12000<=desired_ms<=40000:
        raise ValueError("Desired ski shot must be 12..40 seconds")
    width=min(desired_ms,duration)
    center=(start+end)//2
    new_start=max(0,min(duration-width,center-width//2))
    return {"startMs":new_start,"endMs":new_start+width,
            "note":"Edit proposal only; continuous action must be reviewed in playback"}


def audit_editorial_evidence(workspace:Path,assignments:Path,*,model:str)->dict:
    if not valid_local_model(model):
        raise ValueError("A named local model cache key is required")
    root=Path(workspace).expanduser().resolve()
    bench=load_benchmark(root,assignments)
    reviews=benchmark_feedback_status(root,assignments)["reviewEntries"]
    clip_rows=[]
    source_review_queue={}
    cache={}
    with closing(_open_catalog(root)) as db:
        for entry in bench["assignments"].values():
            aid=entry["assetId"]
            if aid not in cache:
                asset=db.execute("SELECT id,activity,duration FROM assets WHERE id=?",
                                 (aid,)).fetchone()
                if asset is None:
                    raise ValueError("Frozen benchmark asset missing from catalog")
                audio_doc=read_artifact(db,root,aid,AUDIO_VERSION)
                cache[aid]={"duration":int(float(asset["duration"] or 0)*1000),
                            "motion":raw_motion_for_asset(db,root,aid),
                            "audio":audio_doc["series"] if audio_doc else None}
            facts=cache[aid]
            rid=entry["reviewId"]
            human=reviews.get(rid,{})
            flags=human.get("qualityFlags",[])
            if not isinstance(flags,list):
                flags=[]
            manual_delete=human.get("deletionCandidate") is True
            # Validate the source-verification receipt before trusting the
            # adaptive visual cache for editorial hypotheses.
            preview,receipt=preview_paths(root,aid,entry["startMs"],entry["endMs"])
            verified=_check_cached(preview,receipt,aid=aid,
                                   start_ms=entry["startMs"],end_ms=entry["endMs"])
            judge=(cached_adaptive_judgment(preview,model=model,
                    context=context_hint(entry)) if verified else None)
            scene=judge.get("scene") if judge else None
            evidence=action_evidence(facts["motion"],facts["audio"],
                 entry["startMs"],entry["endMs"],
                 activity=entry["selectionGroup"],scene=scene)
            proposed=(extended_window(entry["startMs"],entry["endMs"],
                facts["duration"]) if evidence["longerSkiWindowSuggested"]
                and facts["duration"]>=entry["endMs"] else None)
            if manual_delete and human.get("reviewDecision")=="skip" and flags:
                source_review_queue.setdefault(aid,[]).append({
                    "reviewId":rid,"startMs":entry["startMs"],
                    "endMs":entry["endMs"],"humanQualityFlags":flags})
            clip_rows.append({
                "reviewId":rid,"assetId":aid,"filename":entry["basename"],
                "selectionGroup":entry["selectionGroup"],"role":entry["role"],
                "startMs":entry["startMs"],"endMs":entry["endMs"],
                "humanReview":human.get("reviewDecision"),
                "humanQualityFlags":flags,
                "humanDeletionReviewCandidate":manual_delete,
                "adaptiveSuggestion":judge.get("recommendation") if judge else None,
                "adaptiveSceneHypothesis":scene,
                "visualEvidenceStatus":"verified_cache" if judge else "unknown",
                "compositionStatus":"human_reported" if flags else "unassessed",
                "cameraRollDegrees":None,
                "subjectCenterOffset":None,
                "qualityTagsAreFromHumanOnly":True,
                "actionEvidence":evidence,
                "suggestedLongerWindow":proposed,
                "originalSourceDeletionAuthorized":False,
            })
    queue=[{
        "assetId":aid,"intervals":intervals,
        "status":"human_flagged_pending_full_source_review",
        "sourceCoverageVerified":False,
        "hasVerifiedFullRecordingDefect":False,
        "mayDeleteOriginal":False,
        "requiredNextAction":"Review entire source, other saved moments, backups and exact SHA before deciding outside Klip",
    } for aid,intervals in sorted(source_review_queue.items())]
    counts=Counter(row["selectionGroup"] for row in clip_rows)
    return {
        "version":VERSION,"benchmarkId":bench["benchmarkId"],"modelCacheKey":model,
        "scope":"frozen_assignment_intervals_only_not_whole_archive",
        "reviewedHumanAlready":len(reviews),
        "intervalCount":len(clip_rows),
        "activityCounts":dict(sorted(counts.items())),
        "longerSkiWindowsProposed":sum(x["suggestedLongerWindow"] is not None
                                      for x in clip_rows),
        "sourcesFlaggedForDeletionReview":len(queue),
        "sourceDeletionReviewQueue":queue,
        "intervals":clip_rows,
        "originalVideosRead":0,"inferenceRequests":0,"originalVideosModified":False,
        "humanReviewsWritten":0,"filesDeleted":0,
        "estimatedAbsoluteSpeed":False,
        "interpretation":[
            "All scores are source-relative proxy diagnostics, not ground speed",
            "Low scene coverage or missing motion/audio/vision evidence must abstain",
            "Unverified scene hypotheses cannot certify a completed ski trick or wildlife event",
            "An off-center or rotated human-reviewed clip is not proof a whole recording is disposable",
            "Only explicit human Skip with a defect marks an interval for full-source deletion review",
            "Never delete original footage automatically, including when every sampled interval looks bad",
        ],
    }


def write_editorial_audit(workspace:Path,assignments:Path,output_dir:Path,*,
                          model:str)->dict:
    root=Path(workspace).expanduser().resolve()
    folder=Path(output_dir).expanduser().resolve()
    if (folder==root or not folder.is_relative_to(root)
            or folder.relative_to(root).parts[0] not in ("reports","exports")
            or folder.exists()):
        raise ValueError("Use a new reports/ or exports/ directory inside the workspace")
    report=audit_editorial_evidence(root,assignments,model=model)
    folder.mkdir(mode=0o700,parents=True,exist_ok=False)
    with (folder/"audit.json").open("x",encoding="utf-8") as handle:
        json.dump(report,handle,indent=2,sort_keys=True,allow_nan=False)
        handle.write("\n")
    rows=[]
    for item in report["intervals"]:
        action=item["actionEvidence"]
        cells=(item["filename"],item["selectionGroup"],item["humanReview"],
               item["humanQualityFlags"],item["adaptiveSceneHypothesis"],
               action["visualMotionPercentileWithinRecording"],
               action["audioRmsPercentileWithinRecording"],
               action["longerSkiWindowSuggested"],
               item["humanDeletionReviewCandidate"])
        rows.append("<tr>"+"".join(
            "<td>"+escape("unknown" if v is None else str(v))+"</td>"
            for v in cells)+"</tr>")
    html=(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Adventure Klip · Editorial evidence</title>'
        '<style>body{font:14px system-ui;background:#101923;color:#edf2f5;'
        'max-width:1300px;margin:28px auto;padding:0 16px;line-height:1.5}'
        'h1{font-size:1.6rem}table{border-collapse:collapse;width:100%}'
        'th,td{padding:9px;border-bottom:1px solid #344557;text-align:left}'
        'p{color:#b7c4ce}th{color:#f4b6a6}</style></head><body>'
        '<h1>Cached editorial evidence, not final highlights</h1>'
        '<p>Camera roll and subject framing require actual human observations. '
        'Relative audio loudness is not speed. No original videos were decoded '
        'or deleted. A deletion candidate is never a delete instruction.</p>'
        '<table><thead><tr><th>Video</th><th>Sport</th><th>Human</th>'
        '<th>Defects</th><th>Scene hypothesis</th>'
        '<th>Relative motion</th><th>Relative audio</th>'
        '<th>Longer ski?</th><th>Deletion review?</th></tr></thead><tbody>'
        +''.join(rows)+'</tbody></table></body></html>'
    )
    with (folder/"index.html").open("x",encoding="utf-8") as handle:
        handle.write(html)
    return {"status":"cache_only_editorial_audit",
            "report":str(folder/"audit.json"),"html":str(folder/"index.html"),
            "intervalCount":report["intervalCount"],
            "longerSkiWindowsProposed":report["longerSkiWindowsProposed"],
            "sourcesFlaggedForDeletionReview":report["sourcesFlaggedForDeletionReview"],
            "sourceVideoReads":0,"inferenceRequests":0,"filesDeleted":0}

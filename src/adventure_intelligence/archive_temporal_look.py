"""Budgeted second-look runner over existing, source-verified H264 previews.

It never opens original videos, modifies reviews, or generates proxies:
a missing verified proxy is a reason to abstain and request an explicit
existing preview-preparation step, not a reason to read the external SSD.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import subprocess
import time

from .benchmark_feedback import load_benchmark,benchmark_feedback_status
from .archive_local_triage import _sample_unreviewed
from .studio_preview_judge import preview_paths,_check_cached,valid_local_model
from .adaptive_judge_v2 import cached_adaptive_judgment,context_hint
from .temporal_second_look import should_investigate,cached_second_look,second_look

VERSION="archive-temporal-second-look-v1"


def inspect_temporal_candidates(workspace:Path,assignments:Path,*,model:str,
                                limit:int=4,only_review_id:str|None=None,
                                run:bool=False,
                                exclude_review_ids:tuple[str,...]=())->dict:
    if not valid_local_model(model):
        raise ValueError("Explicit installed local Ollama model required")
    if type(limit) is not int or not 1<=limit<=8:
        raise ValueError("Second-look batch is capped at 8 review intervals")
    root=Path(workspace).expanduser().resolve()
    bench=load_benchmark(root,assignments)
    feedback=benchmark_feedback_status(root,assignments)
    seen=set(feedback["reviewEntries"])
    all_rows=list(bench["assignments"].values())
    excluded=set(exclude_review_ids)
    if excluded.difference(bench["assignments"]):
        raise ValueError("Excluded review ID is not in the frozen assignment set")
    if only_review_id is not None and only_review_id not in bench["assignments"]:
        raise ValueError("Unknown frozen review ID")
    if only_review_id is not None and only_review_id in excluded:
        raise ValueError("A targeted review cannot also be excluded")
    scan=([bench["assignments"][only_review_id]] if only_review_id else
          [row for row in all_rows if row["reviewId"] not in excluded])
    eligible=[]
    awaiting_first=0
    missing_preview=0
    already_reviewed=len(seen)
    first_was_definitive=0
    for item in scan:
        if item["reviewId"] in seen:
            continue
        path,receipt=preview_paths(root,item["assetId"],item["startMs"],item["endMs"])
        context=context_hint(item)
        if not _check_cached(path,receipt,aid=item["assetId"],
                             start_ms=item["startMs"],end_ms=item["endMs"]):
            missing_preview+=1
            continue
        first=cached_adaptive_judgment(path,model=model,context=context)
        if first is None:
            awaiting_first+=1
            continue
        if not should_investigate(first):
            first_was_definitive+=1
            continue
        eligible.append({**item,"_preview":path,"_context":context,"_first":first})
    # Preserve a systematic control whenever eligible controls exist.
    if only_review_id is not None:
        selected=eligible[:1]
    else:
        selected=_sample_unreviewed(eligible,set(),limit)
    report={
        "version":VERSION,"mode":"run" if run else "dry_run",
        "model":model,"sourceVideoReads":0,"newFullSourceShaPasses":0,
        "originalMediaModified":False,"humanReviewsModified":False,
        "externalUploads":0,"humanApprovalRequired":True,
        "eligibleUnreviewed":len(eligible),"selected":len(selected),
        "reviewedHumanAlready":already_reviewed,
        "excludedFromThisRun":len(excluded-seen),
        "exclusionsAreOnlyForThisRun":True,
        "missingVerifiedPreviews":missing_preview,
        "awaitingAdaptiveV2":awaiting_first,
        "firstPassNotFlaggedForInvestigation":first_was_definitive,
        "selectionRoles":dict(sorted(Counter(a["role"] for a in selected).items())),
        "items":[],
        "limitations":[
            "Seven time-ordered stills do not prove continuous motion, a landing, a fall, a species or animal count.",
            "No missing frame/preview is treated as a negative event.",
            "Human approvals and signed source plans remain untouched.",
            "Model outputs are provisional; model tag digests are not pinned.",
        ],
    }
    if not run:
        for item in selected:
            report["items"].append({
                "reviewId":item["reviewId"],"filename":item["basename"],
                "contextHint":item["_context"],"role":item["role"],
                "firstPassRecommendation":item["_first"]["recommendation"],
                "firstPassPriority":item["_first"].get("investigationScore"),
                "alreadyCached":bool(cached_second_look(item["_preview"],
                    model=model,context=item["_context"],first=item["_first"])),
            })
        return report
    failures=0
    newly_judged=0
    reused=0
    for item in selected:
        start=time.monotonic()
        stage="verified_preview_recheck"
        try:
            # Validate the content-addressed receipt *again* before inference.
            p,receipt=preview_paths(root,item["assetId"],
                                    item["startMs"],item["endMs"])
            if not _check_cached(p,receipt,aid=item["assetId"],
                                 start_ms=item["startMs"],end_ms=item["endMs"]):
                raise RuntimeError("The verified preview disappeared or changed")
            stage="seven_frame_sampling_or_inference"
            answer=second_look(p,model=model,context=item["_context"],
                               first=item["_first"])
            newly_judged+=not answer["cached"]
            reused+=bool(answer["cached"])
            report["items"].append({
                "reviewId":item["reviewId"],"filename":item["basename"],
                "role":item["role"],"status":"suggested",
                "recommendation":answer["recommendation"],
                "firstPassRecommendation":item["_first"]["recommendation"],
                "recommendationChanged":answer["firstPassComparison"]["recommendationChanged"],
                "needsContinuousVideoReview":answer["needsContinuousVideoReview"],
                "frameEvidence":answer["frameEvidence"],
                "summary":answer["summary"],"reason":answer["reason"],
                "cached":answer["cached"],
                "sampleTimesMs":answer["sampleTimesMs"],
                "elapsedSeconds":round(time.monotonic()-start,3),
                "humanApprovalRequired":True,
            })
        except (RuntimeError,ValueError,OSError,subprocess.TimeoutExpired) as exc:
            failures+=1
            # Report bounded categories, not private paths or raw model text.
            message=str(exc).lower()
            if stage=="verified_preview_recheck":
                code="preview_cache_validation_error"
            elif isinstance(exc,subprocess.TimeoutExpired):
                code="frame_sampling_timeout"
            elif "ollama timed out" in message:
                code="local_model_timeout"
            elif "frame" in message or "sample" in message or "duration" in message:
                code="frame_sampling_error"
            elif "json" in message or "response" in message or "assessment" in message:
                code="model_response_error"
            else:
                code="local_model_error"
            diagnostic={
                "preview_cache_validation_error":"Verified short-preview receipt changed or disappeared.",
                "frame_sampling_timeout":"FFmpeg timed out while sampling seven proxy frames.",
                "local_model_timeout":"Ollama vision inference timed out; inspect memory and current model load.",
                "frame_sampling_error":"A required frame could not be read from the verified proxy.",
                "model_response_error":"Local model response failed strict JSON validation.",
                "local_model_error":"Seven-frame local inference failed; review this interval manually.",
            }[code]
            report["items"].append({
                "reviewId":item["reviewId"],"filename":item["basename"],
                "role":item["role"],"status":"failed",
                "errorType":type(exc).__name__,
                "failureStage":stage,"errorCode":code,
                "diagnostic":diagnostic,
                "elapsedSeconds":round(time.monotonic()-start,3),
                "humanApprovalRequired":True,
            })
    report["newLocalModelCalls"]=newly_judged
    report["cachedSecondLooksReused"]=reused
    report["failed"]=failures
    report["status"]="partial_failure" if failures else "completed"
    return report

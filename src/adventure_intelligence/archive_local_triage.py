"""Bounded offline local-only AI pre-triage of frozen review assignments.

Important: the source videos must stay private. No network outside the
literal Ollama loopback address, no automatic label writes or export.
Systematic controls remain in the audit pool, and all AI judgments are
provisional three-still observations, not video-understanding ground truth.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import subprocess
import time

from .benchmark_feedback import load_benchmark, benchmark_feedback_status
from .studio_preview_judge import (
    valid_local_model,preview_paths,_check_cached,
    cached_judgment,generate_preview,local_judgment,
)
from .studio import SourceStream
from .adaptive_judge_v2 import (context_hint, adaptive_judgment,
    cached_adaptive_judgment)

VERSION="archive-local-triage-v1"


def _sample_unreviewed(rows:list[dict],reviewed:set[str],limit:int):
    groups=("snorkeling","ski","bike","unresolved")
    buckets={}
    for sport in groups:
        for role in ("candidate_hypothesis","control_systematic"):
            buckets[(sport,role)]=[
                r for r in rows
                if r["selectionGroup"]==sport and r["role"]==role
                and r["reviewId"] not in reviewed
            ]
    selected=[]
    # Prefer event candidates but reserve at least one control probe per
    # four selected slots when available. Round robin sport strata.
    while len(selected)<limit and any(buckets.values()):
        want_control=(len(selected)%4==3)
        roles=(("control_systematic","candidate_hypothesis")
               if want_control else
               ("candidate_hypothesis","control_systematic"))
        for role in roles:
            choices=[key for key in buckets if key[1]==role and buckets[key]]
            if choices:
                sport_key=min(choices,key=lambda k:(
                    sum(r["selectionGroup"]==k[0] for r in selected),
                    groups.index(k[0])))
                selected.append(buckets[sport_key].pop(0))
                break
        else:
            break
    return selected


def run_local_triage(workspace:Path,assignments:Path,*,model:str,
                     limit:int=8,run:bool=False,policy:str="legacy",
                     only_review_id:str|None=None,
                     exclude_review_ids:tuple[str,...]=())->dict:
    if not valid_local_model(model):
        raise ValueError("Specify a valid installed local vision model name")
    if type(limit) is not int or not 1<=limit<=24:
        raise ValueError("Pilot limit must be between 1 and 24")
    if policy not in ("legacy","adaptive-v2"):
        raise ValueError("Judge policy must be legacy or adaptive-v2")
    root=Path(workspace).expanduser().resolve()
    benchmark=load_benchmark(root,assignments)
    feedback=benchmark_feedback_status(root,assignments)
    excluded=set(exclude_review_ids)
    if excluded.difference(benchmark["assignments"]):
        raise ValueError("Excluded review ID is not in the frozen assignment set")
    if only_review_id is not None and only_review_id in excluded:
        raise ValueError("A targeted review cannot also be excluded")
    if only_review_id is not None:
        if only_review_id not in benchmark["assignments"]:
            raise ValueError("Requested review ID is not in the frozen assignment set")
        selected=([benchmark["assignments"][only_review_id]]
                  if only_review_id not in feedback["reviewEntries"] else [])
    else:
        candidates=[row for row in benchmark["assignments"].values()
                    if row["reviewId"] not in excluded]
        selected=_sample_unreviewed(candidates,
                                    set(feedback["reviewEntries"]),limit)
    result={"version":VERSION,"judgePolicy":policy,
            "mode":"local_inference" if run else "dry_run",
            "model":model,"selected":len(selected),
            "reviewedHumanAlready":len(feedback["reviewEntries"]),
            "excludedFromThisRun":len(excluded-set(feedback["reviewEntries"])),
            "exclusionsAreOnlyForThisRun":True,
            "selectionRoles":dict(sorted(Counter(x["role"] for x in selected).items())),
            "items":[],"humanLabelsWritten":0,"externalUploads":0,
            "estimatedCost":"unknown; local CPU/GPU inference and source reads",
            "note":"Three-frame triage only; controls remain available and human approval is required"}
    if not run:
        result["items"]=[{"reviewId":x["reviewId"],"filename":x["basename"],
                          "selectionGroup":x["selectionGroup"],"role":x["role"]}
                         for x in selected]
        return result
    source=SourceStream(root)
    failed=0
    for item in selected:
        aid,start,end=item["assetId"],item["startMs"],item["endMs"]
        clip,manifest=preview_paths(root,aid,start,end)
        stage="cached_judge_lookup"
        start_time=time.perf_counter()
        try:
            context=context_hint(item)
            if policy=="adaptive-v2":
                cached=cached_adaptive_judgment(clip,model=model,context=context)
            else:
                cached=cached_judgment(clip,model=model)
            if cached is not None:
                suggestion=cached
            else:
                # Reuse an earlier source-SHA-verified short H.264 derivative
                # for v2 rejudging without hashing a multi-gigabyte original.
                # The preview carries its original source hash in its key;
                # its checksum/provenance receipt must validate first.
                stage="cached_preview_validation"
                reusable=_check_cached(clip,manifest,aid=aid,
                                       start_ms=start,end_ms=end)
                if not reusable:
                    stage="source_verification_and_preview"
                    generate_preview(root,source,item)
                stage="local_judge_sampling_or_response"
                suggestion=(adaptive_judgment(clip,model=model,context=context)
                            if policy=="adaptive-v2" else
                            local_judgment(clip,model=model))
            result["items"].append({
                "reviewId":item["reviewId"],"filename":item["basename"],
                "selectionGroup":item["selectionGroup"],"role":item["role"],
                "status":"suggested","recommendation":suggestion["recommendation"],
                "summary":suggestion["summary"],"cached":suggestion["cached"],
                "contextHint":context,
                "promptFingerprint":suggestion.get("promptFingerprint"),
                "decisionRationale":suggestion.get("reason"),
                "uncertaintyReason":suggestion.get("uncertaintyReason"),
                "policyOverrides":suggestion.get("policyOverrides",[]),
                "investigationScore":suggestion.get("investigationScore"),
                "assessedDimensions":suggestion.get("assessedDimensions",0),
                "scoreMeaning":suggestion.get("scoreMeaning","unscored_legacy"),
                "elapsedSeconds":round(time.perf_counter()-start_time,3),
                "requiresHumanReview":True})
        except (RuntimeError,ValueError,OSError,subprocess.TimeoutExpired) as exc:
            failed+=1
            message=str(exc)
            # Avoid user source paths, model prompts or private raw frames in
            # machine-readable diagnostics. Narrow codes guide safe retry.
            if stage=="cached_judge_lookup":
                code="cache_validation_error"
            elif stage=="cached_preview_validation":
                code="preview_cache_validation_error"
            elif stage=="source_verification_and_preview":
                code="source_or_preview_error"
            elif isinstance(exc,subprocess.TimeoutExpired):
                code="judge_frame_sampling_timeout"
            elif "ollama timed out" in message.lower():
                code="judge_inference_timeout"
            elif "sample" in message.lower() or "frame" in message.lower():
                code="judge_frame_sampling_error"
            elif "json" in message.lower() or "response" in message.lower():
                code="judge_response_validation_error"
            else:
                code="judge_inference_error"
            safe_message={
                "cache_validation_error":"Could not validate the cached suggestion.",
                "preview_cache_validation_error":"A saved preview failed its checksum or source receipt.",
                "source_or_preview_error":"Original source verification or short preview preparation failed.",
                "judge_frame_sampling_error":"The local model's preview JPEG sampling failed.",
                "judge_frame_sampling_timeout":"FFmpeg frame sampling timed out on the short local preview.",
                "judge_inference_timeout":"Local Ollama vision inference timed out; inspect model load and memory pressure.",
                "judge_response_validation_error":"The local model returned an invalid or unsupported response.",
                "judge_inference_error":"Local model inference failed; inspect the local Ollama service.",
            }[code]
            result["items"].append({
                "reviewId":item["reviewId"],"filename":item["basename"],
                "selectionGroup":item["selectionGroup"],"role":item["role"],
                "status":"failed","errorType":type(exc).__name__,
                "failureStage":stage,"errorCode":code,
                "diagnostic":safe_message,
                "elapsedSeconds":round(time.perf_counter()-start_time,3),
                "requiresHumanReview":True})
    result["failed"]=failed
    result["status"]="partial_failure" if failed else "completed"
    result["confirmedEvents"]=0
    return result

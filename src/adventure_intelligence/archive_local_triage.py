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

from .benchmark_feedback import load_benchmark, benchmark_feedback_status
from .studio_preview_judge import (
    valid_local_model,preview_paths,cached_judgment,generate_preview,local_judgment,
)
from .studio import SourceStream

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
                     limit:int=8,run:bool=False)->dict:
    if not valid_local_model(model):
        raise ValueError("Specify a valid installed local vision model name")
    if type(limit) is not int or not 1<=limit<=24:
        raise ValueError("Pilot limit must be between 1 and 24")
    root=Path(workspace).expanduser().resolve()
    benchmark=load_benchmark(root,assignments)
    feedback=benchmark_feedback_status(root,assignments)
    selected=_sample_unreviewed(list(benchmark["assignments"].values()),
                                 set(feedback["reviewEntries"]),limit)
    result={"version":VERSION,"mode":"local_inference" if run else "dry_run",
            "model":model,"selected":len(selected),
            "reviewedHumanAlready":len(feedback["reviewEntries"]),
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
        clip,_manifest=preview_paths(root,aid,start,end)
        try:
            cached=cached_judgment(clip,model=model)
            if cached is not None:
                suggestion=cached
            else:
                generate_preview(root,source,item)
                suggestion=local_judgment(clip,model=model)
            result["items"].append({
                "reviewId":item["reviewId"],"filename":item["basename"],
                "selectionGroup":item["selectionGroup"],"role":item["role"],
                "status":"suggested","recommendation":suggestion["recommendation"],
                "summary":suggestion["summary"],"cached":suggestion["cached"],
                "requiresHumanReview":True})
        except (RuntimeError,ValueError,OSError) as exc:
            failed+=1
            result["items"].append({
                "reviewId":item["reviewId"],"filename":item["basename"],
                "selectionGroup":item["selectionGroup"],"role":item["role"],
                "status":"failed","errorType":type(exc).__name__,
                "requiresHumanReview":True})
    result["failed"]=failed
    result["status"]="partial_failure" if failed else "completed"
    result["confirmedEvents"]=0
    return result

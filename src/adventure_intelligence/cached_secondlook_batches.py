"""Checkpointed, serial local Ollama second-look batches from cached previews.

Each batch is <=8 independently receipt-verified short proxy intervals.
No source path resolution, SD-card reads, original hashing, new proxies,
human approval, trainable ground truth or automated source deletion.
"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
from collections import Counter
import json
import os

from .highlight_feedback_discovery import _safe_input
from .highlight_curation_pilot import _safe_dest
from .archive_temporal_look import inspect_temporal_candidates

VERSION="klip-cached-secondlook-overnight-v1"


def _write_atomic(path:Path,data:dict):
    tmp=path.with_suffix(path.suffix+".tmp")
    with tmp.open("x",encoding="utf-8") as f:
        json.dump(data,f,indent=2,sort_keys=True,allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp,path)


def run_cached_secondlook_batches(workspace:Path,assignments:Path,output_dir:Path,*,
                                  model:str="gemma3:4b",batches:int=16,
                                  resume:bool=False)->dict:
    if type(batches) is not int or not 1<=batches<=40:
        raise ValueError("Number of local second-look batches must be 1..40")
    root=Path(workspace).expanduser().resolve()
    assignments=_safe_input(root,assignments)
    if (assignments.is_symlink() or not assignments.is_file()
            or assignments.stat().st_size>20_000_000):
        raise ValueError("Assignments must be an existing small private workspace report")
    fingerprint=sha256(assignments.read_bytes()).hexdigest()
    dest=Path(output_dir).expanduser().resolve()
    if resume:
        if (not dest.is_dir() or dest==root or not dest.is_relative_to(root)
                or dest.relative_to(root).parts[0] not in ("reports","exports")
                or dest.is_symlink()):
            raise ValueError("Resume requires existing private reports/ or exports/ directory")
    else:
        dest=_safe_dest(root,dest)
        dest.mkdir(parents=True,mode=0o700,exist_ok=False)
    state_file=dest/"secondlook-state.json"
    if resume:
        if not state_file.is_file():
            raise ValueError("No checkpointed batch state to resume")
        checkpoint=json.loads(state_file.read_text(encoding="utf-8"))
        if (checkpoint.get("version")!=VERSION or
                checkpoint.get("assignmentsSha256")!=fingerprint or
                checkpoint.get("model")!=model or
                not isinstance(checkpoint.get("excludedReviewIds"),list)):
            raise ValueError("Second-look resume cannot change input assignments or model")
    else:
        checkpoint={
            "version":VERSION,
            "assignmentsSha256":fingerprint,"model":model,
            "excludedReviewIds":[],
            "batchFiles":[],
            "reusedLocalModelOutputs":0,
            "newLocalModelOutputs":0,
            "failures":0,
            "completed":False,
            "sourceMediaReads":0,
            "newFullSourceShaPasses":0,
            "filesDeleted":0,
        }
        _write_atomic(state_file,checkpoint)
    excluded=set(checkpoint["excludedReviewIds"])
    start_at=len(checkpoint["batchFiles"])
    for i in range(start_at,min(40,start_at+batches)):
        report=inspect_temporal_candidates(
            root,assignments,model=model,limit=8,run=True,
            exclude_review_ids=tuple(sorted(excluded)))
        selected=report.get("selected",0)
        report_file=dest/(f"secondlook-batch-{i+1:03d}.json")
        if not isinstance(selected,int) or selected<0 or selected>8:
            raise ValueError("Unexpected local cached second-look batch count")
        # Save full batch results before updating checkpoint. Retries always
        # reuse the local second-look content cache if a crash occurred.
        _write_atomic(report_file,report)
        for item in report.get("items",[]):
            rid=item.get("reviewId")
            if isinstance(rid,str):
                excluded.add(rid)
        checkpoint["excludedReviewIds"]=sorted(excluded)
        checkpoint["batchFiles"].append(report_file.name)
        checkpoint["reusedLocalModelOutputs"]+=report.get("cachedSecondLooksReused",0)
        checkpoint["newLocalModelOutputs"]+=report.get("newLocalModelCalls",0)
        checkpoint["failures"]+=report.get("failed",0)
        checkpoint["lastEligibleUnreviewed"]=report.get("eligibleUnreviewed",0)
        checkpoint["lastSelected"]=selected
        if selected==0:
            checkpoint["completed"]=True
        state_file.unlink()
        _write_atomic(state_file,checkpoint)
        print(f"[klip-secondlook] batch {i+1}, selected {selected}, "
              f"total qualified IDs {len(excluded)}, failures {checkpoint['failures']}",
              flush=True)
        if selected==0:
            break
    return {
        "status":"cached_secondlook_batches_complete" if checkpoint["completed"]
                 else "cached_secondlook_budget_reached",
        "outputDir":str(dest),
        "checkpoint":str(state_file),
        "batchesRecorded":len(checkpoint["batchFiles"]),
        "reviewIdsCovered":len(excluded),
        "newLocalModelOutputs":checkpoint["newLocalModelOutputs"],
        "reusedLocalModelOutputs":checkpoint["reusedLocalModelOutputs"],
        "failed":checkpoint["failures"],
        "sourceVideoReads":0,"newFullSourceShaPasses":0,
        "newProxiesCreated":0,"filesDeleted":0,
        "trainingPerformed":False,
    }

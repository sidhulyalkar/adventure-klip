"""Read-only paired comparison of legacy vs adaptive-v2 local judge caches.

No original video reads, new frame decoding, local Ollama calls, external
requests, review edits, or changes to signed archive artifacts. Human Keep/Skip
is personal preference, not scientific ground truth. Missing ratings stay null.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from html import escape
from pathlib import Path
import json

from .benchmark_feedback import load_benchmark, benchmark_feedback_status
from .studio_preview_judge import preview_paths, cached_judgment, valid_local_model
from .adaptive_judge_v2 import context_hint, cached_adaptive_judgment

VERSION="archive-judge-audit-v1"


def compare_judge_policies(workspace:Path,assignments:Path,*,model:str)->dict:
    if not valid_local_model(model):
        raise ValueError("Explicit installed local model identifier required")
    root=Path(workspace).expanduser().resolve()
    benchmark=load_benchmark(root,assignments)
    reviews=benchmark_feedback_status(root,assignments)["reviewEntries"]
    counts={
        "legacy":{"available":0,"comparedToHuman":0,"wouldSkipHumanKeep":0,
                  "wouldKeepHumanSkip":0,"unsureOnHumanKeep":0},
        "adaptive-v2":{"available":0,"comparedToHuman":0,"wouldSkipHumanKeep":0,
                       "wouldKeepHumanSkip":0,"unsureOnHumanKeep":0},
    }
    by_sport=defaultdict(lambda:defaultdict(lambda:Counter()))
    rows=[]
    both=0
    for item in benchmark["assignments"].values():
        aid=item["assetId"]
        video,_=preview_paths(root,aid,item["startMs"],item["endMs"])
        context=context_hint(item)
        legacy=cached_judgment(video,model=model)
        adaptive=cached_adaptive_judgment(video,model=model,context=context)
        human=reviews.get(item["reviewId"],{})
        user_decision=human.get("reviewDecision")
        row={
            "reviewId":item["reviewId"],"filename":item.get("basename"),
            "role":item["role"],"contextHint":context,
            "humanDecision":user_decision,
            "legacyRecommendation":legacy["recommendation"] if legacy else None,
            "adaptiveRecommendation":adaptive["recommendation"] if adaptive else None,
            "adaptiveUncertainty":adaptive.get("uncertaintyReason") if adaptive else None,
            "adaptivePolicyOverrides":adaptive.get("policyOverrides",[]) if adaptive else [],
            "modelCannotVerifySpeciesOrManeuver":True,
        }
        rows.append(row)
        if legacy and adaptive:
            both+=1
        for policy,vote in (("legacy",legacy),("adaptive-v2",adaptive)):
            if vote is None:
                continue
            counts[policy]["available"]+=1
            by_sport[context][policy]["available"]+=1
            if user_decision not in ("keep","skip"):
                continue
            counts[policy]["comparedToHuman"]+=1
            by_sport[context][policy]["comparedToHuman"]+=1
            if vote["recommendation"]=="skip" and user_decision=="keep":
                counts[policy]["wouldSkipHumanKeep"]+=1
                by_sport[context][policy]["wouldSkipHumanKeep"]+=1
            if vote["recommendation"]=="keep" and user_decision=="skip":
                counts[policy]["wouldKeepHumanSkip"]+=1
                by_sport[context][policy]["wouldKeepHumanSkip"]+=1
            if vote["recommendation"]=="unsure" and user_decision=="keep":
                counts[policy]["unsureOnHumanKeep"]+=1
    return {
        "version":VERSION,"model":model,
        "benchmarkId":benchmark["benchmarkId"],
        "totalIntervals":len(rows),"humanReviews":len(reviews),
        "bothPolicyCachesAvailable":both,
        "byPolicy":counts,
        "bySport":{sport:{policy:dict(c) for policy,c in sorted(by.items())}
                   for sport,by in sorted(by_sport.items())},
        "intervals":rows,
        "videoSourceBytesRead":0,"newVisionCalls":0,"humanReviewsModified":False,
        "archiveRecall":None,"modelQualityImprovementProven":False,
        "interpretation":[
            "WouldSkipHumanKeep is preference disagreement, not a verified missed event.",
            "AI skips are preliminary ranking signals; all controls stay visible.",
            "Counts may differ because v1 and v2 caches cover different intervals.",
            "Use only intervals with both policies and a saved human judgment for a fair paired comparison.",
            "Review selection is nonrandom; no archive-wide generalization is supported.",
            "Model tag might be mutable: local Ollama digest must be recorded before scientific claims.",
            "Temporal maneuvers, exact species and personal novelty cannot be verified from three stills.",
        ],
    }


def save_judge_audit(workspace:Path,assignments:Path,*,model:str,output_dir:Path)->dict:
    root=Path(workspace).expanduser().resolve()
    folder=Path(output_dir).expanduser().resolve()
    if (folder==root or not folder.is_relative_to(root)
        or folder.relative_to(root).parts[0] not in ("reports","exports")
        or folder.exists()):
        raise ValueError("Use a new output directory under reports/ or exports/")
    report=compare_judge_policies(root,assignments,model=model)
    folder.mkdir(parents=True,mode=0o700,exist_ok=False)
    with (folder/"audit.json").open("x",encoding="utf-8") as f:
        json.dump(report,f,sort_keys=True,indent=2,allow_nan=False)
        f.write("\n")
    rows=[]
    for item in report["intervals"]:
        cells=(item["filename"],item["role"],item["contextHint"],
               item["humanDecision"],item["legacyRecommendation"],
               item["adaptiveRecommendation"])
        rows.append("<tr>"+"".join("<td>"+escape(str(v or "unassessed"))+"</td>"
                                    for v in cells)+"</tr>")
    html=(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Klip · Context-aware judge audit</title>'
        '<style>body{font:14px system-ui;background:#101923;color:#edf2f5;'
        'max-width:1200px;margin:28px auto;padding:0 16px;line-height:1.5}'
        'h1{font-size:1.6rem}table{border-collapse:collapse;width:100%}'
        'th,td{padding:9px;border-bottom:1px solid #344557;text-align:left}'
        'p{color:#b7c4ce}th{color:#f4b6a6}</style></head><body>'
        '<h1>Local judge v1 versus adaptive-v2</h1>'
        '<p>These are *provisional* suggestions against personal decisions. '
        'No model was run and no clip was automatically accepted. '
        'Disagreements are not confirmed wildlife or action misses.</p>'
        '<p>Both caches available: '+str(report["bothPolicyCachesAvailable"])+
        '; human-reviewed intervals: '+str(report["humanReviews"])+'.</p>'
        '<table><thead><tr><th>Video</th><th>Role</th><th>Context hint</th>'
        '<th>Human</th><th>Legacy</th><th>Adaptive v2</th></tr></thead>'
        '<tbody>'+''.join(rows)+'</tbody></table></body></html>'
    )
    with (folder/"index.html").open("x",encoding="utf-8") as f:
        f.write(html)
    return {"status":"cache_only_policy_audit","report":str(folder/"audit.json"),
            "html":str(folder/"index.html"),
            "comparedWithBothCaches":report["bothPolicyCachesAvailable"],
            "humanReviews":report["humanReviews"],
            "sourceVideosRead":0,"visionRequests":0}

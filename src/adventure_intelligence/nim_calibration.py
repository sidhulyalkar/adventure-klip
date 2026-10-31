"""Calibrate model jury against human editorial Keep/Skip reviews.

This is a conditional comparison on reviewed intervals, not a proof of
all-library retrieval recall, temporal completeness, or action ground truth.
"""
from __future__ import annotations
from collections import Counter
from .examples import review_inventory

VERSION="nim-human-calibration-v1"


def _overlap(a,b,c,d):
    return max(0,min(b,d)-max(a,c))

def compare_with_humans(manifest: dict, jury_report: dict,
                        human_examples: list[dict]) -> dict:
    if manifest.get("schemaVersion")!=1 or jury_report.get("schemaVersion")!=1:
        raise ValueError("Unsupported benchmark files")
    clips={c["clipId"]:c for c in manifest.get("clips",[])}
    results={r["clipId"]:r for r in jury_report.get("results",[])}
    if not clips or len(clips)!=len(manifest.get("clips",[])):
        raise ValueError("Missing or duplicate benchmark clips")
    stats={pool:Counter() for pool in ("candidate","random")}
    samples=[]
    for cid,clip in clips.items():
        pool=clip.get("pool")
        if pool not in stats: raise ValueError("Unknown sampling pool")
        info=stats[pool]
        info["sampled"]+=1
        matching=[]
        for human in human_examples:
            if (human.get("assetId")!=clip.get("sourceAssetId")
                    or human.get("highlight") not in {"keep","skip"}):
                continue
            coverage=_overlap(clip["startMs"],clip["endMs"],
                              human["startMs"],human["endMs"])/max(1,clip["endMs"]-clip["startMs"])
            if coverage>=.8:
                matching.append(human["highlight"])
        if not matching:
            info["unreviewed"]+=1
            continue
        if len(set(matching))!=1:
            info["conflicted"]+=1
            continue
        human_positive=matching[0]=="keep"
        info["humanReviewed"]+=1
        info["humanKeeps"]+=int(human_positive)
        info["humanSkips"]+=int(not human_positive)
        result=results.get(cid)
        if result is None:
            info["modelMissing"]+=1
            continue
        jury=result.get("jury") or {}
        if jury.get("status") not in {"provisional","disagreement_review"}:
            info["modelAbstained"]+=1
            continue
        recommendation=jury.get("recommendation")
        if recommendation not in {"keep","skip"}:
            info["modelAbstained"]+=1
            continue
        predicted=recommendation=="keep"
        info["modelEvaluated"]+=1
        info["tp" if predicted and human_positive else
             "fp" if predicted and not human_positive else
             "fn" if not predicted and human_positive else "tn"]+=1
        samples.append({"clipId":cid,"pool":pool,"human":matching[0],
                        "model":recommendation,"disagreement":jury["status"]=="disagreement_review"})
    report={}
    for pool,c in stats.items():
        tp,fp,fn=c["tp"],c["fp"],c["fn"]
        report[pool]={**{name:c[name] for name in (
            "sampled","unreviewed","conflicted","humanReviewed","humanKeeps",
            "humanSkips","modelMissing","modelAbstained","modelEvaluated",
            "tp","fp","fn","tn")},
            "precisionAmongReviewed":round(tp/(tp+fp),3) if tp+fp else None,
            "recallAmongReviewed":round(tp/(tp+fn),3) if tp+fn else None,
            "modelCoverageAmongReviewed":
                round(c["modelEvaluated"]/c["humanReviewed"],3) if c["humanReviewed"] else None}
    return {"schemaVersion":1,"version":VERSION,"pools":report,
            "matchedCases":samples,"warnings":[
                "These are conditional comparisons to individual human editorial labels.",
                "Randomly sampled intervals are not known negatives.",
                "RecallAmongReviewed is not recall over all valuable moments in the full video library.",
                "Human review intervals must cover >=80% of the sampled window to be compared.",
                "Use separate trips/dates for calibration and held-out evaluation; no tuning on the test set.",
                "Missing or ambiguous human labels and abstaining models are not counted as true negatives.",
            ]}

def calibrate_workspace(workspace, manifest: dict, jury_report: dict) -> dict:
    return compare_with_humans(manifest,jury_report,
                               review_inventory(workspace)["examples"])

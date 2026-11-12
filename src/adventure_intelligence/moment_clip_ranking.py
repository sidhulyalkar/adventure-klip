"""Evidence-separated, goal-aware review ranking of existing clip proposals.

This is triage, NOT calibrated clip quality, action verification or learned
personal preferences. Missing evidence stays unknown, not a zero rating.
Originals are never read, modified, trimmed, deleted or published.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from hashlib import sha256
import math
import re

from .scene_relevance import GOAL_WEIGHTS

VERSION = "klip-moment-review-ranking-v1"
SHA = re.compile(r"^[a-f0-9]{64}$")
GOALS = ("auto", *GOAL_WEIGHTS, "exploration")
SPORTS = ("ski", "bike", "snorkeling", "unknown")
# Hypothesized routing weights for review, NOT trained preferences.
WEIGHTS = {
    "sport_action": (.30,.28,.17,.07,.00,.18),
    "nature_encounter": (.24,.28,.16,.02,.12,.18),
    "scenic_transition": (.22,.13,.24,.07,.16,.18),
    "personal_memory": (.13,.07,.10,.02,.08,.60),
    "technique_analysis": (.26,.33,.14,.09,.00,.18),
    "exploration": (.23,.24,.17,.08,.10,.18),
}
DIMENSIONS = ("detector", "event_kind_fit", "scene_context",
              "camera_motion_proxy", "underwater_continuity", "local_review_keep")
EVENT_FIT = {
    "sport_action": {
        "jump_chain":.95,"carving_flow":.93,"tree_weaving":.93,
        "technical_feature_chain":.90,"active_sequence":.60,
        "sustained_motion_candidate":.30,"camera_rhythm_candidate":.15,
        "wildlife_encounter":.25},
    "nature_encounter": {
        "wildlife_encounter":.98,"active_sequence":.35,
        "scenic_transition":.45,"visual_transition":.35,
        "sustained_motion_candidate":.20,"camera_rhythm_candidate":.10},
    "scenic_transition": {
        "scenic_transition":.90,"visual_transition":.80,
        "active_sequence":.45,"wildlife_encounter":.75,
        "sustained_motion_candidate":.20,"camera_rhythm_candidate":.10},
    "personal_memory": {
        "wildlife_encounter":.30,"jump_chain":.30,
        "carving_flow":.25,"active_sequence":.20},
    "technique_analysis": {
        "technical_feature_chain":.95,"jump_chain":.85,
        "carving_flow":.90,"tree_weaving":.88,
        "incident_review":.50,"active_sequence":.55},
    "exploration": {
        "wildlife_encounter":.80,"jump_chain":.80,
        "carving_flow":.72,"tree_weaving":.72,
        "technical_feature_chain":.72,"active_sequence":.50,
        "scenic_transition":.55,"visual_transition":.55,
        "sustained_motion_candidate":.22,
        "camera_rhythm_candidate":.15},
}


def _unit(value):
    return (float(value) if type(value) in (int,float)
            and math.isfinite(value) and 0 <= value <= 1 else None)


def _identifier(value):
    return (isinstance(value,str) and 1 <= len(value) <= 140
            and all(ch.isalnum() or ch in "._:-" for ch in value))


def _goal(goal: str,sport: str) -> str:
    if goal != "auto":
        return goal
    return {"ski":"sport_action","bike":"sport_action",
            "snorkeling":"nature_encounter","unknown":"exploration"}[sport]


def score_candidate(candidate: dict, *, sport: str, goal: str = "auto") -> dict:
    """Compute a bounded sensitivity interval over explicit heuristic cues.

    Its lower bound is a review priority, not a statistical credible bound.
    Do NOT double-count the existing composite editorial score: use only
    originalEpisodeScore and separately named existing evidence fields.
    """
    if sport not in SPORTS or goal not in GOALS:
        raise ValueError("Invalid sport or editorial goal")
    if not isinstance(candidate,dict):
        raise ValueError("Expected a clip candidate")
    aid=candidate.get("sourceAssetId")
    start,end=candidate.get("startMs"),candidate.get("endMs")
    kind,ep=candidate.get("kind"),candidate.get("episodeId")
    if (not isinstance(aid,str) or not SHA.fullmatch(aid)
            or type(start) is not int or type(end) is not int
            or not 0 <= start < end <= 86_400_000
            or not _identifier(kind) or not _identifier(ep)):
        raise ValueError("Invalid bounded candidate identity")
    policy=_goal(goal,sport)
    scene=candidate.get("sceneEvidence") or {}
    motion=candidate.get("motionEvidence") or {}
    water=candidate.get("underwaterContinuity") or {}
    if not all(isinstance(x,dict) for x in (scene,motion,water)):
        raise ValueError("Malformed existing evidence object")
    scene_value=None
    if type(scene.get("observations")) is int and scene["observations"] >= 3:
        positive=scene.get("likelyPositiveContext") is True
        negative=scene.get("likelyNegativeContext") is True
        if positive != negative:
            scene_value=1.0 if positive else 0.0
    motion_value=(_unit(motion.get("quality"))
                  if type(motion.get("samples")) is int and motion["samples"] >= 2
                  else None)
    water_value=(_unit(water.get("supportedFraction"))
                 if sport=="snorkeling"
                 and type(water.get("intervalCount")) is int
                 and water["intervalCount"]>0 else None)
    keep=_unit(candidate.get("reviewedKeepFraction"))
    values=( _unit(candidate.get("originalEpisodeScore")),
             EVENT_FIT[policy].get(kind),scene_value,motion_value,
             water_value,keep if keep is not None and keep>0 else None)
    lower=coverage=0.0
    components={}
    for name,weight,value in zip(DIMENSIONS,WEIGHTS[policy],values):
        if weight==0:
            continue
        if value is not None:
            lower+=weight*value
            coverage+=weight
        components[name]={
            "value":value,"weight":weight,
            "contribution":round(weight*value,5) if value is not None else None,
            "provenance":"unverified_model_or_detector_hypothesis"
                         if name!="local_review_keep" else
                         "local_review_record_not_independently_audited",
            "status":"observed_proxy" if value is not None else "unknown",
        }
    warnings=["not_calibrated_momentworthiness",
              "missing_evidence_not_a_negative_label"]
    if candidate.get("incidentReviewFlags"):
        warnings.append("camera_disturbance_requires_review_not_crash")
    if scene.get("likelyNegativeContext") is True:
        warnings.append("possible_non_action_not_automatically_discarded")
    return {
        "clipId":"clip-"+sha256(f"{aid}:{start}:{end}".encode()).hexdigest()[:24],
        "sourceSha256":aid,"startMs":start,"endMs":end,
        "durationSeconds":round((end-start)/1000,3),
        "episodeId":ep,"eventKindProposal":kind,
        "sportHint":sport,"editorialGoal":policy,
        "goalChosenAutomatically":goal=="auto",
        "priorityLower":round(100*lower,3),
        "priorityUpper":round(100*min(1.,lower+(1.-coverage)),3),
        "observableCueCoverage":round(coverage,5),
        "cueDetails":components,
        "missingCueNames":[name for name,c in components.items()
                           if c["value"] is None],
        "eventCompleteness":None,
        "technicalImageQuality":None,
        "withinArchiveNovelty":None,
        "personalMeaning":None,
        "verifiedOuting":False,
        "requiresReview":True,
        "riskFlags":warnings,
        "rankingVersion":VERSION,
        "preferenceModelTrained":False,
    }


def _overlap(a,b):
    if a["sourceSha256"]!=b["sourceSha256"]:
        return 0.0
    duration=min(a["endMs"]-a["startMs"],b["endMs"]-b["startMs"])
    return max(0,min(a["endMs"],b["endMs"]) -
               max(a["startMs"],b["startMs"]))/duration


def rank_candidates(candidates:list[dict],sport_by_source:dict[str,str],
                    *, goal:str="auto",limit:int=24,
                    max_per_source:int=2,min_coverage:float=.25)->dict:
    """Interleave sport buckets; do not pretend cross-goal scores are comparable."""
    if (goal not in GOALS or type(limit) is not int or not 1<=limit<=200
            or type(max_per_source) is not int or not 1<=max_per_source<=8
            or _unit(min_coverage) is None):
        raise ValueError("Invalid review ranking parameters")
    if not isinstance(candidates,list) or len(candidates)>20000:
        raise ValueError("Unbounded candidate input")
    scored=[score_candidate(x,sport=sport_by_source.get(
        x.get("sourceAssetId"),"unknown"),goal=goal) for x in candidates]
    buckets=defaultdict(list)
    low=[]
    for row in scored:
        if row["observableCueCoverage"] >= min_coverage:
            buckets[row["sportHint"]].append(row)
        else:
            low.append(row)
    for rows in buckets.values():
        rows.sort(key=lambda x:(-x["priorityLower"],-x["observableCueCoverage"],
                                x["sourceSha256"],x["startMs"]))
    selected,per_source,cursors=[],Counter(),Counter()
    while len(selected)<limit:
        changed=False
        for sport in SPORTS:
            pool=buckets[sport]
            while cursors[sport]<len(pool):
                row=pool[cursors[sport]]
                cursors[sport]+=1
                if per_source[row["sourceSha256"]]>=max_per_source:
                    continue
                if any(_overlap(row,prior)>.45 for prior in selected):
                    continue
                selected.append(row)
                per_source[row["sourceSha256"]]+=1
                changed=True
                break
            if len(selected)>=limit:
                break
        if not changed:
            break
    for i,row in enumerate(selected,1):
        row["rankForReview"]=i
    return {
        "version":VERSION,"goal":goal,
        "candidatesEvaluated":len(scored),
        "candidatesWithUsableHeuristicEvidence":len(scored)-len(low),
        "candidatesWithInsufficientEvidence":len(low),
        "rankedCount":len(selected),
        "rankedClips":selected,
        "rankingScope":"diverse_review_priority_not_cross_sport_quality",
        "preferenceModelTrained":False,
        "automaticSkipPublishDeleteAllowed":False,
        "sourceMediaRead":False,
    }

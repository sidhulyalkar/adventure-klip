"""Read-only archive adapter for explicit clip review ranking and exploration.

Existing episodes -> editorial candidate evidence -> goal-aware heuristic
review order. Sources without evidence get unscored exploratory intervals.
No video, FFmpeg, GPU, HTTP, DB writes, or published clip is involved.
"""
from __future__ import annotations

from collections import Counter
import math
from pathlib import Path
import sqlite3

from .encoder_dataset import safe_training_workspace
from .editorial_quality import editorial_candidates
from .moment_archive_profile import audit_archive
from .moment_clip_ranking import GOALS, VERSION, rank_candidates


def _explore_span(seconds):
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<=0:
        return None
    end=round(seconds*1000)
    span=min(20000,end)
    # This is a *uniform coverage probe*, not an identified event or edit.
    start=min(max(0,round(end*.33-span/2)),end-span)
    if span<3000:
        return None
    return start,start+span


def rank_archive(workspace:Path, *, goal:str="auto", limit:int=24,
                 explore_fraction:float=.35, max_per_source:int=2,
                 judge_report:Path|None=None,seed:int=17,
                 source_limit:int=5000, episode_limit:int=6000) -> dict:
    if (goal not in GOALS or type(limit) is not int or not 1<=limit<=100
            or type(max_per_source) is not int or not 1<=max_per_source<=8
            or type(seed) is not int or not 0<=seed<=2**31-1
            or type(explore_fraction) not in (int,float)
            or not math.isfinite(explore_fraction)
            or not .10<=explore_fraction<=.90
            or type(source_limit) is not int or not 1<=source_limit<=10000
            or type(episode_limit) is not int or not 1<=episode_limit<=20000):
        raise ValueError("Invalid bounded clip ranking request")
    root=safe_training_workspace(workspace)
    inventory=audit_archive(root,limit=source_limit,
        pilot_sources=min(256,max(72,limit*4)),seed=seed,
        judge_report=judge_report)
    assets={x["sourceSha256"]:x for x in inventory["sources"]}
    catalog=root/"catalog.sqlite3"
    if catalog.is_symlink() or not catalog.is_file():
        raise ValueError("Missing or symlinked catalog")
    with sqlite3.connect(catalog.as_uri()+"?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        count=db.execute(
            "SELECT COUNT(*) FROM episodes WHERE kind!='recording_summary'"
        ).fetchone()[0]
        if count>episode_limit:
            raise ValueError("Too many episodes; increase explicit bounded limit")
        rows=[dict(x) for x in db.execute(
            "SELECT * FROM episodes WHERE kind!='recording_summary'"
            " ORDER BY asset_id,start_ms,id")]
        valid=[]
        for x in rows:
            asset=assets.get(x["asset_id"])
            duration=asset["durationSeconds"] if asset else None
            if (not asset or duration is None or
                    type(x["start_ms"]) is not int or
                    type(x["end_ms"]) is not int or
                    not 0<=x["start_ms"]<x["end_ms"]<=round(duration*1000)):
                continue
            valid.append(x)
        # The existing candidate builder reads only cached, hash-checkable
        # derived features and stored event/review metadata. It never opens
        # any original camera source. Suggest-only avoids model-based excludes.
        candidates,exclusions=editorial_candidates(db,root,valid,policy="suggest")
    if len(candidates)>20000:
        raise ValueError("Expanded candidate windows exceed safe ranking limit")
    sport_map={aid:row["candidateSport"] for aid,row in assets.items()}
    # Ensure an exploration slot even when heuristic episodes dominate.
    target_explore=max(1,math.ceil(limit*explore_fraction))
    ranked=rank_candidates(candidates,sport_map,goal=goal,
                           limit=limit-target_explore,
                           max_per_source=max_per_source)
    selected_sources={x["sourceSha256"] for x in ranked["rankedClips"]}
    exploration=[]
    def append_probes(avoid_selected: bool):
        for row in inventory["pilotPlan"]["candidateSources"]:
            if len(exploration)>=limit-len(ranked["rankedClips"]):
                break
            aid=row["sourceSha256"]
            if aid in {x["sourceSha256"] for x in exploration}:
                continue
            if avoid_selected and aid in selected_sources:
                continue
            span=_explore_span(row["durationSeconds"])
            if span is None:
                continue
            exploration.append({
                "sourceSha256":aid,"startMs":span[0],"endMs":span[1],
                "sportHint":row["candidateSport"],
                "sportEvidence":row["sportEvidence"],
                "captureMonthUtc":row["captureMonthUtc"],
                "rankForReview":len(ranked["rankedClips"])+len(exploration)+1,
                "priorityScore":None,"eventKindProposal":None,
                "selectionType":"unscored_archive_coverage_probe",
                "selectionReason":"representative_exploration_no_event_verification",
                "completeEventVerified":False,
                "sourceVideoRead":False,"requiresReview":True,
            })
    append_probes(True)
    # If the inventory is small, a reviewable source may need both a ranked
    # event and an independent coverage probe. Never suppress by guess.
    append_probes(False)
    if len(exploration)<target_explore and len(ranked["rankedClips"])>0:
        # A short archive can have fewer than requested reviewable sources.
        # Do not fabricate extra windows or scores to fill empty slots.
        pass
    return {
        "version":VERSION,
        "sourceCatalogCount":len(assets),
        "catalogActivityUnverified":inventory["catalogActivityUnverifiedCounts"],
        "episodeRowsInCatalog":count,
        "eligibleEpisodeRows":len(valid),
        "episodeRowsWithInvalidOrMissingBounds":len(rows)-len(valid),
        "expandedCandidateWindows":len(candidates),
        "upstreamManualExclusions":len(exclusions),
        "rankingPolicy":"suggest_only_no_model_hard_rejections",
        "goalRequested":goal,
        "rankedCount":len(ranked["rankedClips"]),
        "explorationCount":len(exploration),
        "rankedClips":ranked["rankedClips"],
        "explorationClips":exploration,
        "rankingDiagnostics":{
            k:v for k,v in ranked.items()
            if k not in ("rankedClips","version")
        },
        "recommendedNextAction":(
            "Review unscored exploration probes; generate event evidence before clip ranking"
            if not ranked["rankedClips"] else
            "Review ranked event proposals AND unscored exploration probes"
        ),
        "allActivityAndEventProposalsUnverified":True,
        "actualRecordedEventCompletenessMeasured":False,
        "videoDecoded":False,"sourceVideoRead":False,
        "workspaceCatalogModified":False,
        "modelInferencePerformed":False,
        "trainablePreferenceModelFitted":False,
        "automaticDeleteExportPublishAuthorized":False,
    }

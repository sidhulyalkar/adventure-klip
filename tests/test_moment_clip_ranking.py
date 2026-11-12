"""Synthetic, source-free tests of bounded editorial triage ranking."""
from __future__ import annotations
from pathlib import Path
import json
import subprocess
import sys

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.moment_clip_ranking import (
    VERSION,rank_candidates,score_candidate,
)
from adventure_intelligence.moment_rank_archive import rank_archive
from adventure_intelligence.workspace import init


def proposed(aid,kind="jump_chain",*,start=0,end=12000,strength=.8):
    return {
        "sourceAssetId":aid,"episodeId":"ep-synthetic-"+kind,
        "kind":kind,"startMs":start,"endMs":end,
        "originalEpisodeScore":strength,
        "motionEvidence":{"quality":None,"samples":0},
        "sceneEvidence":{"observations":0,
                          "likelyPositiveContext":False,
                          "likelyNegativeContext":False},
        "underwaterContinuity":None,
        "reviewedKeepFraction":0.,
        "incidentReviewFlags":[],
        "score":.99,  # Composite must NEVER be double counted.
    }


def test_event_type_relevance_and_missing_evidence_not_quality():
    aid="a"*64
    jump=score_candidate(proposed(aid),sport="ski")
    jolt=score_candidate(proposed(aid,"camera_rhythm_candidate"),sport="ski")
    assert jump["priorityLower"] > jolt["priorityLower"]
    assert jump["eventCompleteness"] is None
    assert jump["technicalImageQuality"] is None
    assert jump["withinArchiveNovelty"] is None
    assert jump["personalMeaning"] is None
    assert "scene_context" in jump["missingCueNames"]
    assert "local_review_keep" in jump["missingCueNames"]
    assert jump["priorityUpper"]>jump["priorityLower"]
    assert jump["goalChosenAutomatically"] is True
    assert jump["editorialGoal"]=="sport_action"
    assert jump["preferenceModelTrained"] is False


def test_existing_composite_score_cannot_inflate_rank():
    aid="b"*64
    base=proposed(aid)
    unchanged=score_candidate(base,sport="bike")
    base["score"]=.01
    assert score_candidate(base,sport="bike")["priorityLower"]==unchanged["priorityLower"]
    # An apparent camera disturbance is a review warning, never a crash claim
    base["incidentReviewFlags"]=[{"label":"abrupt_camera_disturbance"}]
    flagged=score_candidate(base,sport="bike")
    assert "camera_disturbance_requires_review_not_crash" in flagged["riskFlags"]
    assert flagged["priorityLower"]==unchanged["priorityLower"]


def test_water_continuity_is_proxy_not_proof_of_complete_encounter():
    c=proposed("c"*64,"wildlife_encounter")
    c["underwaterContinuity"]={"intervalCount":2,"supportedFraction":.86}
    c["sceneEvidence"]={"observations":4,"likelyPositiveContext":True,
                        "likelyNegativeContext":False}
    x=score_candidate(c,sport="snorkeling")
    assert x["editorialGoal"]=="nature_encounter"
    assert x["cueDetails"]["underwater_continuity"]["value"]==.86
    assert x["cueDetails"]["scene_context"]["value"]==1.
    assert x["eventCompleteness"] is None
    assert x["personalMeaning"] is None


def test_rank_interleaves_sports_and_deduplicates_overlap():
    a,b,c="a"*64,"b"*64,"c"*64
    candidates=[
        proposed(a,start=0,end=15000),
        proposed(a,start=2000,end=16000),
        proposed(b,start=0,end=15000),
        proposed(c,"wildlife_encounter",start=0,end=15000),
    ]
    ranks=rank_candidates(candidates,{a:"ski",b:"bike",c:"snorkeling"},
                          limit=4)
    assert ranks["candidatesEvaluated"]==4
    assert ranks["rankedCount"]==3
    assert [x["sportHint"] for x in ranks["rankedClips"]]==[
        "ski","bike","snorkeling"]
    assert len({(x["sourceSha256"],x["startMs"]) for x in ranks["rankedClips"]})==3
    assert all(x["rankForReview"]==i for i,x in enumerate(ranks["rankedClips"],1))


def test_unobserved_event_and_invalid_inputs_fail_closed():
    aid="a"*64
    with pytest.raises(ValueError,match="Invalid bounded"):
        score_candidate(proposed(aid,start=100,end=100),sport="ski")
    with pytest.raises(ValueError,match="Invalid sport"):
        score_candidate(proposed(aid),sport="boat")
    with pytest.raises(ValueError):
        rank_candidates([proposed(aid)],{aid:"ski"},limit=0)
    with pytest.raises(ValueError):
        score_candidate({**proposed(aid),"sourceAssetId":"bad"},sport="ski")
    # Contradictory visual evidence is not promoted to a known positive.
    ambiguous=proposed(aid)
    ambiguous["sceneEvidence"]={
        "observations":4,"likelyPositiveContext":True,
        "likelyNegativeContext":True}
    assert score_candidate(ambiguous,sport="ski")[
        "cueDetails"]["scene_context"]["value"] is None


def archive(tmp_path,with_episodes=True):
    root=tmp_path/"workspace"
    init(root)
    (root/"reports").mkdir()
    aids=[f"{i+1:064x}" for i in range(8)]
    with connect(root) as db:
        for i,aid in enumerate(aids):
            activity=("ski","bike","snorkeling","unknown")[i%4]
            duration=180.
            db.execute(
                "INSERT INTO assets(id,sha256,stored_path,size_bytes,suffix,"
                "duration,capture_time,activity,metadata_json)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (aid,aid,f"/Volumes/OFFLINE/NO_FILE_{i}.MP4",10_000_000,
                 ".mp4",duration,f"2026-01-{i+1:02d}T10:00:00Z",activity,
                 '{"width":3840,"height":2160,"codec":"hevc",'
                 '"avg_frame_rate":"50/1","audio":true}'))
        if with_episodes:
            for i in range(4):
                aid=aids[i]
                kind=("jump_chain","technical_feature_chain",
                      "wildlife_encounter","camera_rhythm_candidate")[i]
                db.execute(
                    "INSERT INTO episodes(id,asset_id,kind,start_ms,end_ms,"
                    "score,event_ids_json,evidence_json,origin,version)"
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (f"episode{i}",aid,kind,10000,32000,.8,
                     "[]","{}","derived","synthetic-v1"))
    return root,aids


def test_archive_adapter_reads_only_caches_and_provides_ranked_and_explore(tmp_path):
    ws,ids=archive(tmp_path)
    before=(ws/"catalog.sqlite3").stat().st_mtime_ns
    result=rank_archive(ws,limit=8,explore_fraction=.5)
    assert result["sourceCatalogCount"]==8
    assert result["episodeRowsInCatalog"]==4
    assert result["eligibleEpisodeRows"]==4
    assert result["rankedCount"]>=1
    assert result["explorationCount"]>=1
    assert result["videoDecoded"] is False
    assert result["sourceVideoRead"] is False
    assert result["workspaceCatalogModified"] is False
    assert result["automaticDeleteExportPublishAuthorized"] is False
    assert all(row["requiresReview"] for row in result["rankedClips"])
    assert all(row["priorityScore"] is None for row in result["explorationClips"])
    assert not any(row["selectionType"] != "unscored_archive_coverage_probe"
                   for row in result["explorationClips"])
    assert (ws/"catalog.sqlite3").stat().st_mtime_ns==before
    assert "/Volumes/" not in json.dumps(result)


def test_archive_without_episodes_never_ranks_unknown_footage(tmp_path):
    ws,_=archive(tmp_path,with_episodes=False)
    output=rank_archive(ws,limit=6)
    assert output["episodeRowsInCatalog"]==0
    assert output["rankedCount"]==0
    assert output["explorationCount"]==6
    assert output["expandedCandidateWindows"]==0
    assert output["recommendedNextAction"].startswith("Review unscored")
    output2=rank_archive(ws,limit=6)
    assert output==output2
    # One-slot archive requests must not call a zero-sized candidate ranker.
    single=rank_archive(ws,limit=1)
    assert single["rankedCount"]==0
    assert single["explorationCount"]==1


def test_cli_stdout_only_even_with_unmounted_source_paths(tmp_path):
    ws,_=archive(tmp_path)
    project=Path(__file__).resolve().parents[1]
    r=subprocess.run([
        sys.executable,str(project/"scripts/klip_rank_clips.py"),
        "--workspace",str(ws),"--limit","4","--goal","auto",
    ],cwd=project,capture_output=True,text=True)
    assert r.returncode==0,r.stderr
    output=json.loads(r.stdout)
    assert output["version"]==VERSION
    assert output["rankedCount"]+output["explorationCount"]<=4
    assert not output["sourceVideoRead"]
    assert not any((ws/"reports").iterdir())

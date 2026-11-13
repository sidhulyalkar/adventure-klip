"""Contract tests for read-only adaptive temporal inspection planning."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.workspace import init
from adventure_intelligence.moment_clip_ranking import VERSION as RANK_VERSION
from adventure_intelligence.moment_adaptive_inspection import (
    VERSION, REPORT_NAME, plan_inspection,
)
from adventure_intelligence.semantic_jobs import encoded, digest


def _ranked(aid, start=6000, end=20000, sport="ski"):
    return {
        "sourceSha256": aid, "startMs": start, "endMs": end,
        "sportHint": sport, "priorityLower": 47.5,
        "priorityUpper": 82.5, "eventKindProposal": "active_sequence",
    }


def _probe(aid,start=2000,end=17000,sport="unknown"):
    return {
        "sourceSha256": aid, "startMs": start, "endMs": end,
        "sportHint": sport,
        "selectionType": "unscored_archive_coverage_probe",
        "priorityScore": None,
    }


def _prepare(tmp_path):
    root = tmp_path / "klip"
    init(root)
    (root/"reports").mkdir()
    (root/"semantics").mkdir()
    aids = [f"{n+1:064x}" for n in range(5)]
    paths = [tmp_path / f"original_{i}.mp4" for i in range(5)]
    # Deliberately do not create original media files.
    with connect(root) as db:
        for i,aid in enumerate(aids):
            db.execute(
                "INSERT INTO assets(id,sha256,stored_path,size_bytes,suffix,"
                "duration,activity,metadata_json) VALUES(?,?,?,?,?,?,?,?)",
                (aid,aid,str(paths[i]),1_000_000,".mp4",60.,
                 ("ski","bike","snorkeling","unknown","unknown")[i],"{}"))
        # Synthetic cached SigLIP2 observations with *8-second* gaps.
        config = {"providerConfig":{"resolvedRevision":"test-r1",
                                    "imageProcessor":{}},
                  "sampleSeconds":8.0}
        for aid in aids[:2]:
            doc = {
                "assetId":aid,"provider":"siglip2","configId":"test-config",
                "modelVersion":"synthetic-siglip","config":config,
                "frames":[{
                    "tMs":t,"decodedPtsMs":t,
                    "embedding":[1. if j==k else 0. for j in range(8)],
                } for k,t in enumerate((0,8000,16000,24000))],
            }
            serialized = encoded(doc)
            filename = f"{aid}-test-config.json"
            (root/"semantics"/filename).write_text(serialized)
            db.execute(
                "INSERT INTO semantic_jobs(asset_id,config_id,config_json,"
                "status,filename,sha256) VALUES(?,?,?,'completed',?,?)",
                (aid,"test-config",encoded(config),filename,digest(serialized)))
            db.execute(
                "INSERT INTO semantic_active(asset_id,provider,config_id)"
                " VALUES(?,'siglip2','test-config')",(aid,))
    report = {
        "version":RANK_VERSION,"rankedCount":3,"explorationCount":2,
        "rankedClips":[
            _ranked(aids[0]),
            _ranked(aids[1],sport="bike"),
            _ranked(aids[2],sport="snorkeling"),
        ],
        "explorationClips":[
            _probe(aids[3]),_probe(aids[4]),
        ],
    }
    (root/"reports"/REPORT_NAME).write_text(json.dumps(report))
    return root,aids,paths,report


def test_selection_reserves_unscored_controls_and_multirate_bounded_frames(tmp_path):
    ws,aids,paths,_ = _prepare(tmp_path)
    original_mtime = (ws/"catalog.sqlite3").stat().st_mtime_ns
    result = plan_inspection(ws,max_clips=4,frame_budget=700)
    assert result["version"] == VERSION
    assert result["inputRankedProposals"] == 3
    assert result["inputExplorationProbes"] == 2
    assert result["plannedClips"] == 4
    assert result["countsBySelectionType"]["exploration"] >= 1
    assert result["requestedFrames"] <= 700
    assert result["sourceVideoRead"] is False
    assert result["modelInferencePerformed"] is False
    assert result["catalogModified"] is False
    assert result["automaticClipSelectionOrDeletion"] is False
    assert all(not p.exists() for p in paths)
    assert (ws/"catalog.sqlite3").stat().st_mtime_ns == original_mtime
    assert "/original_" not in json.dumps(result)
    for item in result["inspections"]:
        assert item["status"] == "inspection_requested"
        assert item["trimReady"] is False
        assert item["completeEventVerified"] is False
        assert item["requestedTimesMs"] == sorted(set(item["requestedTimesMs"]))
        assert all(item["expandedStartMs"] <= t < item["expandedEndMs"]
                   for t in item["requestedTimesMs"])
        assert item["eventPhases"] == {
            "setup":None,"onset":None,"peak":None,"resolution":None}
        if item["selectionType"] == "exploration":
            assert item["eventKindProposal"] is None
            assert item["heuristicReviewPriority"] is None
    # Coarse contrast is allowed; false temporal continuity is not.
    first = next(item for item in result["inspections"]
                 if item["sourceSha256"] == aids[0])
    assert first["coarseContext"]["pairsConnectedAt7000Ms"] == 0
    assert first["coarseContext"]["coarseFeatureModelKey"]
    assert first["coarseContext"]["gapConditionedTemporalActionInferenceSupported"] is False


def test_human_skip_guards_are_hard_in_inspection_plans(tmp_path):
    ws,aids,_,_ = _prepare(tmp_path)
    with connect(ws) as db:
        db.execute(
            "INSERT INTO example_revisions("
            "revision_id,example_id,asset_id,start_ms,end_ms,label,"
            "decision,highlight) VALUES(?,?,?,?,?,?,?,?)",
            ("rev1","ex1",aids[0],7000,13000,"waiting","confirmed","skip"))
    data = plan_inspection(ws,max_clips=5,frame_budget=1000)
    assert all(item["sourceSha256"] != aids[0] for item in data["inspections"])
    assert data["skipped"]["overlaps_human_skip"] == 1
    assert data["plannedClips"] <= 4


def test_overlapping_ranked_candidates_do_not_duplicate_dense_work(tmp_path):
    ws,aids,_,report = _prepare(tmp_path)
    report["rankedClips"][1] = _ranked(aids[0],start=8000,end=22000)
    path = ws/"reports"/REPORT_NAME
    path.write_text(json.dumps(report))
    data = plan_inspection(ws,max_clips=5,frame_budget=1000)
    assert sum(x["sourceSha256"] == aids[0] for x in data["inspections"]) == 1


def test_long_event_is_not_silently_cut_to_fit_budget(tmp_path):
    ws,aids,_,report = _prepare(tmp_path)
    report["rankedClips"][0] = _ranked(aids[0],start=1000,end=56000)
    (ws/"reports"/REPORT_NAME).write_text(json.dumps(report))
    data = plan_inspection(ws,max_clips=4,frame_budget=600)
    assert data["skipped"]["long_clip_requires_manual_split"] == 1
    assert not any(item["sourceSha256"]==aids[0] for item in data["inspections"])


def test_tiny_budget_does_not_truncate_a_candidate_interval(tmp_path):
    ws,_,_,_ = _prepare(tmp_path)
    data = plan_inspection(ws,max_clips=5,frame_budget=64)
    assert data["requestedFrames"] <= 64
    assert data["plannedClips"] == 0
    assert data["skipped"]["frame_budget_rejections"] >= 1


def test_invalid_packet_and_non_workspace_symlinks_refused(tmp_path):
    ws,aids,_,report = _prepare(tmp_path)
    report["rankedClips"][0]["sourceSha256"] = "../../secret"
    (ws/"reports"/REPORT_NAME).write_text(json.dumps(report))
    with pytest.raises(ValueError, match="Invalid ranking"):
        plan_inspection(ws)
    report["rankedClips"][0] = _ranked(aids[0])
    target = tmp_path / "other-file.json"
    target.write_text(json.dumps(report))
    packet = ws/"reports"/REPORT_NAME
    packet.unlink()
    packet.symlink_to(target)
    with pytest.raises(ValueError, match="regular, bounded"):
        plan_inspection(ws)
    with pytest.raises(ValueError, match="Invalid bounded inspection"):
        plan_inspection(ws,max_clips=0)


def test_cli_is_a_read_only_json_plan(tmp_path):
    ws,_,paths,_ = _prepare(tmp_path)
    project = Path(__file__).resolve().parents[1]
    script = project/"scripts"/"klip_plan_dense_inspection.py"
    result = subprocess.run(
        [sys.executable,str(script),"--workspace",str(ws),
         "--max-clips","4","--frame-budget","700"],
        cwd=project,capture_output=True,text=True,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["plannedClips"] == 4
    assert output["frameTimesAreRequestsNotDecodedObservations"]
    assert not any(path.exists() for path in paths)

"""Adaptive scheduler qualification on synthetic source bytes and cached evidence.

The fixtures intentionally bypass media decoding; no real media or model weights
are needed. The repo's FFmpeg integration smoke tests remain separate.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from adventure_intelligence.adaptive_sampling import build_plan, validate_plan
from adventure_intelligence.analyze import MOTION_VERSION
from adventure_intelligence.artifacts import write_artifact
from adventure_intelligence.catalog import connect
from adventure_intelligence.cli import main
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.semantic_jobs import run_semantics


@pytest.fixture
def footage(tmp_path, monkeypatch):
    source = tmp_path / "camera"
    source.mkdir()
    movie = source / "DJI_001.mp4"
    payload = b"original-video-must-never-change" * 200
    movie.write_bytes(payload)
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda p: {"duration": 180., "audio": False,
                                   "capture_time": None})
    workspace = tmp_path / "work"
    outcome = index_folder(source, workspace, activity="snorkeling")
    aid = outcome["asset_ids"][0]
    with connect(workspace) as db:
        series = [{"t_ms": i * 500, "change": .012} for i in range(360)]
        for index in range(100, 106):
            series[index]["change"] = .35
        write_artifact(db, workspace, aid, MOTION_VERSION, series)
    return workspace, movie, payload, aid


def test_plan_is_deterministic_bounded_and_has_coverage(footage):
    workspace, movie, payload, aid = footage
    left = build_plan(workspace, coverage_seconds=35,
                      interest_seconds=2, max_frames=24)
    right = build_plan(workspace, coverage_seconds=35,
                       interest_seconds=2, max_frames=24)
    assert left == right
    assert validate_plan(left)[aid] == [f["tMs"] for f in left["assets"][0]["frames"]]
    asset = left["assets"][0]
    assert asset["frameCount"] <= 24
    assert asset["coverageFrames"] >= 5
    assert asset["interestFrames"] > 0
    assert "coverage_probe" in asset["reasonCounts"]
    assert asset["maximumScheduledGapMs"] <= asset["effectiveCoverageIntervalMs"]
    assert asset["cacheEvidence"]["motion"]
    assert movie.read_bytes() == payload


def test_budget_limit_adapts_spacing_and_reports_it(footage):
    workspace, _, _, _ = footage
    p = build_plan(workspace, coverage_seconds=5, max_frames=8)
    a = p["assets"][0]
    assert a["frameCount"] <= 8
    assert a["effectiveCoverageIntervalMs"] >= 22500
    assert a["samplingIsNotEventRecallGuarantee"]
    assert p["totals"]["assets"] == 1


@pytest.mark.parametrize("change", [
    lambda p: p["assets"][0]["frames"][0].update(tMs=-1),
    lambda p: p["assets"][0]["frames"].append(p["assets"][0]["frames"][0].copy()),
    lambda p: p["assets"][0].update(durationMs=0),
    lambda p: p.update(algorithm="unsafe-adaptive-v42"),
])
def test_plan_tampering_rejected(footage, change):
    p = build_plan(footage[0])
    change(p)
    with pytest.raises(ValueError):
        validate_plan(p)


def test_wrong_and_duplicate_frame_times_are_rejected_even_with_valid_hash(footage):
    from adventure_intelligence.adaptive_sampling import _encoded
    from hashlib import sha256
    p = build_plan(footage[0])
    p["assets"][0]["frames"] = [{"tMs": 42}, {"tMs": 42}]
    p["planId"] = sha256(_encoded({k: v for k, v in p.items()
                                  if k != "planId"}).encode()).hexdigest()
    with pytest.raises(ValueError, match="timestamps"):
        validate_plan(p)


def test_adaptive_inference_reuses_checkpoint_and_never_changes_source(footage, monkeypatch):
    workspace, movie, payload, aid = footage
    p = build_plan(workspace, max_frames=12)
    schedule = validate_plan(p)
    observed = []

    def fake_frame(path, t, width=384, include_timing=False):
        assert path == movie
        observed.append(t)
        return (b"fake-image", None) if include_timing else b"fake-image"

    monkeypatch.setattr("adventure_intelligence.semantic.frame_png", fake_frame)

    class FakeEmbedding:
        name = "test-adaptive-embedding"
        version = "test-v1"
        cache_config = {}
        def describe(self, _raw):
            return {"embedding": [1., 0., 0.], "objects": [],
                    "keypoints": [], "notes": ["synthetic"]}

    first = run_semantics(workspace, FakeEmbedding(), sample_schedule=schedule,
                          schedule_id=p["planId"], asset_ids={aid})
    assert first["processed"] == 1 and first["errors"] == []
    assert first["frames_computed"] == len(schedule[aid])
    assert first["scheduleId"] == p["planId"]
    assert set(first["timingSeconds"]) == {
        "sourceVerification", "frameDecoding", "modelInference", "finalVerification"
    }
    assert observed == schedule[aid]
    assert movie.read_bytes() == payload

    second = run_semantics(workspace, FakeEmbedding(), sample_schedule=schedule,
                           schedule_id=p["planId"], asset_ids={aid})
    assert second["cached"] == 1 and second["frames_computed"] == 0
    assert len(observed) == len(schedule[aid])

    # A different, versioned schedule creates a new config rather than claiming
    # that prior per-frame cache is a completed analysis of the new schedule.
    modified = {aid: schedule[aid][:2]}
    third = run_semantics(workspace, FakeEmbedding(), sample_schedule=modified,
                         schedule_id="a" * 64, asset_ids={aid})
    assert third["processed"] == 1 and third["frames_computed"] == 2
    assert movie.read_bytes() == payload


def test_cli_plan_output_is_local_and_readable(footage, capsys):
    workspace, movie, payload, _ = footage
    target = workspace / "exports" / "pilot.json"
    code = main(["--workspace", str(workspace), "adaptive-plan",
                 "--output", str(target), "--limit", "1", "--max-frames", "24"])
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["totals"]["assets"] == 1
    assert validate_plan(json.loads(target.read_text()))
    assert movie.read_bytes() == payload
    # The planner never silently overwrites a prior plan.
    assert main(["--workspace", str(workspace), "adaptive-plan",
                 "--output", str(target)]) == 2


def test_activity_filter_and_missing_cache_do_not_invent_semantics(footage):
    workspace, movie, payload, aid = footage
    plan = build_plan(workspace, activity="snorkeling")
    assert plan["assets"][0]["activity"] == "snorkeling"
    assert build_plan(workspace, activity="ski")["assets"] == []
    with connect(workspace) as db:
        db.execute("DELETE FROM artifacts WHERE asset_id=?", (aid,))
    p = build_plan(workspace)
    assert p["assets"][0]["cacheEvidence"]["motion"] is False
    assert p["assets"][0]["coverageFrames"] > 0
    assert movie.read_bytes() == payload


def test_adapter_rejects_missing_and_wrong_source_id_without_writing_original(footage):
    workspace, movie, payload, aid = footage
    plan = build_plan(workspace)
    schedule = validate_plan(plan)
    class Provider:
        name="tiny"
        version="v1"
        def describe(self, _):
            return {"embedding":[1., 0.], "objects":[], "keypoints":[]}
    with pytest.raises(ValueError, match="exactly"):
        run_semantics(workspace, Provider(), asset_ids=set(),
                      sample_schedule=schedule, schedule_id=plan["planId"])
    movie.write_bytes(b"changed-source-content")
    result = run_semantics(workspace, Provider(), asset_ids={aid},
                           sample_schedule=schedule, schedule_id=plan["planId"])
    assert result["errors"] and result["processed"] == 0
    assert movie.read_bytes() == b"changed-source-content"
    # The original source payload is not restored or modified by the pipeline.
    assert payload != movie.read_bytes()

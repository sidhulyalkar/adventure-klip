"""Temporal Lite: pure graph invariants and cache-only CLI regression tests."""
from __future__ import annotations

import json

import pytest

from adventure_intelligence.temporal_lite import (
    VERSION, build_temporal_lite, build_cached_temporal_lite,
)
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.catalog import connect
from adventure_intelligence.semantic_jobs import encoded, digest


def frame(t, vector, **extra):
    return {"tMs": t, "embedding": vector, **extra}


def graph(frames, **kwargs):
    return build_temporal_lite(
        frames, model_key="frozen-model-1", duration_ms=30000, **kwargs)


def test_detects_visual_change_not_a_verified_action():
    samples = [frame(1000, [1, 0]), frame(2000, [1, 0]),
               frame(3000, [0, 1]), frame(4000, [0, 1])]
    out = graph(samples)
    assert out["status"] == "available"
    assert len(out["segments"]) == 2
    assert len(out["visualTransitions"]) == 1
    change = out["visualTransitions"][0]
    assert change["candidateBoundaryMs"] == 2500
    assert change["cosineDistance"] == 1
    assert change["kind"] == "visual_change_candidate"
    assert change["confidence"] is None and change["actionIdentity"] is None
    assert all(node["verifiedEvent"] is False for node in out["segments"])
    assert out["sourceVideoRead"] is False and out["inferencePerformed"] is False


def test_order_independent_and_deterministic():
    samples = [frame(t, v) for t, v in (
        (1200, [1, 0]), (2400, [1, 0]), (3600, [0, 1]),
        (4800, [0, 1]))]
    assert graph(samples) == graph(list(reversed(samples)))


def test_long_sparse_gap_is_unknown_even_with_matching_embeddings():
    out = graph([frame(1000, [1, 0]), frame(20000, [1, 0])],
                max_gap_ms=5000, support_radius_ms=1000)
    assert len(out["visualTransitions"]) == 0
    assert len(out["segments"]) == 2
    assert any(gap["startMs"] <= 10000 < gap["endMs"]
               for gap in out["unknownIntervals"])
    assert out["coverage"]["unknownMs"] > 0
    assert out["edges"][0]["continuityVerified"] is False
    assert out["edges"][0]["gapMs"] > 0


def test_do_not_substitute_decoded_pts_for_video_local_requested_time():
    out = graph([{"decodedPtsMs": 2200, "embedding": [1, 0]},
                 frame(1000, [1, 0], decodedPtsMs=90000)])
    assert out["sampling"]["rejected"] == 1
    assert out["status"] == "insufficient_temporal_evidence"
    assert out["segments"][0]["sampleTimesMs"] == [1000]
    assert out["segments"][0]["decodedPtsEvidenceMs"] == [90000]
    assert out["timestampBasis"] == "requested_video_relative_ms"


def test_missing_and_corrupt_embeddings_abstain():
    missing = build_temporal_lite([], duration_ms=30000, model_key=None)
    assert missing["status"] == "abstained"
    assert missing["abstentionReason"] == "no_frozen_model_provenance"
    invalid = graph([frame(1000, [float("nan"), 0]),
                     frame(3000, [0, 0])])
    assert invalid["status"] == "abstained"
    assert invalid["coverage"]["unknownMs"] == 30000


def test_duplicate_timestamps_and_incompatible_dims_are_not_combined():
    duplicate = graph([frame(1000, [1, 0]), frame(1000, [0, 1])])
    assert duplicate["abstentionReason"] == "duplicate_sample_timestamps"
    different = graph([frame(1000, [1, 0]), frame(2000, [1, 0, 0])])
    assert different["abstentionReason"] == "mixed_embedding_dimensions"
    assert not duplicate["segments"] and not different["segments"]


@pytest.mark.parametrize("args", [
    {"duration_ms": 0},
    {"duration_ms": True},
    {"max_gap_ms": 0},
    {"support_radius_ms": False},
    {"change_threshold": -0.1},
    {"change_threshold": float("nan")},
    {"change_threshold": True},
])
def test_bad_parameters_never_create_a_graph(args):
    defaults = {"duration_ms": 30000, "model_key": "frozen-model"}
    with pytest.raises(ValueError):
        build_temporal_lite([frame(1000, [1, 0])], **{**defaults, **args})


def test_duration_bounds_and_coverage_partition():
    out = graph([frame(0, [1, 0]), frame(3000, [1, 0]),
                 frame(29999, [0, 1])])
    parts = sorted([(x["startMs"], x["endMs"])
                    for x in out["segments"] + out["unknownIntervals"]])
    assert parts[0][0] == 0 and parts[-1][1] == 30000
    assert all(x[0] < x[1] for x in parts)
    assert all(a[1] == b[0] for a, b in zip(parts, parts[1:]))
    assert out["coverage"]["observedSupportMs"] + out["coverage"]["unknownMs"] == 30000


@pytest.fixture
def cached_workspace(tmp_path, monkeypatch):
    originals = tmp_path / "DJI originals"
    originals.mkdir()
    source = originals / "DJI_001.MP4"
    source.write_bytes(b"synthetic-test-original-not-a-video")
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda _: {"duration": 30, "audio": False})
    workspace = tmp_path / "work"
    asset_id = index_folder(originals, workspace, activity="ski")["asset_ids"][0]
    conf = "temporal-lite-test"
    document = {"assetId": asset_id, "configId": conf, "provider": "siglip2",
                "modelVersion": "synthetic-model",
                "config": {"providerConfig": {
                    "resolvedRevision": "pinned-revision",
                    "imageProcessor": {"size": 224}}},
                "frames": [frame(1000, [1, 0]), frame(2000, [1, 0]),
                           frame(3000, [0, 1]), frame(4000, [0, 1])]}
    data = encoded(document)
    (workspace / "semantics").mkdir(parents=True, exist_ok=True)
    (workspace / "semantics" / "temporal-test.json").write_text(data)
    with connect(workspace) as db:
        db.execute("""INSERT INTO semantic_jobs
            (asset_id,config_id,config_json,status,filename,sha256)
            VALUES (?,?,?,'completed',?,?)""",
            (asset_id,conf,"{}","temporal-test.json",digest(data)))
        db.execute("INSERT OR REPLACE INTO semantic_active VALUES (?,?,?)",
                   (asset_id,"siglip2",conf))
    return workspace, asset_id, source


def test_cache_only_tool_does_not_require_original_media(cached_workspace):
    workspace, asset_id, source = cached_workspace
    source.unlink()  # Evidence is already in the verified cache.
    out = build_cached_temporal_lite(workspace, asset_id)
    assert out["status"] == "available"
    assert out["assetId"] == asset_id
    assert out["modelKey"]
    assert len(out["visualTransitions"]) == 1


def test_cli_outputs_new_local_report_without_writing_source(cached_workspace, capsys):
    from adventure_intelligence.cli import main
    workspace, asset_id, source = cached_workspace
    before = source.read_bytes()
    target = workspace / "reports" / "temporal-lite-test.json"
    args = ["--workspace", str(workspace), "temporal-lite",
            "--asset", asset_id, "--output", str(target)]
    assert main(args) == 0
    printed = json.loads(capsys.readouterr().out)
    saved = json.loads(target.read_text())
    assert printed["status"] == "available"
    assert saved["version"] == VERSION
    assert len(saved["visualTransitions"]) == 1
    assert source.read_bytes() == before
    assert main(args) == 2  # Existing reports are never overwritten.


def test_cli_rejects_output_outside_workspace(cached_workspace, tmp_path):
    from adventure_intelligence.cli import main
    workspace, asset_id, _ = cached_workspace
    destination = tmp_path / "invalid.json"
    assert main(["--workspace", str(workspace), "temporal-lite",
                 "--asset", asset_id, "--output", str(destination)]) == 2
    assert not destination.exists()

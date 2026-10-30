"""PTS and conservative tracking regression tests with real FFmpeg decode."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from adventure_intelligence.frame_timing import extract_frame_png
from adventure_intelligence.semantic_jobs import frame_descriptor
from adventure_intelligence.tracking import associate_boxes


def obj(label="ray", left=10, right=50):
    return {
        "label": label, "score": .9, "reviewed": True,
        "bboxFormat": "xyxy", "coordinateSpace": "sampled_image_pixels",
        "bbox": {"xmin": left, "ymin": 10, "xmax": right, "ymax": 50}
    }


def test_unambiguous_temporal_track_hypothesis():
    frames = [
        {"tMs": 0, "objects": [obj()]},
        {"tMs": 1000, "objects": [obj(left=12, right=52)]},
        {"tMs": 2000, "objects": [obj("shark")]},
    ]
    summary = associate_boxes(frames)
    assert len(summary["tracks"]) == 2
    assert frames[0]["objects"][0]["trackId"] == frames[1]["objects"][0]["trackId"]
    assert frames[2]["objects"][0]["trackId"] != frames[0]["objects"][0]["trackId"]
    assert summary["tracks"][0]["detectionCount"] == 2
    assert not summary["identityVerified"]
    assert not frames[0]["objects"][0]["reviewed"]


def test_ambiguous_similar_animals_do_not_force_identity_match():
    frames = [
        {"tMs": 0, "objects": [obj(), obj()]},
        {"tMs": 1000, "objects": [obj()]},
    ]
    tracks = associate_boxes(frames)["tracks"]
    assert len(tracks) == 3
    assert frames[1]["objects"][0]["trackId"] not in {
        frames[0]["objects"][0]["trackId"],
        frames[0]["objects"][1]["trackId"]
    }


def test_track_breaks_on_gap_and_invalid_bounding_box():
    frames = [
        {"tMs": 0, "objects": [obj()]},
        {"tMs": 13000, "objects": [obj()]},
        {"tMs": 14000, "objects": [{"label": "ray", "bbox": {"xmin": 1, "ymin": 1,
                                               "xmax": 1, "ymax": 5}}]},
    ]
    assert len(associate_boxes(frames)["tracks"]) == 2
    assert "trackId" not in frames[2]["objects"][0]


def test_tracker_rejects_nonmatching_coordinate_spaces():
    frames = [
        {"tMs": 0, "objects": [obj()]},
        {"tMs": 1000, "objects": [{**obj(), "coordinateSpace": "normalized_image"}]},
    ]
    assert len(associate_boxes(frames)["tracks"]) == 2


def test_frame_descriptor_preserves_independent_time_evidence():
    class Provider:
        def describe(self, raw):
            return {"objects": [obj()], "embedding": [1., 0.], "keypoints": []}
    measured = {"requestedMs": 1330, "decodedPts": 20480,
                "ptsTimeBase": "1/14336", "decodedPtsMs": 1429,
                "timestampBasis": "decoded_frame_pts"}
    result = frame_descriptor(Provider(), b"image", 1330, measured)
    assert result["tMs"] == 1330
    assert result["requestedMs"] == 1330
    assert result["decodedPtsMs"] == 1429
    assert result["ptsTimeBase"] == "1/14336"
    assert result["objects"][0]["reviewed"] is False


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg not installed")
def test_decoded_frame_pts_not_assumed_to_equal_seek_time(tmp_path):
    video = tmp_path / "bframes.mp4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=7",
        "-t", "3", "-c:v", "libx264", "-bf", "2", str(video)
    ], check=True)
    raw, timing = extract_frame_png(video, requested_ms=1330)
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    assert timing["requestedMs"] == 1330
    assert timing["decodedPts"] is not None
    assert timing["ptsTimeBase"] is not None
    assert timing["decodedPtsMs"] is not None
    assert 1330 <= timing["decodedPtsMs"] <= 1500
    assert timing["decodedPtsMs"] != 1330
    assert timing["timestampBasis"] == "decoded_frame_pts"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg not installed")
def test_tracking_and_timeline_full_semantic_pipeline(tmp_path):
    from adventure_intelligence.indexing import index_folder
    from adventure_intelligence.semantic_jobs import run_semantics
    from adventure_intelligence.timeline import generate_timelines
    from adventure_intelligence.catalog import connect
    from adventure_intelligence.analyze import analyze
    pytest.importorskip("PIL")
    folder = tmp_path / "dji"
    folder.mkdir()
    video = folder / "DJI_0123.MP4"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=7",
        "-t", "3", "-c:v", "libx264", "-bf", "2", str(video)
    ], check=True)
    workspace = tmp_path / "workspace"
    asset_id = index_folder(folder, workspace, activity="snorkeling")["asset_ids"][0]
    assert analyze(workspace)["analyzed"] == 1

    class Stub:
        name = "tracking-test"
        version = "1"
        def describe(self, image):
            return {"objects": [obj()], "embedding": [1., 0.]}

    output = run_semantics(workspace, Stub(), sample_seconds=1, asset_ids={asset_id})
    assert output["processed"] == 1 and output["frames_computed"] == 3
    semantic_file = next((workspace / "semantics").glob("*.json"))
    doc = json.loads(semantic_file.read_text())
    assert doc["tracking"]["tracks"][0]["detectionCount"] == 3
    assert doc["frames"][1]["timestampBasis"] == "decoded_frame_pts"
    assert doc["frames"][1]["decodedPtsMs"] is not None
    assert len({f["objects"][0]["trackId"] for f in doc["frames"]}) == 1
    timeline_path = generate_timelines(workspace)["paths"][0]
    timeline = json.loads(open(timeline_path).read())
    assert len(timeline["semanticTracks"]) == 1
    observations = [o for b in timeline["bins"] for o in b["objects"]]
    assert len(observations) == 3
    assert all(o["timestampBasis"] == "decoded_frame_pts" for o in observations)
    assert observations[0]["trackHypothesis"] is True
    assert run_semantics(workspace, Stub(), sample_seconds=1)["cached"] == 1

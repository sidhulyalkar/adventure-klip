"""Context-aware highlights never infer breathing from underwater camera signals."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from adventure_intelligence.acoustics import acoustic_changes, audio_series, analyze_audio
from adventure_intelligence.submersion import (
    submersion_samples, continuous_underwater_intervals, continuity_report,
)
from adventure_intelligence.editorial_quality import (
    manual_exclusions, subtract_exclusions, editorial_candidates,
)
from adventure_intelligence.semantic_jobs import frame_descriptor, encoded, digest
from adventure_intelligence.catalog import connect
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.storyboard import plan_storyboard


def scene(t, submerged=True):
    return {
        "tMs": t, "sceneScores": {
            "method": "siglip2_image_text_cosine",
            "similarities": {
                "submerged": .60 if submerged else .25,
                "at_surface": .20 if submerged else .70,
                "ski_lift": .20,
                "ski_downhill": .55,
                "bike_riding": .45,
                "bike_stopped": .20,
            },
        },
    }


def test_underwater_intervals_break_at_waterline_and_bridge_one_uncertain_frame():
    frames = [scene(t) for t in (0, 2000, 4000, 6000, 8000, 10000)]
    frames += [scene(12000, False)]
    frames += [scene(t) for t in (14000, 16000, 18000, 20000, 22000, 24000)]
    samples = submersion_samples(frames)
    spans = continuous_underwater_intervals(samples, duration_ms=26000)
    assert len(spans) == 2
    assert spans[0]["endMs"] < 12000 < spans[1]["startMs"]
    assert all(s["status"] == "proposed_unverified" for s in spans)
    assert all("breathing assessment" in s["warning"] for s in spans)
    assert not continuous_underwater_intervals(
        submersion_samples([scene(0), scene(2000)]), duration_ms=6000)


def test_audio_spike_is_evidence_not_breathing_or_automatic_interruption():
    signals = [
        {"t_ms": i*1000, "end_ms": (i+1)*1000,
         "rms": (.28 if i == 7 else .004)}
        for i in range(12)
    ]
    spikes = acoustic_changes(signals)
    assert len(spikes) == 1 and spikes[0]["startMs"] == 7000
    assert spikes[0]["classification"] == "unknown_acoustic_cause"
    frames = [scene(t) for t in range(0, 12000, 2000)]
    segments = continuous_underwater_intervals(
        submersion_samples(frames), duration_ms=12000)
    assert len(segments) == 1
    # A transient never breaks a visually consistent underwater interval.


def test_manual_lift_and_surface_intervals_are_hard_exclusions():
    from adventure_intelligence.editorial_quality import NEGATIVE_LABELS
    for activity, kind in (("ski", "ski_lift"),
                           ("snorkeling", "surface_break")):
        events = [{"origin": "manual", "kind": kind,
                   "start_ms": 5000, "end_ms": 15000}]
        intervals = manual_exclusions(events, activity)
        allowed = subtract_exclusions(0, 30000, intervals)
        assert allowed
        assert all(not (a < 15000 and 5000 < b) for a, b in allowed)
        assert kind in NEGATIVE_LABELS[activity]


def test_context_descriptors_preserve_uncalibrated_similarity_values():
    class Provider:
        def describe(self, raw):
            return {"embedding": None, "objects": [], "keypoints": [],
                    "sceneScores": scene(0)["sceneScores"]}
    result = frame_descriptor(Provider(), b"test", 1234)
    assert result["sceneScores"]["method"] == "siglip2_image_text_cosine"
    assert "submerged" in result["sceneScores"]["similarities"]
    assert result["embedding"] is None


def setup_asset(tmp_path, monkeypatch, activity):
    source = tmp_path / "originals"
    source.mkdir()
    original = source / "DJI_VIDEO.MP4"
    original.write_bytes(b"original video bytes do not change")
    monkeypatch.setattr(
        "adventure_intelligence.indexing.probe",
        lambda p: {"duration": 30, "audio": False, "capture_time": None},
    )
    work = tmp_path / "workspace"
    aid = index_folder(source, work, activity=activity)["asset_ids"][0]
    with connect(work) as db:
        db.execute("""INSERT INTO episodes
         (id,asset_id,kind,start_ms,end_ms,score,event_ids_json,evidence_json,origin,version)
         VALUES (?,?,?,?,?,?,?,?,?,?)""",
         ("ep-test", aid, "sustained_motion_candidate", 0, 30000, .99,
          "[]", "{}", "derived", "test"))
    return work, aid, original


def store_context(work, aid, frames):
    config_id = "context-test"
    filename = "context-fixture.json"
    doc = {"assetId": aid, "configId": config_id, "frames": frames}
    (work / "semantics").mkdir(parents=True, exist_ok=True)
    raw = encoded(doc)
    (work / "semantics" / filename).write_text(raw)
    with connect(work) as db:
        db.execute("""INSERT INTO semantic_jobs
            (asset_id,config_id,config_json,status,filename,sha256)
            VALUES (?,?,'{}','completed',?,?)""",
            (aid, config_id, filename, digest(raw)))
        db.execute("INSERT INTO semantic_active VALUES (?,?,?)",
                   (aid, "context", config_id))


def test_snorkel_context_restricts_auto_plan_to_underwater_windows(tmp_path, monkeypatch):
    work, aid, original = setup_asset(tmp_path, monkeypatch, "snorkeling")
    frames = [scene(t, False) for t in (0, 2000, 4000, 6000)]
    frames += [scene(t) for t in (10000, 12000, 14000, 16000, 18000, 20000,
                                  22000, 24000, 26000, 28000)]
    store_context(work, aid, frames)
    report = continuity_report(work)
    intervals = report["assets"][0]["intervals"]
    assert intervals and intervals[0]["startMs"] >= 8000
    with connect(work) as db:
        rows = [dict(x) for x in db.execute("SELECT * FROM episodes")]
        candidates, _ = editorial_candidates(db, work, rows, policy="automatic")
    assert candidates
    assert all(x["startMs"] >= intervals[0]["startMs"] for x in candidates)
    assert all("underwaterContinuity" in x for x in candidates)
    assert all(x["requiresReview"] for x in candidates)
    assert original.read_bytes() == b"original video bytes do not change"


def test_context_plan_ranks_short_ski_windows_instead_of_full_motion_span(tmp_path, monkeypatch):
    work, aid, original = setup_asset(tmp_path, monkeypatch, "ski")
    plan = plan_storyboard(work, "motion-review", max_seconds=30,
                           ranking="context")
    assert plan["editor"]["version"] == "storyboard-v2"
    assert plan["segments"]
    assert all(x["duration"] <= 18 for x in plan["segments"])
    assert all(x["requiresReview"] for x in plan["segments"])
    assert original.read_bytes() == b"original video bytes do not change"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg missing")
def test_acoustics_handles_video_without_sound_and_with_synthetic_tone(tmp_path):
    quiet = tmp_path / "no_audio.mp4"
    sound = tmp_path / "with_audio.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=7",
        "-t", "2", "-c:v", "mpeg4", str(quiet)], check=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=7",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=8000",
        "-t", "2", "-c:v", "mpeg4", "-c:a", "aac",
        "-shortest", str(sound)], check=True)
    assert audio_series(quiet) == []
    rows = audio_series(sound)
    assert len(rows) >= 1
    assert all(0 <= r["rms"] <= 1 for r in rows)
    assert all(r["interpretation"].endswith("breathing assessment")
               for r in rows)

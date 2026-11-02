"""Synthetic temporal signals and video exercise the optional camera-motion stage."""
import shutil
import subprocess

import pytest

from adventure_intelligence.vision import detect_flow_events, flow_series
from adventure_intelligence.episodes import build_episodes
from adventure_intelligence.catalog import connect
from adventure_intelligence.ingest import ingest
from adventure_intelligence.analyze import analyze
from adventure_intelligence.reels import plan_reel


def sample(i, vx, change=0.16, coherence=.85):
    return {"t_ms":i*250,"change":change,"flow_x":vx,"flow_y":0,
            "flow_magnitude":abs(vx),"flow_coherence":coherence,"residual_motion":.1}


def test_alternating_camera_motion_is_grouped_but_not_mislabeled_as_carving():
    samples=[sample(i,(2 if (i//8)%2 == 0 else -2)) for i in range(80)]
    events=detect_flow_events("asset",samples)
    assert {e["kind"] for e in events} == {
        "rhythmic_camera_sweep", "sustained_camera_motion"}
    assert all("Not a validated" in e["evidence"]["warning"] for e in events)
    episodes=build_episodes("asset",[
        {**e,"evidence_json":"{}"} for e in events],20000)
    kinds={ep["kind"] for ep in episodes}
    assert "camera_rhythm_candidate" in kinds
    assert "sustained_motion_candidate" in kinds
    assert "carving_flow" not in kinds
    assert "jump_chain" not in kinds


def test_noisy_sign_flips_and_static_footage_are_not_turn_sequences():
    still=[sample(i,0,change=0.001,coherence=0) for i in range(48)]
    assert detect_flow_events("still",still)==[]
    jitter=[sample(i, (2 if i%2 else -2),coherence=.2) for i in range(60)]
    assert "rhythmic_camera_sweep" not in {e["kind"] for e in detect_flow_events("jitter",jitter)}


def test_multiple_distinct_camera_sweep_chains():
    a=[sample(i,(2 if (i//8)%2==0 else -2)) for i in range(32)]
    pause=[sample(i,0,change=.001,coherence=0) for i in range(32,60)]
    b=[sample(i,(2 if ((i-60)//8)%2==0 else -2)) for i in range(60,92)]
    rhythmic=[e for e in detect_flow_events("asset",a+pause+b)
              if e["kind"] == "rhythmic_camera_sweep"]
    assert len(rhythmic)==2
    assert rhythmic[0]["end_ms"] < rhythmic[1]["start_ms"]


def test_reject_duplicate_or_non_increasing_sample_time():
    with pytest.raises(ValueError, match="increase strictly"):
        detect_flow_events("x",[sample(1,2)]*6)


@pytest.mark.skipif(not shutil.which("ffmpeg"),reason="FFmpeg not available")
def test_flow_extraction_and_cached_separate_analysis(tmp_path):
    pytest.importorskip("cv2"); pytest.importorskip("numpy")
    src=tmp_path/"camera";src.mkdir()
    recording=src/"DJI_0003.mp4"
    subprocess.run(["ffmpeg","-loglevel","error","-y","-f","lavfi","-i",
        "testsrc2=size=160x90:rate=12","-t","4","-c:v","libx264",str(recording)],check=True)
    values=flow_series(recording)
    assert len(values) >= 10
    assert all(0<=v["change"]<=1 for v in values)
    assert all("flow_x" in v and "flow_coherence" in v for v in values)
    work=tmp_path/"work"
    assert ingest(src,work,activity="ski")["imported"]==1
    assert analyze(work,detector_name="flow")["analyzed"]==1
    assert analyze(work,detector_name="flow")["cached"]==1
    assert analyze(work,detector_name="baseline")["analyzed"]==1
    with connect(work) as db:
        assert {r["stage"] for r in db.execute("SELECT stage FROM analyses")} == {"flow","motion"}
        versions={r["stage"]:r["version"] for r in db.execute("SELECT * FROM analyses")}
        assert "optical-flow" in versions["flow"] and "pixel-change" in versions["motion"]
    plan=plan_reel(work,"motion-review",20)
    assert plan["status"] == "draft"
    assert "not a confirmed athletic maneuver" in plan["disclaimer"]

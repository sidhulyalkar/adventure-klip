"""Runs with FFmpeg in CI; creates synthetic video, never personal footage."""
import json
import shutil
import subprocess
import pytest
from adventure_intelligence.ingest import ingest
from adventure_intelligence.analyze import analyze
from adventure_intelligence.catalog import connect
from adventure_intelligence.curation import import_labels
from adventure_intelligence.reels import plan_reel,render_reel

@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),reason="FFmpeg unavailable")
def test_complete_synthetic_media_pipeline(tmp_path):
    src=tmp_path/"camera";src.mkdir()
    video=src/"DJI_0001.mp4"
    subprocess.run(["ffmpeg","-loglevel","error","-y","-f","lavfi","-i",
          "testsrc2=size=160x90:rate=12","-t","5","-c:v","libx264",str(video)],check=True)
    work=tmp_path/"work"
    stats=ingest(src,work,activity="ski")
    assert stats["errors"]==[]
    assert analyze(work)["analyzed"]==1
    assert analyze(work)["cached"]==1
    with connect(work) as db:
        aid=db.execute("SELECT id FROM assets").fetchone()[0]
    labels={"asset_id":aid,"events":[{"kind":"jump","start_ms":250,"end_ms":700},
             {"kind":"jump","start_ms":1900,"end_ms":2300}]}
    lab=tmp_path/"labels.json";lab.write_text(json.dumps(labels))
    import_labels(work,lab)
    plan=plan_reel(work,"ski-highlights",30)
    assert len(plan["segments"])==1
    rendered=tmp_path/"reel.mp4"
    assert render_reel(work,plan,rendered)["segments"]==1
    assert rendered.stat().st_size>1000

"""Real synthetic FFmpeg regression for source-safe, resumed dense-frame extraction.

Only tiny, generated test videos are used. No actual DJI footage, models,
network calls or external drives needed.
"""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.ingest import sha_file
from adventure_intelligence.moment_adaptive_inspection import VERSION as PLAN_VERSION
from adventure_intelligence.moment_dense_decode import (
    VERSION, decode_inspection,
)
from adventure_intelligence.workspace import init


@pytest.fixture
def tiny_plan(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg unavailable")
    ws=tmp_path/"workspace"
    init(ws)
    (ws/"reports").mkdir()
    videos=tmp_path/"recordings"
    videos.mkdir()
    originals=[]
    aids=[]
    for index in range(2):
        path=videos/f"source_{index}.mp4"
        # Second stream has a non-zero PTS origin to check source-relative
        # mapping (frame PTS minus video stream start time).
        command=[
            "ffmpeg","-v","error","-nostdin","-y","-f","lavfi",
            "-i","testsrc=size=160x96:rate=12:duration=13",
        ]
        if index==1:
            command += ["-vf","setpts=PTS+5/TB"]
        command += ["-c:v","mpeg4","-q:v","4",str(path)]
        subprocess.run(command,check=True,capture_output=True,timeout=60)
        originals.append(path)
        aids.append(sha_file(path))
    with connect(ws) as db:
        for path,aid in zip(originals,aids):
            db.execute(
                "INSERT INTO assets(id,sha256,stored_path,size_bytes,suffix,"
                "duration,activity,metadata_json) VALUES(?,?,?,?,?,?,?,?)",
                (aid,aid,str(path),path.stat().st_size,".mp4",13.,"unknown","{}"))
    times=list(range(2000,4000,500))+list(range(4000,8000,250))+list(range(8000,10000,500))
    plan={
        "version":PLAN_VERSION,
        "plannedClips":2,
        "requestedFrames":len(times)*2,
        "inspections":[{
            "status":"inspection_requested",
            "sourceSha256":aid,
            "expandedStartMs":2000,"expandedEndMs":10000,
            "coreStartMs":4000,"coreEndMs":8000,
            "selectionType":"exploration" if i else "ranked",
            "requestedTimesMs":times,
            "plannedFrameCount":len(times),
        } for i,aid in enumerate(aids)],
    }
    plan_path=ws/"reports"/"kmoment-dense-inspection-plan-v1.json"
    plan_path.write_text(json.dumps(plan))
    return ws,plan_path,originals,aids,plan,times


def _run(ws,path,*,name="kmoment-dense-test-001",max_clips=2,
         max_frames=100,run=True,ack_original_reads=True):
    return decode_inspection(
        ws,path,run_name=name,
        max_clips=max_clips,max_frames=max_frames,
        run=run,ack_original_reads=ack_original_reads,
        timeout_seconds=90,width=192,
    )


def test_preflight_does_not_open_source_or_create_outputs(tiny_plan):
    ws,path,originals,_,_,times=tiny_plan
    before=[f.read_bytes() for f in originals]
    result=_run(ws,path,run=False,ack_original_reads=False)
    assert result["preflightOnly"] is True
    assert result["sourceVideoRead"] is False
    assert result["requestedFrames"]==2*len(times)
    assert result["distinctSourceCount"]==2
    assert not (ws/"reports"/"kmoment-dense-test-001").exists()
    assert [f.read_bytes() for f in originals]==before
    with pytest.raises(ValueError,match="ack-original-reads"):
        _run(ws,path,run=True,ack_original_reads=False)


def test_batched_decode_full_sha_receipts_and_resume(tiny_plan):
    ws,path,originals,aids,_,times=tiny_plan
    before=[f.read_bytes() for f in originals]
    first=_run(ws,path)
    assert first["sourceVideoRead"] is True
    assert first["failures"]==[]
    assert first["clipsCompleted"]==2
    assert first["framesObserved"]==2*len(times)
    assert all(c["status"]=="decoded_source_sha_verified" for c in first["clips"])
    output=ws/"reports"/"kmoment-dense-test-001"
    for clip in first["clips"]:
        doc=json.loads((output/clip["receiptRelativePath"]).read_text())
        assert doc["version"]==VERSION
        assert doc["sourceVerifiedBeforeAndAfter"] is True
        assert doc["nativeOriginalFramePtsMeasured"] is False
        assert doc["observedFrames"]==len(times)
        assert doc["unobservedRequestedTimesMs"]==[]
        assert all(abs(r["gridOffsetFromRequestMs"])<=135 for r in doc["frames"])
        assert all((output/clip["receiptRelativePath"]).parent.joinpath(
            r["relativePath"]).is_file() for r in doc["frames"])
    second=_run(ws,path)
    assert second["failures"]==[]
    assert second["clipsCompleted"]==2
    assert all(c["status"]=="reused_verified_derived_cache" for c in second["clips"])
    assert [f.read_bytes() for f in originals]==before


def test_source_change_causes_failure_not_successful_reuse(tiny_plan):
    ws,path,originals,_,_,_=tiny_plan
    assert not _run(ws,path)["failures"]
    # Original is modified here deliberately by test, never by Klip.
    originals[0].write_bytes(originals[0].read_bytes()+b"changed")
    resumed=_run(ws,path)
    assert resumed["failures"]
    assert len(resumed["clips"])==1
    assert resumed["sourceCountCompleted"]==1


def test_corrupted_derived_artifact_refuses_overwrite(tiny_plan):
    ws,path,_,_,_,_=tiny_plan
    first=_run(ws,path)
    rec=ws/"reports"/"kmoment-dense-test-001"/first["clips"][0]["receiptRelativePath"]
    doc=json.loads(rec.read_text())
    image=rec.parent/doc["frames"][0]["relativePath"]
    image.write_bytes(b"changed")
    again=_run(ws,path)
    assert len(again["failures"])==1
    assert len(again["clips"])==1
    assert image.read_bytes()==b"changed"  # no deletion or auto repair
    with pytest.raises(ValueError,match="Existing run settings differ"):
        _run(ws,path,max_clips=1)


def test_invalid_plan_and_budget_fail_before_source_reads(tiny_plan):
    ws,path,originals,_,plan,times=tiny_plan
    with pytest.raises(ValueError,match="frame cap"):
        _run(ws,path,max_frames=5)
    plan["inspections"][0]["expandedEndMs"]=50_000
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError,match="Invalid bounded"):
        _run(ws,path,run=False)
    path.unlink()
    other=ws.parent/"elsewhere.json"
    other.write_text(json.dumps(plan))
    path.symlink_to(other)
    with pytest.raises(ValueError,match="regular JSON"):
        _run(ws,path,run=False)
    assert all(f.exists() for f in originals)

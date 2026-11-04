"""Regression qualification: broken browser playback and explicit local AI triage."""
from __future__ import annotations

from pathlib import Path
import json

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.archive_pilot import run_archive_pilot
from adventure_intelligence.archive_review import seed_archive_reviews
from adventure_intelligence.benchmark_feedback import load_benchmark,benchmark_feedback_status
from adventure_intelligence.studio import SourceStream,make_handler
from adventure_intelligence import studio_preview_judge as preview
from adventure_intelligence.archive_local_triage import run_local_triage


@pytest.fixture
def testbench(tmp_path,monkeypatch):
    originals={}
    media=tmp_path/"external_DJI"
    media.mkdir()
    for i in range(8):
        p=media/f"DJI_{i:03}.MP4"
        raw=(f"original-{i}-".encode())*500
        p.write_bytes(raw)
        originals[p.name]=raw
    ws=tmp_path/"workspace"
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda p:{"duration":40.,"audio":False,"capture_time":None})
    index_folder(media,ws,activity="unknown")
    with connect(ws) as db:
        ids=list(db.execute("SELECT id FROM assets ORDER BY stored_path"))
        for i,item in enumerate(ids):
            db.execute("UPDATE assets SET activity=? WHERE id=?",
                (("snorkeling","ski","bike","unknown")[i%4],item["id"]))
    run_archive_pilot(ws,batch_size=8,max_frames=8)
    review=ws/"reports"/"review"
    seed_archive_reviews(ws,ws/"reports"/"archive-pilot-v1",review,max_assets=8)
    return ws,media,originals,review/"assignments.json"


def _fake_transcoder(cmd,**kwargs):
    # Writes a small local derivative for the single synthetic FFmpeg call.
    class Result:
        returncode=0
    Path(cmd[-1]).write_bytes(b"\x00\x00\x00\x18ftypmp42"+b"z"*256)
    return Result()


def test_source_verified_preview_cached_by_sha_without_changing_media(testbench,monkeypatch):
    ws,media,originals,manifest=testbench
    bench=load_benchmark(ws,manifest)
    entry=next(iter(bench["assignments"].values()))
    monkeypatch.setattr(preview,"require_tool",lambda name:"ffmpeg")
    monkeypatch.setattr(preview.subprocess,"run",_fake_transcoder)
    stream=SourceStream(ws)
    first=preview.generate_preview(ws,stream,entry)
    assert first["status"]=="prepared"
    assert first["videoSourceModified"] is False
    second=preview.generate_preview(ws,stream,entry)
    assert second["status"]=="cached"
    path,receipt=preview.preview_paths(ws,entry["assetId"],
                                     entry["startMs"],entry["endMs"])
    assert path.is_file() and receipt.is_file()
    assert preview._check_cached(path,receipt,aid=entry["assetId"],
                                start_ms=entry["startMs"],end_ms=entry["endMs"])
    assert {p.name:p.read_bytes() for p in media.iterdir()}==originals


def test_ai_judge_schema_does_not_turn_sparse_frames_into_human_truth(tmp_path,monkeypatch):
    sample=tmp_path/"private-proxy.mp4"
    sample.write_bytes(b"sample"*60)
    monkeypatch.setattr(preview,"_jpeg_samples",
                        lambda file:["/9j/4AAQSkZJRg=="]*3)
    calls=[]
    def local_stub(payload):
        calls.append(payload)
        return {"message":{"content":json.dumps({
            "recommendation":"unsure","observedEvent":"uncertain",
            "summary":"A dark underwater form is visible",
            "reason":"Three frames cannot resolve an animal identification",
            "limitations":["No full motion viewed","No species verification"],
        })},"total_duration":1}
    suggestion=preview.local_judgment(sample,model="gemma3:4b",
                                      transport=local_stub)
    assert suggestion["recommendation"]=="unsure"
    assert suggestion["humanApprovalRequired"] is True
    assert suggestion["reviewedByHuman"] is False
    assert suggestion["speciesVerified"] is False
    assert suggestion["videoOrAudioReviewed"] is False
    assert suggestion["networkDestination"]=="127.0.0.1"
    assert len(calls)==1 and len(calls[0]["messages"][0]["images"])==3
    again=preview.local_judgment(sample,model="gemma3:4b",
                                 transport=lambda payload:pytest.fail("cache not used"))
    assert again["cached"] is True
    assert again["recommendation"]=="unsure"
    with pytest.raises(ValueError):
        preview._validate_judgment({"recommendation":"keep"})
    with pytest.raises(ValueError):
        preview.judge_preview(sample,model="http://external-host/")


def test_bounded_dry_run_does_not_decode_or_contact_local_model(testbench,monkeypatch):
    ws,media,originals,manifest=testbench
    monkeypatch.setattr("adventure_intelligence.archive_local_triage.generate_preview",
                        lambda *a,**kw:pytest.fail("dry-run must not read original"))
    monkeypatch.setattr("adventure_intelligence.archive_local_triage.local_judgment",
                        lambda *a,**kw:pytest.fail("dry-run must not call model"))
    out=run_local_triage(ws,manifest,model="gemma3:4b",limit=8,run=False)
    assert out["mode"]=="dry_run"
    assert out["selected"]==8
    assert out["humanLabelsWritten"]==0
    assert out["externalUploads"]==0
    assert out["selectionRoles"]["control_systematic"]>=1
    assert {p.name:p.read_bytes() for p in media.iterdir()}==originals


def test_bound_local_judge_never_writes_human_review(testbench,monkeypatch):
    ws,media,originals,manifest=testbench
    from adventure_intelligence import archive_local_triage as batch
    monkeypatch.setattr(batch,"generate_preview",
                        lambda root,source,a:{"status":"prepared"})
    monkeypatch.setattr(batch,"cached_judgment",lambda path,model:None)
    monkeypatch.setattr(batch,"local_judgment",lambda path,model:{
        "recommendation":"keep","summary":"possible animal","cached":False})
    out=batch.run_local_triage(ws,manifest,model="gemma3:4b",limit=4,run=True)
    assert out["status"]=="completed" and out["selected"]==4
    assert out["humanLabelsWritten"]==0
    assert benchmark_feedback_status(ws,manifest)["reviewedAssignments"]==0
    assert {p.name:p.read_bytes() for p in media.iterdir()}==originals

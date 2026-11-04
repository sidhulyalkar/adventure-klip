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



def test_local_studio_preview_routes_are_token_gated_and_range_seekable(
        testbench,monkeypatch):
    from http.client import HTTPConnection
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from adventure_intelligence.studio import make_handler
    from adventure_intelligence import studio as server_module
    ws,media,originals,manifest=testbench
    bench=load_benchmark(ws,manifest)
    a=next(iter(bench["assignments"].values()))
    monkeypatch.setattr(preview,"require_tool",lambda name:"ffmpeg")
    monkeypatch.setattr(preview.subprocess,"run",_fake_transcoder)
    monkeypatch.setattr(server_module,"local_judgment",lambda path,model:{
        "recommendation":"unsure","summary":"Possible underwater object",
        "reason":"Sparse frames cannot confirm identity",
        "limitations":["Unverified object"],"humanApprovalRequired":True,
        "cached":False})
    srv=ThreadingHTTPServer(
        ("127.0.0.1",0),make_handler(ws,"unit-token",manifest,"gemma3:4b"))
    srv.daemon_threads=True
    thread=Thread(target=srv.serve_forever,daemon=True)
    thread.start()
    def req(method,path,body=None,token=True,headers=None):
        conn=HTTPConnection("127.0.0.1",srv.server_port,timeout=15)
        h={"Host":f"127.0.0.1:{srv.server_port}"}
        if token:h["X-Klip-Token"]="unit-token"
        if headers:h.update(headers)
        if body is not None:
            body=json.dumps(body)
            h["Content-Type"]="application/json"
        conn.request(method,path,body=body,headers=h)
        response=conn.getresponse()
        result=response.status,response.read(),dict(response.getheaders())
        conn.close()
        return result
    try:
        route="/api/benchmark/preview-file/"+a["reviewId"]
        status,_,_=req("GET",route)
        assert status==404
        status,_,_=req("POST","/api/benchmark/prepare-preview",
                       {"reviewId":a["reviewId"]},token=False)
        assert status==403
        status,body,_=req("POST","/api/benchmark/prepare-preview",
                          {"reviewId":a["reviewId"]})
        assert status==200
        assert json.loads(body)["status"]=="preview_ready"
        status,bytes_out,headers=req("GET",route,headers={"Range":"bytes=0-63"})
        assert status==206 and len(bytes_out)==64
        assert headers["Content-Range"].startswith("bytes 0-63/")
        status,body,_=req("POST","/api/benchmark/local-judge",
                          {"reviewId":a["reviewId"]})
        assert status==200
        result=json.loads(body)
        assert result["suggestion"]["recommendation"]=="unsure"
        status,body,_=req("GET","/api/benchmark")
        assert status==200
        detail=json.loads(body)["previews"][a["reviewId"]]
        assert detail["ready"] is True
        with connect(ws) as db:
            assert db.execute("SELECT count(*) FROM benchmark_review_revisions").fetchone()[0]==0
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=4)
    assert {p.name:p.read_bytes() for p in media.iterdir()}==originals

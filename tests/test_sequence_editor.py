"""Curation funnel and sequence editing never mutate DJI source media."""
from __future__ import annotations
from http.server import ThreadingHTTPServer
import http.client
import json
import threading
from pathlib import Path

import pytest

from adventure_intelligence.indexing import index_folder
from adventure_intelligence.catalog import connect
from adventure_intelligence.examples import review_example,review_inventory
from adventure_intelligence.curation_queue import review_queue
from adventure_intelligence.sequence_editor import (
    sequence_state,save_sequence,review_combination,export_sequence)
from adventure_intelligence.studio import make_handler


@pytest.fixture
def sample(tmp_path,monkeypatch):
    original=tmp_path/"originals";original.mkdir()
    for name in ("DJI_A.MP4","DJI_B.MP4","DJI_C.MP4"):
        (original/name).write_bytes(("ORIGINAL-"+name).encode())
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda p:{"duration":50.0,"audio":False})
    work=tmp_path/"workspace"
    ids=index_folder(original,work,activity="ski")["asset_ids"]
    with connect(work) as db:
        for j,aid in enumerate(ids):
            for i,(start,end,score) in enumerate([
                    (0,16000,.97),(1200,16500,.94),(21000,37000,.9)]):
                db.execute("""INSERT INTO episodes
                  (id,asset_id,kind,start_ms,end_ms,score,event_ids_json,evidence_json,origin,version)
                  VALUES (?,?,?,?,?,?,?,?,?,?)""",
                  (f"ep-{j}-{i}",aid,"sustained_motion_candidate",start,end,score,
                   "[]","{}","derived","test"))
    before={p.name:p.read_bytes() for p in original.iterdir()}
    return work,ids,original,before


def assert_unchanged(original,before):
    assert {p.name:p.read_bytes() for p in original.iterdir()}==before


def test_queue_suppresses_overlapping_camera_proposals(sample):
    work,ids,original,before=sample
    queue=review_queue(work)
    assert queue["totalAssets"]==3
    assert queue["totalPages"]==1
    assert queue["moments"]
    assert len(queue["moments"])<=9
    assert all(x["reviewState"]=="unreviewed" for x in queue["moments"])
    assert all(x["lane"] in {"promising","needs_review","likely_filler"}
               for x in queue["moments"])
    one=[m for m in queue["moments"] if m["assetId"]==ids[0]]
    for i,a in enumerate(one):
        for b in one[i+1:]:
            overlap=max(0,min(a["endMs"],b["endMs"])-max(a["startMs"],b["startMs"]))
            assert overlap/min(a["endMs"]-a["startMs"],b["endMs"]-b["startMs"])<=.45
    with pytest.raises(ValueError):
        review_queue(work,page=-1)
    with pytest.raises(ValueError):
        review_queue(work,activity="wrong")
    assert_unchanged(original,before)


def test_explicit_review_removes_redundant_queue_candidate(sample):
    work,ids,original,before=sample
    candidate=review_queue(work)["moments"][0]
    review_example(work,asset_id=candidate["assetId"],
        start_seconds=candidate["startMs"]/1000,end_seconds=candidate["endMs"]/1000,
        label="highlight_moment",decision="confirmed",highlight="keep")
    next_queue=review_queue(work)["moments"]
    assert not any(m["assetId"]==candidate["assetId"] and
        m["startMs"]==candidate["startMs"] for m in next_queue)
    assert_unchanged(original,before)


def test_reel_assembly_revision_checks_review_reversal_and_export(sample):
    work,ids,original,before=sample
    refs=[]
    for i,aid in enumerate(ids[:2]):
        ref=review_example(work,asset_id=aid,start_seconds=1,end_seconds=11,
            label="highlight_moment",decision="confirmed",highlight="keep",note="Great sequence")
        refs.append(ref["exampleId"])
    initial=sequence_state(work)
    assert len(initial["bank"])==2
    assert not initial["revisionId"]
    one=save_sequence(work,entries=[{"exampleId":refs[1]},{"exampleId":refs[0]}],
                      expected_revision=None,name="Ski day")
    assert one["status"]=="saved"
    with pytest.raises(ValueError,match="another tab"):
        save_sequence(work,entries=[],expected_revision=None)
    current=sequence_state(work)
    assert [e["exampleId"] for e in current["entries"]]==refs[::-1]
    assert current["totalSeconds"]==20
    assert len(current["joinHints"])==1
    assert current["joinHints"][0]["visualSimilarity"] is None
    assert current["joinHints"][0]["status"]=="unverified_editorial_evidence"
    feedback=review_combination(work,sequence_revision=one["revisionId"],
        left_example_id=refs[1],right_example_id=refs[0],
        verdict="reject",reason="continuity",note="Abrupt change in terrain")
    assert feedback["status"]=="recorded"
    with pytest.raises(ValueError,match="Explain"):
        review_combination(work,sequence_revision=one["revisionId"],
            left_example_id=refs[1],right_example_id=refs[0],
            verdict="reject",reason="continuity",note="")
    with pytest.raises(ValueError,match="adjacent"):
        review_combination(work,sequence_revision=one["revisionId"],
            left_example_id=refs[0],right_example_id=refs[1],
            verdict="approve")
    exported=export_sequence(work,expected_revision=one["revisionId"])
    plan=json.loads(Path(exported["path"]).read_text())
    assert plan["status"]=="draft"
    assert plan["recipe"]=="manual-sequence"
    assert [x["sourceAssetId"] for x in plan["segments"]]==ids[:2][::-1]
    assert_unchanged(original,before)
    new=save_sequence(work,entries=[{"exampleId":refs[0]},{"exampleId":refs[1]}],
        expected_revision=one["revisionId"])
    assert new["revisionId"]!=one["revisionId"]
    with pytest.raises(ValueError,match="changed"):
        export_sequence(work,expected_revision=one["revisionId"])
    # Revoking a formerly Keep review must prevent exporting it.
    review_example(work,asset_id=ids[0],start_seconds=1,end_seconds=11,
        label="highlight_moment",decision="confirmed",highlight="skip",note="Too similar")
    with pytest.raises(ValueError,match="confirmed Keep"):
        export_sequence(work,expected_revision=new["revisionId"])
    assert_unchanged(original,before)


def test_unsafe_or_unapproved_sequence_references_rejected(sample):
    work,ids,original,before=sample
    with pytest.raises(ValueError,match="Only human-confirmed"):
        save_sequence(work,entries=[{"exampleId":"ex-"+"0"*24}],expected_revision=None)
    valid=review_example(work,asset_id=ids[0],start_seconds=2,end_seconds=8,
        label="highlight_moment",decision="confirmed",highlight="keep")["exampleId"]
    with pytest.raises(ValueError,match="Duplicate"):
        save_sequence(work,entries=[{"exampleId":valid},{"exampleId":valid}],
                      expected_revision=None)
    with pytest.raises(ValueError,match="one approved"):
        save_sequence(work,entries=[{"exampleId":valid,"sourceAssetId":ids[1]}],
                      expected_revision=None)
    with pytest.raises(ValueError,match="Invalid reel name"):
        save_sequence(work,entries=[],name="",expected_revision=None)
    assert_unchanged(original,before)


def test_studio_queue_sequence_static_assets_and_authenticated_api(sample):
    work,ids,original,before=sample
    server=ThreadingHTTPServer(("127.0.0.1",0),make_handler(work,"session-token"))
    server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    def request(method,path,payload=None,auth=True):
        conn=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=30)
        headers={"Host":f"127.0.0.1:{server.server_port}"}
        if auth:headers["X-Klip-Token"]="session-token"
        body=json.dumps(payload).encode() if payload is not None else None
        if payload is not None:headers["Content-Type"]="application/json"
        conn.request(method,path,body=body,headers=headers)
        response=conn.getresponse()
        raw=response.read();code=response.status
        conn.close()
        return code,raw
    try:
        for path,needle in (("/studio_editor.js",b"loadQueue"),
                            ("/editor.css",b".queue-card")):
            code,raw=request("GET",path)
            assert code==200 and needle in raw
        code,_=request("GET","/api/queue",auth=False)
        assert code==403
        code,raw=request("GET","/api/queue?page=0")
        assert code==200 and json.loads(raw)["moments"]
        code,raw=request("GET","/api/queue?page=oops")
        assert code==400
        code,raw=request("GET","/api/sequence")
        assert code==200 and not json.loads(raw)["bank"]
        code,raw=request("POST","/api/sequence",{"entries":[{"exampleId":"ex-"+"1"*24}]})
        assert code==400
        chosen=review_example(work,asset_id=ids[0],start_seconds=2,end_seconds=10,
            label="highlight_moment",decision="confirmed",highlight="keep")
        code,raw=request("POST","/api/sequence",{
            "entries":[{"exampleId":chosen["exampleId"]}],"expectedRevision":None,"name":"Great ski run"})
        assert code==200
        rev=json.loads(raw)["revisionId"]
        code,raw=request("POST","/api/sequence/export",{"expectedRevision":rev})
        assert code==200 and json.loads(raw)["status"]=="exported"
        code,_=request("POST","/api/sequence/export",{"expectedRevision":rev},auth=False)
        assert code==403
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
    assert_unchanged(original,before)



def test_queue_visual_stacks_are_hints_not_dropped_clips(sample):
    from adventure_intelligence.semantic_jobs import encoded,digest
    work,ids,original,before=sample
    semantic_dir=work/"semantics"
    semantic_dir.mkdir(exist_ok=True)
    with connect(work) as db:
        for idx,aid in enumerate(ids[:2]):
            config="clip-test-"+str(idx)
            doc={"assetId":aid,"configId":config,"provider":"siglip2",
                 "modelVersion":"test-v1",
                 "config":{"providerConfig":{
                    "resolvedRevision":"frozen-1","imageProcessor":{"size":224}}},
                 "frames":[{"tMs":t,"embedding":[1.0,0.0]}
                           for t in (1000,3000,6000,22000,26000,32000)]}
            raw=encoded(doc)
            filename=config+".json"
            (semantic_dir/filename).write_text(raw)
            db.execute("""INSERT INTO semantic_jobs
            (asset_id,config_id,config_json,status,filename,sha256)
            VALUES (?,?,?,'completed',?,?)""",(aid,config,"{}",filename,digest(raw)))
            db.execute("INSERT INTO semantic_active VALUES (?,?,?)",(aid,"siglip2",config))
    moments=review_queue(work)["moments"]
    assert len(moments)>=2
    same=[m for m in moments if m["assetId"] in ids[:2]]
    assert len(same)>=2
    assert any(m["similarSceneCount"]>0 for m in same)
    assert all(m["visualComparisonAvailable"] for m in same)
    assert all(m["reviewState"]=="unreviewed" for m in same)
    assert_unchanged(original,before)

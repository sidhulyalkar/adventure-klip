"""Qualification of human benchmark revisions and loopback source review APIs."""
from __future__ import annotations

from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import json

import pytest

from adventure_intelligence.archive_pilot import run_archive_pilot
from adventure_intelligence.archive_review import seed_archive_reviews, CRITERIA
from adventure_intelligence.benchmark_feedback import (
    load_benchmark,save_human_review,validate_human_review,
    benchmark_feedback_status,write_benchmark_feedback,import_human_reviews,
)
from adventure_intelligence.catalog import connect
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.studio import make_handler
from adventure_intelligence.cli import main


@pytest.fixture
def benchmark(tmp_path,monkeypatch):
    camera=tmp_path/"DJI_originals"
    camera.mkdir()
    originals={}
    for k in range(6):
        path=camera/f"DJI_20240101_{k:04d}_D.MP4"
        data=(f"immutable-source-{k:03d}".encode())*400
        path.write_bytes(data)
        originals[path.name]=data
    root=tmp_path/"workspace"
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda p:{"duration":130.2,"audio":False,"capture_time":None})
    index_folder(camera,root,activity="unknown")
    with connect(root) as db:
        rows=list(db.execute("SELECT id FROM assets ORDER BY stored_path"))
        for i,row in enumerate(rows):
            db.execute("UPDATE assets SET activity=? WHERE id=?",
                       (("snorkeling","ski","bike")[i%3],row["id"]))
        db.commit()
    run_archive_pilot(root,batch_size=6,max_frames=24,step_seconds=15)
    dest=root/"reports"/"review-first-six"
    seed_archive_reviews(root,root/"reports"/"archive-pilot-v1",dest,max_assets=6)
    manifest=dest/"assignments.json"
    doc=json.loads(manifest.read_text())
    assert doc["reviewIntervalCount"]>=8
    return root,manifest,doc,camera,originals


def _review(assignment,**changes):
    doc={"reviewId":assignment["reviewId"],"reviewDecision":"keep",
         "observedActivity":"snorkeling","observedEvent":True,
         "eventKind":"ray-like subject passes across frame",
         "notes":"Human-visible subject, taxon unresolved",
         "ratings":{name:None for name in CRITERIA},
         "rareTaxonVerified":False,"taxonReviewSource":None,
         "eventBoundaryAdjustmentMs":None}
    doc["ratings"].update({"eventSignificance":.75,
                           "subjectVisibility":.5,"ambientVisibility":.25,
                           "sharingValue":.75,"personalMeaning":1.0})
    doc.update(changes)
    return doc


def test_studio_review_persists_append_only_without_reprocessing(benchmark):
    root,filename,doc,camera,originals=benchmark
    first=doc["assignments"][0]
    b=load_benchmark(root,filename)
    review=_review(first)
    clean=validate_human_review(b,review)
    assert clean["sourceInterval"]["assetId"]==first["assetId"]
    assert clean["ratings"]["ambientVisibility"]==.25
    assert clean["ratings"]["subjectVisibility"]==.5
    assert clean["rareTaxonVerified"] is False
    a=save_human_review(root,filename,review)
    assert a["status"]=="saved"
    same=save_human_review(root,filename,review)
    assert same["status"]=="unchanged"
    assert same["revisionId"]==a["revisionId"]
    later=_review(first,reviewDecision="skip",notes="New interpretation")
    updated=save_human_review(root,filename,later)
    assert updated["status"]=="saved"
    assert updated["revisionId"]!=a["revisionId"]
    with connect(root) as db:
        rows=list(db.execute(
            "SELECT revision_id FROM benchmark_review_revisions "
            "WHERE benchmark_id=? AND review_id=? ORDER BY rowid",
            (a["benchmarkId"],first["reviewId"])))
        assert len(rows)==2
    result=benchmark_feedback_status(root,filename)
    assert result["reviewedAssignments"]==1
    assert result["unreviewedAssignments"]==len(doc["assignments"])-1
    assert result["reviewEntries"][first["reviewId"]]["reviewDecision"]=="skip"
    assert result["estimatedWholeArchiveRecall"] is None
    assert result["estimatedWholeArchivePrecision"] is None
    assert result["sourceVideosDecoded"]==0
    assert {p.name:p.read_bytes() for p in camera.iterdir()}==originals


def test_control_event_is_a_potential_miss_not_estimated_full_recall(benchmark):
    root,filename,doc,camera,originals=benchmark
    control=next(x for x in doc["assignments"]
                 if x["role"]=="control_systematic")
    save_human_review(root,filename,_review(control))
    result=benchmark_feedback_status(root,filename)
    assert len(result["potentiallyMissedControlMoments"])==1
    assert result["potentiallyMissedControlMoments"][0]["reviewId"]==control["reviewId"]
    row=result["bySportAndRole"][control["selectionGroup"]]["control_systematic"]
    assert row["eventYes"]==1
    assert row["eventNo"]==0
    assert row["keep"]==1
    assert result["estimatedWholeArchiveRecall"] is None
    assert result["estimatedWholeArchivePrecision"] is None
    folder=root/"reports"/"evaluation-first"
    written=write_benchmark_feedback(root,filename,folder)
    assert written["interestingControls"]==1
    assert (folder/"evaluation.json").is_file()
    assert (folder/"index.html").is_file()
    with pytest.raises(ValueError,match="new reports"):
        write_benchmark_feedback(root,filename,folder)


def test_validation_abstains_on_unreviewed_not_false_negative(benchmark):
    root,filename,doc,camera,originals=benchmark
    b=load_benchmark(root,filename)
    a=doc["assignments"][0]
    r=_review(a)
    for field,bad in [("observedEvent","unknown"),
                      ("reviewDecision","strongly_keep"),
                      ("observedActivity","paragliding"),
                      ("rareTaxonVerified",True),
                      ("taxonReviewSource","guess"),
                      ("eventBoundaryAdjustmentMs",50_000)]:
        changed={**r,field:bad}
        with pytest.raises(ValueError):
            validate_human_review(b,changed)
    for bad in [float("nan"),float("inf"),-0.1,1.1,True]:
        changed={**r,"ratings":{**r["ratings"],"eventSignificance":bad}}
        with pytest.raises(ValueError):
            validate_human_review(b,changed)
    empty={**r,"ratings":{},"reviewDecision":None,"observedEvent":None,
           "eventKind":None,"notes":""}
    with pytest.raises(ValueError,match="empty review"):
        validate_human_review(b,empty)
    assert benchmark_feedback_status(root,filename)["reviewedAssignments"]==0


def test_offline_import_is_atomic_idempotent_and_skips_unreviewed(benchmark):
    root,filename,doc,camera,originals=benchmark
    first,second=doc["assignments"][:2]
    reviews=[_review(first),_review(second,reviewDecision="uncertain")]
    reviews.append({"reviewId":doc["assignments"][2]["reviewId"],
                    "reviewDecision":None,"observedEvent":None,
                    "ratings":{name:None for name in CRITERIA},
                    "notes":""})
    export=root/"reports"/"offline-human.json"
    export.write_text(json.dumps(reviews))
    result=import_human_reviews(root,filename,export)
    assert result["savedRevisions"]==2
    assert result["blankRowsSkipped"]==1
    repeat=import_human_reviews(root,filename,export)
    assert repeat["savedRevisions"]==0
    assert repeat["unchanged"]==2
    with connect(root) as db:
        before=db.execute("SELECT COUNT(*) FROM benchmark_review_revisions").fetchone()[0]
        assert before==2
    bad=[_review(first,reviewDecision="skip"),_review(second,rareTaxonVerified=True)]
    export.write_text(json.dumps(bad))
    with pytest.raises(ValueError,match="Species verification"):
        import_human_reviews(root,filename,export)
    with connect(root) as db:
        after=db.execute("SELECT COUNT(*) FROM benchmark_review_revisions").fetchone()[0]
    assert before==after
    assert {p.name:p.read_bytes() for p in camera.iterdir()}==originals


def test_benchmark_cannot_be_misattributed_to_a_different_review_file(benchmark):
    root,filename,doc,camera,originals=benchmark
    arbitrary=root.parent/"outside"/"assignments.json"
    with pytest.raises(ValueError,match="under workspace"):
        load_benchmark(root,arbitrary)
    invalid=_review(doc["assignments"][0],reviewId="0"*20)
    with pytest.raises(ValueError,match="absent"):
        save_human_review(root,filename,invalid)


def test_loopback_studio_api_requires_session_token_and_serves_intervals(benchmark):
    root,filename,doc,camera,originals=benchmark
    token="unit-test-local-token-123"
    server=ThreadingHTTPServer(("127.0.0.1",0),make_handler(root,token,filename))
    server.daemon_threads=True
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        def request(method,path,body=None,authorized=True):
            conn=HTTPConnection("127.0.0.1",server.server_port,timeout=10)
            headers={"Host":f"127.0.0.1:{server.server_port}"}
            if authorized:
                headers["X-Klip-Token"]=token
            if body is not None:
                headers["Content-Type"]="application/json"
                body=json.dumps(body)
            conn.request(method,path,body=body,headers=headers)
            response=conn.getresponse()
            payload=response.read()
            conn.close()
            return response.status,payload
        status,html=request("GET","/benchmark",authorized=False)
        assert status==200
        assert b'Klip' in html
        assert b'benchmark.js' in html
        assert token.encode() in html
        status,_=request("GET","/api/benchmark",authorized=False)
        assert status==403
        status,payload=request("GET","/api/benchmark")
        assert status==200
        data=json.loads(payload)
        assert len(data["assignments"])==doc["reviewIntervalCount"]
        assert "subjectVisibility" in data["criteria"]
        first=data["assignments"][0]
        status,payload=request("POST","/api/benchmark/review",_review(first),
                               authorized=False)
        assert status==403
        status,payload=request("POST","/api/benchmark/review",_review(first))
        assert status==200,json.loads(payload)
        status,payload=request("GET","/api/benchmark/evaluation")
        assert status==200
        summary=json.loads(payload)
        assert summary["reviewedAssignments"]==1
        assert summary["estimatedWholeArchiveRecall"] is None
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
    assert {p.name:p.read_bytes() for p in camera.iterdir()}==originals


def test_cli_evaluation_and_import_contract(benchmark,tmp_path,capsys):
    root,filename,doc,camera,originals=benchmark
    review=root/"reports"/"test-export.json"
    review.write_text(json.dumps([_review(doc["assignments"][0])]))
    assert main(["--workspace",str(root),"archive-review-import",
                 "--assignments",str(filename),"--reviews",str(review)])==0
    result=json.loads(capsys.readouterr().out)
    assert result["savedRevisions"]==1
    output=root/"reports"/"evaluated"
    assert main(["--workspace",str(root),"archive-review-results",
                 "--assignments",str(filename),"--output-dir",str(output)])==0
    result=json.loads(capsys.readouterr().out)
    assert result["reviewed"]==1
    assert (output/"index.html").is_file()

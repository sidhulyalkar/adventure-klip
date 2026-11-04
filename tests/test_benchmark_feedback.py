"""Archive benchmark feedback stores only human choices and evaluates controls honestly."""
from __future__ import annotations

from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
import json
import threading

import pytest

from adventure_intelligence.archive_pilot import run_archive_pilot
from adventure_intelligence.archive_review import seed_archive_reviews,CRITERIA
from adventure_intelligence.benchmark_feedback import (
    load_benchmark,validate_human_review,save_human_review,
    benchmark_feedback_status,write_benchmark_feedback,
)
from adventure_intelligence.catalog import connect
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.studio import make_handler


@pytest.fixture
def sample(tmp_path,monkeypatch):
    originals=tmp_path/"camera"
    originals.mkdir()
    bytes_before={}
    for i in range(8):
        p=originals/f"DJI_{i+1:04d}.MP4"
        raw=(f"immutable-{i+1}".encode())*3000
        p.write_bytes(raw)
        bytes_before[p.name]=raw
    root=tmp_path/"workspace"
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda path:{"duration":90.0,"audio":False,
                                     "capture_time":None})
    index_folder(originals,root,activity="unknown")
    with connect(root) as db:
        assets=list(db.execute("SELECT id FROM assets ORDER BY stored_path"))
        for i,row in enumerate(assets):
            a=("snorkeling" if i<2 else "ski" if i<4 else
               "bike" if i<6 else "unknown")
            db.execute("UPDATE assets SET activity=? WHERE id=?",(a,row["id"]))
        db.commit()
    run_archive_pilot(root,batch_size=8,step_seconds=10,max_frames=16)
    folder=root/"reports"/"benchmark"
    seed_archive_reviews(root,root/"reports"/"archive-pilot-v1",folder,
                         max_assets=8,window_seconds=12)
    benchmark=folder/"assignments.json"
    return root,originals,bytes_before,benchmark


def _payload(review_id,decision="keep",observed=True):
    return {
        "reviewId":review_id,
        "reviewDecision":decision,
        "observedEvent":observed,
        "observedActivity":"snorkeling",
        "eventKind":"visible marine encounter",
        "notes":"Animal-like subject visibly crosses frame; species unverified.",
        "ratings":{k:(.75 if k in ("subjectVisibility","sharingValue") else None)
                   for k in CRITERIA},
    }


def test_save_revisions_are_append_only_idempotent_and_source_unchanged(sample):
    root,originals,bytes_before,filename=sample
    bench=load_benchmark(root,filename)
    rid=next(iter(bench["assignments"]))
    before=benchmark_feedback_status(root,filename)
    assert before["reviewedAssignments"]==0
    assert before["estimatedWholeArchiveRecall"] is None
    first=save_human_review(root,filename,_payload(rid))
    assert first["status"]=="saved"
    again=save_human_review(root,filename,_payload(rid))
    assert again["status"]=="unchanged"
    assert again["revisionId"]==first["revisionId"]
    changed=save_human_review(root,filename,
                              _payload(rid,"skip",False))
    assert changed["status"]=="saved"
    assert changed["revisionId"]!=first["revisionId"]
    with connect(root) as db:
        n=db.execute(
            "SELECT COUNT(*) FROM benchmark_review_revisions WHERE review_id=?",
            (rid,)).fetchone()[0]
        assert n==2
    after=benchmark_feedback_status(root,filename)
    assert after["reviewedAssignments"]==1
    assert after["reviewEntries"][rid]["reviewDecision"]=="skip"
    assert {x.name:x.read_bytes() for x in originals.iterdir()}==bytes_before


def test_informative_control_cannot_be_misreported_as_held_out_recall(sample):
    root,originals,bytes_before,filename=sample
    bench=load_benchmark(root,filename)
    control=next(a for a in bench["assignments"].values()
                 if a["role"]=="control_systematic")
    candidate=next(a for a in bench["assignments"].values()
                   if a["role"]=="candidate_hypothesis")
    save_human_review(root,filename,_payload(control["reviewId"],"keep",True))
    save_human_review(root,filename,_payload(candidate["reviewId"],"skip",False))
    report=benchmark_feedback_status(root,filename)
    assert report["estimatedWholeArchiveRecall"] is None
    assert report["estimatedWholeArchivePrecision"] is None
    assert report["reviewedAssignments"]==2
    assert len(report["potentiallyMissedControlMoments"])==1
    missing=report["potentiallyMissedControlMoments"][0]
    assert missing["reviewId"]==control["reviewId"]
    role=report["bySportAndRole"][control["selectionGroup"]]["control_systematic"]
    assert role["observedEventRate"]==1.0
    assert report["criterionMeans"]["subjectVisibility"]["assessed"]==2
    assert role["eventAssessedCount"]==1
    output=root/"reports"/"benchmark-evaluation-v1"
    result=write_benchmark_feedback(root,filename,output)
    assert result["interestingControls"]==1
    assert (output/"evaluation.json").is_file()
    assert (output/"index.html").is_file()
    with pytest.raises(ValueError,match="new reports"):
        write_benchmark_feedback(root,filename,output)


def test_unverified_species_missing_review_and_outside_path_are_rejected(sample):
    root,originals,bytes_before,filename=sample
    bench=load_benchmark(root,filename)
    rid=next(iter(bench["assignments"]))
    raw=_payload(rid)
    raw["ratings"]["subjectVisibility"]=float("nan")
    with pytest.raises(ValueError,match="subjectVisibility"):
        validate_human_review(bench,raw)
    raw=_payload(rid)
    raw["rareTaxonVerified"]=True
    with pytest.raises(ValueError,match="independent curated"):
        validate_human_review(bench,raw)
    raw=_payload(rid)
    raw["reviewId"]="absent"
    with pytest.raises(ValueError,match="absent"):
        save_human_review(root,filename,raw)
    raw={"reviewId":rid,"reviewDecision":None,"observedEvent":None}
    with pytest.raises(ValueError,match="empty review"):
        save_human_review(root,filename,raw)
    with pytest.raises(ValueError,match="Benchmark must be"):
        load_benchmark(root,root.parent/"outside.json")
    assert {p.name:p.read_bytes() for p in originals.iterdir()}==bytes_before


def test_studio_benchmark_api_is_token_protected_and_records_review(sample):
    root,originals,bytes_before,filename=sample
    token="synthetic-secret-for-only-this-test"
    handler=make_handler(root,token,filename)
    server=ThreadingHTTPServer(("127.0.0.1",0),handler)
    server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    conn=HTTPConnection("127.0.0.1",server.server_port,timeout=10)
    try:
        conn.request("GET","/benchmark")
        page=conn.getresponse()
        body=page.read().decode()
        assert page.status==200
        assert "Archive benchmark" in body
        assert token in body
        conn.request("GET","/benchmark.js")
        script=conn.getresponse()
        assert script.status==200
        assert "api/benchmark/review" in script.read().decode()
        conn.request("GET","/api/benchmark")
        unauthorized=conn.getresponse()
        unauthorized.read()
        assert unauthorized.status==403
        conn.request("GET","/api/benchmark",
                     headers={"X-Klip-Token":token})
        answer=conn.getresponse()
        assert answer.status==200
        assignments=json.loads(answer.read())["assignments"]
        rid=assignments[0]["reviewId"]
        payload=json.dumps(_payload(rid))
        conn.request("POST","/api/benchmark/review",body=payload,
                     headers={"X-Klip-Token":token,
                              "Content-Type":"application/json"})
        posted=conn.getresponse()
        response=json.loads(posted.read())
        assert posted.status==200
        assert response["status"]=="saved"
        conn.request("GET","/api/benchmark/evaluation",
                     headers={"X-Klip-Token":token})
        eva=conn.getresponse()
        report=json.loads(eva.read())
        assert eva.status==200
        assert report["reviewedAssignments"]==1
        assert {p.name:p.read_bytes() for p in originals.iterdir()}==bytes_before
    finally:
        conn.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)



def test_benchmark_identity_rejects_changed_time_even_when_review_id_unchanged(sample):
    root,originals,bytes_before,filename=sample
    original=filename.read_text()
    doc=json.loads(original)
    doc["assignments"][0]["startMs"]+=1000
    filename.write_text(json.dumps(doc))
    try:
        with pytest.raises(ValueError,match="identity differs"):
            load_benchmark(root,filename)
    finally:
        filename.write_text(original)
    assert {p.name:p.read_bytes() for p in originals.iterdir()}==bytes_before

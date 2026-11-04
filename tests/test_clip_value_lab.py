"""Pilot moment-value review: controls, source provenance, ratings, and Studio API."""
from __future__ import annotations

from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import json

import pytest

from adventure_intelligence.archive_pilot import run_archive_pilot
from adventure_intelligence.catalog import connect
from adventure_intelligence.clip_value_lab import (
    CRITERIA,make_value_packet,read_value_packet,save_value_review,
    value_lab_state,value_report,
)
from adventure_intelligence.cli import main
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.studio import make_handler


@pytest.fixture
def workspace(tmp_path,monkeypatch):
    camera=tmp_path/"DJI_source"
    camera.mkdir()
    originals={}
    for i in range(10):
        p=camera/f"DJI_{i:03d}.MP4"
        original=(f"source-{i}-".encode())*5000
        p.write_bytes(original)
        originals[p.name]=original
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda p:{"duration":60.555,"audio":False,
                                  "capture_time":None})
    root=tmp_path/"current"
    index_folder(camera,root,activity="unknown")
    with connect(root) as db:
        rows=list(db.execute("SELECT id,stored_path FROM assets ORDER BY stored_path"))
        for i,row in enumerate(rows):
            activity=("snorkeling" if i<3 else "ski" if i<6 else
                      "bike" if i<9 else "unknown")
            db.execute("UPDATE assets SET activity=? WHERE id=?",
                       (activity,row["id"]))
        asset=rows[0]["id"]
        aid2=rows[1]["id"]
        db.execute(
            "INSERT INTO episodes "
            "(id,asset_id,kind,start_ms,end_ms,score,event_ids_json,"
            "evidence_json,origin,version) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("ep-one",aid2,"wildlife_encounter",15000,27000,.8,"[]","{}",
             "derived","test-v1"))
        db.commit()
    first=run_archive_pilot(root,batch_size=8)
    assert first["status"]=="planned"
    # A local detector packet can offer a candidate but never a confirmed ID.
    report=root/"reports"/"ray-event-context-v2"
    report.mkdir(parents=True)
    (report/"manifest.json").write_text(json.dumps({
        "version":"event-context-embedding-v1",
        "assetId":asset,
        "events":[{"id":"event-context-01","group":"conflicting_labels_unverified",
                   "startMs":11000,"endMs":33000}],
    }))
    return root,camera,originals,asset


def _packet(workspace):
    root,*_=workspace
    return make_value_packet(root,root/"reports"/"archive-pilot-v1")


def _full_ratings(**changes):
    ratings={k:None for k in CRITERIA}
    ratings.update({
        "eventSignificance":3,"subjectVisibility":3,
        "imageQuality":2,"narrativeCompleteness":3,
        "visualNovelty":4,"learningValue":3,
        "investigationPotential":4,"uncertainty":2,
        "ambientClarity":1,"actionOutcome":3,
        "personalMeaning":4,"audienceAppeal":3,"watchAgain":4
    })
    ratings.update(changes)
    return ratings


def test_packet_reuses_initial_clips_and_one_random_control_per_video(workspace):
    root,camera,originals,asset=workspace
    result=_packet(workspace)
    assert result["status"]=="created"
    assert result["videoDecoded"] is False
    packet=read_value_packet(root)
    assert packet["sourcePlanId"]
    assert packet["packetId"]==result["packetId"]
    assert len(packet["moments"])<=24
    assert len(packet["moments"])>=16
    sources={m["selectionSource"] for m in packet["moments"]}
    assert "random_control" in sources
    assert "cached_event_hypothesis" in sources
    assert "episode_proposal" in sources
    assert "coverage_probe" in sources
    for aid in {m["assetId"] for m in packet["moments"]}:
        items=[m for m in packet["moments"] if m["assetId"]==aid]
        assert len(items)<=3
        assert sum(m["selectionSource"]=="random_control" for m in items)==1
        assert all(m["eventOccurrenceVerified"] is False for m in items)
    assert {p.name:p.read_bytes() for p in camera.iterdir()}==originals
    same=_packet(workspace)
    assert same["status"]=="existing"
    assert same["packetId"]==result["packetId"]


def test_ratings_append_only_round_trip_and_score_from_reviewed_only(workspace):
    root,camera,originals,asset=workspace
    _packet(workspace)
    packet=read_value_packet(root)
    candidate=next(m for m in packet["moments"]
                   if m["selectionGroup"]=="snorkeling"
                   and m["selectionSource"]=="cached_event_hypothesis")
    control=next(m for m in packet["moments"]
                 if m["selectionSource"]=="random_control")
    state=value_lab_state(root)
    assert state["reviewedCount"]==0
    unknown=value_report(root)
    assert all(not q for q in unknown["ranking"]["queues"].values())
    assert unknown["totalCount"]==len(packet["moments"])
    first=save_value_review(root,batch=1,moment_id=candidate["momentId"],
        decision="keep",event_observed="observed",ratings=_full_ratings(),
        note="Ray-like shape visible, exact animal unknown",
        improvement_note="Capture the approach with a wider field")
    assert first["status"]=="reviewed"
    duplicate=save_value_review(root,batch=1,moment_id=candidate["momentId"],
        decision="keep",event_observed="observed",ratings=_full_ratings(),
        note="Ray-like shape visible, exact animal unknown",
        improvement_note="Capture the approach with a wider field")
    assert duplicate["status"]=="unchanged"
    # A random control can contain something important; its review cannot
    # be fabricated as a negative simply because it was randomly sampled.
    save_value_review(root,batch=1,moment_id=control["momentId"],
        decision="later",event_observed="uncertain",ratings={
            "ambientClarity":4,"subjectVisibility":None
        })
    refreshed=value_lab_state(root)
    assert refreshed["reviewedCount"]==2
    assert refreshed["reviews"][candidate["momentId"]]["ratings"]["imageQuality"]==2
    scores=value_report(root)
    item=next(m for m in scores["ranking"]["moments"]
              if m["momentId"]==candidate["momentId"])
    assert item["signalsUsed"]["eventSignificance"]==.75
    assert item["signalsUsed"]["imageQuality"]==.5
    assert item["signalsUsed"]["verifiedTaxonRarity"] is None
    assert item["reviewEligibility"]["share"] is True
    assert item["reviewEligibility"]["autoPublish"] is False
    assert scores["reviewedBySelectionSource"]["random_control"]==1
    with connect(root) as db:
        assert db.execute("SELECT COUNT(*) FROM moment_value_revisions").fetchone()[0]==2
    updated=save_value_review(root,batch=1,moment_id=candidate["momentId"],
        decision="skip",event_observed="observed",ratings=_full_ratings(),
        note="Same subject, but too much camera shake")
    assert updated["previousRevisionId"]==first["revisionId"]
    with connect(root) as db:
        assert db.execute("SELECT COUNT(*) FROM moment_value_revisions").fetchone()[0]==3
    scores=value_report(root)
    assert candidate["momentId"] not in scores["ranking"]["queues"]["share"]
    assert {p.name:p.read_bytes() for p in camera.iterdir()}==originals


def test_human_no_event_cannot_be_positive_and_invalid_rating_rejected(workspace):
    root,*_=workspace
    _packet(workspace)
    m=read_value_packet(root)["moments"][0]["momentId"]
    with pytest.raises(ValueError,match="positive event significance"):
        save_value_review(root,batch=1,moment_id=m,decision="keep",
                          event_observed="not_observed",
                          ratings={"eventSignificance":4})
    with pytest.raises(ValueError,match="integers"):
        save_value_review(root,batch=1,moment_id=m,decision="later",
                          event_observed="uncertain",
                          ratings={"watchAgain":True})
    with pytest.raises(ValueError,match="versioned"):
        save_value_review(root,batch=1,moment_id=m,decision="later",
                          event_observed="uncertain",
                          ratings={"fakeRareSpeciesBonus":4})
    with pytest.raises(ValueError,match="frozen review packet"):
        save_value_review(root,batch=1,moment_id="mv-unknown",decision="keep",
                          event_observed="observed",ratings={})


def test_signed_packet_tamper_refused_and_cli_exports_no_decoding(workspace,capsys):
    root,camera,originals,asset=workspace
    assert main(["--workspace",str(root),"value-lab"])==0
    assert "momentCount" in capsys.readouterr().out
    path=root/"reports"/"clip-value-lab-v1"/"batch-0001"/"manifest.json"
    raw=path.read_text()
    modified=json.loads(raw)
    modified["moments"][0]["startMs"]=25000
    path.write_text(json.dumps(modified))
    with pytest.raises(ValueError,match="modified"):
        read_value_packet(root)
    path.write_text(raw)
    export=root/"reports"/"my-value-report.json"
    assert main(["--workspace",str(root),"value-report",
                 "--output",str(export)])==0
    output=json.loads(capsys.readouterr().out)
    assert output["reviewedCount"]==0
    assert export.is_file()
    assert {p.name:p.read_bytes() for p in camera.iterdir()}==originals


def test_value_lab_authenticated_studio_review_route(workspace):
    root,camera,originals,asset=workspace
    _packet(workspace)
    server=ThreadingHTTPServer(("127.0.0.1",0),make_handler(root,"test-token"))
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    def request(method,path,data=None,authorized=True):
        conn=HTTPConnection("127.0.0.1",server.server_port,timeout=10)
        body=json.dumps(data) if data is not None else None
        headers={"Host":f"127.0.0.1:{server.server_port}"}
        if authorized:headers["X-Klip-Token"]="test-token"
        if data is not None:headers["Content-Type"]="application/json"
        try:
            conn.request(method,path,body=body,headers=headers)
            res=conn.getresponse()
            return res.status,res.read().decode("utf-8")
        finally:
            conn.close()
    try:
        status,_=request("GET","/api/value-lab?batch=1",authorized=False)
        assert status==403
        status,html=request("GET","/value-lab")
        assert status==200
        assert "KLIP_SESSION_TOKEN" not in html
        assert "Clip Value Lab" in html
        status,raw=request("GET","/api/value-lab?batch=1")
        assert status==200
        state=json.loads(raw)
        first=state["moments"][0]
        status,raw=request("POST","/api/value-lab/review",{
            "batch":1,"momentId":first["momentId"],"decision":"keep",
            "eventObserved":"observed",
            "ratings":{"eventSignificance":3,"subjectVisibility":3}})
        assert status==200
        assert json.loads(raw)["status"]=="reviewed"
        status,raw=request("GET","/api/value-lab?batch=1")
        assert status==200
        assert json.loads(raw)["reviewedCount"]==1
        status,_=request("POST","/api/value-lab/review",{
            "batch":1,"momentId":first["momentId"],"decision":"keep",
            "eventObserved":"observed","ratings":{}},authorized=False)
        assert status==403
        status,raw=request("GET","/api/value-lab/report?batch=1")
        assert status==200
        assert json.loads(raw)["reviewedCount"]==1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert {p.name:p.read_bytes() for p in camera.iterdir()}==originals

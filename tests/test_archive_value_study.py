"""Archive-wide value study: reproducible selection, safe inputs, blind review."""
from __future__ import annotations

from hashlib import sha256
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread

import pytest

from adventure_intelligence import archive_value_study as study
from adventure_intelligence.catalog import connect
from adventure_intelligence.benchmark_feedback import load_benchmark
from adventure_intelligence import studio as studio_module


@pytest.fixture
def corpus(tmp_path):
    root=tmp_path/"workspace"
    originals=tmp_path/"external-camera"
    originals.mkdir()
    baseline={}
    with connect(root) as db:
        for i in range(12):
            sport=("ski","snorkeling","bike","unknown")[i%4]
            raw=(f"immutable-camera-recording-{i}-".encode())*60
            source=originals/f"DJI_{i:03}.MP4"
            source.write_bytes(raw)
            baseline[source.name]=sha256(raw).hexdigest()
            aid=sha256(raw).hexdigest()
            db.execute(
                "INSERT INTO assets(id,sha256,stored_path,size_bytes,suffix,"
                "duration,activity) VALUES (?,?,?,?,?,?,?)",
                (aid,aid,str(source),len(raw),".mp4",120.0,sport))
            if i in (3,7,11):
                continue  # no episode evidence; still should get a control
            db.execute(
                "INSERT INTO episodes(id,asset_id,kind,start_ms,end_ms,score,"
                "event_ids_json,evidence_json,origin,version)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f"episode-{i}",aid,
                 "carving_flow" if sport=="ski" else
                 "wildlife_encounter" if sport=="snorkeling" else
                 "active_sequence",
                 36000,62000,.80,"[]","{}","detector","synthetic-v1"))
    return root,originals,baseline


def assert_unchanged(originals,baseline):
    assert {p.name:sha256(p.read_bytes()).hexdigest()
            for p in originals.iterdir()}==baseline


def test_study_creates_valid_frozen_blind_manifest_without_source_reads(corpus):
    root,originals,baseline=corpus
    target=root/"reports"/"value-study"
    result=study.save_value_study(root,target,seed=17,review_limit=24)
    assert result["status"]=="archive_value_study_created"
    assert result["analyzedAssets"]==12
    assert result["candidateWindows"]>0
    assert result["reviewIntervals"]>0
    assert result["sourceVideoReads"]==0
    assert result["modelCalls"]==0
    manifest=load_benchmark(root,target/"assignments.json")
    assert manifest["blindReview"] is True
    rows=list(manifest["assignments"].values())
    assert any(x["role"]=="control_systematic" for x in rows)
    assert any(x["role"]=="candidate_hypothesis" for x in rows)
    assert set(x["selectionGroup"] for x in rows)=={
        "ski","snorkeling","bike","unresolved"}
    assert all(x["endMs"]-x["startMs"]>=1000 for x in rows)
    assert all(x["endMs"]-x["startMs"]<=120000 for x in rows)
    for x in rows:
        if x["role"]=="candidate_hypothesis" and x["selectionGroup"]=="ski":
            assert x["endMs"]-x["startMs"]>=28000
    report=json.loads((target/"study.json").read_text())
    assert report["calibratedRankingAccuracy"] is None
    assert report["archiveRecall"] is None
    assert report["filesDeleted"]==0
    assert report["zeroOriginalVideoBytesRead"] is True
    assert (target/"index.html").is_file()
    assert_unchanged(originals,baseline)


def test_fixed_seed_reproduces_review_assignments_and_controls_do_not_overlap(corpus):
    root,originals,baseline=corpus
    report1,a=study.make_value_study(root,seed=101,review_limit=23)
    report2,b=study.make_value_study(root,seed=101,review_limit=23)
    assert a["assignments"]==b["assignments"]
    assert report1["selectionRoles"]==report2["selectionRoles"]
    by_asset={}
    for row in a["assignments"]:
        by_asset.setdefault(row["assetId"],[]).append(row)
    for items in by_asset.values():
        if len(items)!=2:
            continue
        first,second=items
        assert (first["endMs"]<=second["startMs"] or
                second["endMs"]<=first["startMs"])
    assert_unchanged(originals,baseline)


def test_no_episodes_or_empty_caches_are_unknown_not_confirmed_negative(corpus):
    root,_,_=corpus
    report,manifest=study.make_value_study(root,seed=5,review_limit=24)
    assert report["assetsWithCachedEpisodeCandidates"]<report["analyzedAssets"]
    unresolved=[x for x in manifest["assignments"] if
                x["selectionGroup"]=="unresolved"]
    assert unresolved and all(x["role"]=="control_systematic" for x in unresolved)
    assert report["holdoutQuality"]=="not_session_disjoint_without_verified_outing_ids"
    assert report["calibratedRankingAccuracy"] is None


def test_study_rejects_bad_config_and_preexisting_report_dir(corpus):
    root,_,_=corpus
    for bad in (-1,501):
        with pytest.raises(ValueError):
            study.make_value_study(root,review_limit=bad)
    with pytest.raises(ValueError):
        study.make_value_study(root,max_assets=-1)
    with pytest.raises(ValueError):
        study.make_value_study(root,seed=-1)
    target=root/"reports"/"existing"
    study.save_value_study(root,target,review_limit=8)
    with pytest.raises(ValueError,match="NEW"):
        study.save_value_study(root,target,review_limit=8)


def test_blind_studio_masks_roles_and_model_and_preserves_human_save(corpus):
    root,originals,baseline=corpus
    directory=root/"reports"/"blind"
    study.save_value_study(root,directory,review_limit=8)
    path=directory/"assignments.json"
    server=ThreadingHTTPServer(("127.0.0.1",0),
        studio_module.make_handler(root,"test-blind-token",path,
                                   "gemma3:4b","adaptive-v2"))
    server.daemon_threads=True
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    def req(method,path,body=None):
        client=HTTPConnection("127.0.0.1",server.server_port,timeout=20)
        headers={"X-Klip-Token":"test-blind-token",
                 "Host":f"127.0.0.1:{server.server_port}"}
        if body is not None:
            body=json.dumps(body)
            headers["Content-Type"]="application/json"
        client.request(method,path,body=body,headers=headers)
        resp=client.getresponse()
        out=resp.status,json.loads(resp.read())
        client.close()
        return out
    try:
        status,raw=req("GET","/api/benchmark")
        assert status==200
        assert raw["blindReview"] is True
        assert raw["localJudgeEnabled"] is False
        assert raw["temporalSecondLookEnabled"] is False
        assert all("role" not in x and "schedulerReasons" not in x
                   and "candidateScore" not in x for x in raw["assignments"])
        assert all(x["suggestion"] is None and x["temporalSuggestion"] is None
                   for x in raw["previews"].values())
        rid=raw["assignments"][0]["reviewId"]
        status,_=req("POST","/api/benchmark/local-judge",{"reviewId":rid})
        assert status==400
        status,_=req("POST","/api/benchmark/temporal-look",{"reviewId":rid})
        assert status==400
        status,save=req("POST","/api/benchmark/review",{
            "reviewId":rid,"reviewDecision":"uncertain",
            "observedActivity":"other",
            "notes":"Review complete source action and framing before Keep."})
        assert status==200 and save["status"]=="saved"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert_unchanged(originals,baseline)

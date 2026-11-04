"""Temporal second look: bounded 7-frame local inference and evidence provenance."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json

import pytest

from adventure_intelligence import temporal_second_look as temporal
from adventure_intelligence import archive_temporal_look as runner
from adventure_intelligence import studio_preview_judge as preview
from adventure_intelligence import adaptive_judge_v2 as v2
from adventure_intelligence.benchmark_feedback import load_benchmark
from adventure_intelligence.archive_pilot import run_archive_pilot
from adventure_intelligence.archive_review import seed_archive_reviews
from adventure_intelligence.catalog import connect
from adventure_intelligence.indexing import index_folder


def model_answer(**kwargs):
    raw={
        "recommendation":"unsure",
        "observedEvent":"uncertain",
        "sceneType":"potential_encounter",
        "summary":"A wing-shaped subject appears above pale sand.",
        "reason":"The subject is visible in separate frames but its identity and path remain uncertain.",
        "frameEvidence":[{"index":1,"observation":"Dark outline above seabed"},
                         {"index":4,"observation":"Similar outline partly in frame"}],
        "uncertainty":"Subject identity and continuous behavior require video.",
        "needsContinuousVideoReview":True,
        "ratings":{"eventInterest":3,"subjectVisibility":2,
                   "learningPotential":2,"sharingPotential":2},
    }
    raw.update(kwargs)
    return raw


def first_pass(**kwargs):
    return {
        "recommendation":"unsure","observedEvent":"uncertain",
        "needsTemporalReview":True,"uncertaintyReason":"between_frames",
        "policyVersion":"adaptive-context-rubric-v2",
        **kwargs,
    }


@pytest.fixture
def review_fixture(tmp_path,monkeypatch):
    source=tmp_path/"external-camera"
    source.mkdir()
    for i in range(8):
        (source/f"DJI_{i:03}.MP4").write_bytes((f"immutable-video-{i}-".encode())*350)
    originals={p.name:p.read_bytes() for p in source.iterdir()}
    root=tmp_path/"ws"
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda p:{"duration":40.,"audio":False,"capture_time":None})
    index_folder(source,root,activity="unknown")
    with connect(root) as db:
        entries=list(db.execute("SELECT id FROM assets ORDER BY stored_path"))
        for i,item in enumerate(entries):
            db.execute("UPDATE assets SET activity=? WHERE id=?",
                       (("snorkeling","ski","bike","unknown")[i%4],item["id"]))
    run_archive_pilot(root,batch_size=8,max_frames=8)
    dest=root/"reports"/"reviews"
    seed_archive_reviews(root,root/"reports"/"archive-pilot-v1",dest,max_assets=8)
    return root,source,originals,dest/"assignments.json"


def cache_proxy(root,a):
    path,receipt=preview.preview_paths(root,a["assetId"],
                                       a["startMs"],a["endMs"])
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(b"FAKE-H264-PROXY"+b"z"*320)
    receipt.write_text(json.dumps({
        "version":preview.VERSION,"assetId":a["assetId"],
        "startMs":a["startMs"],"endMs":a["endMs"],
        "previewSha256":sha256(path.read_bytes()).hexdigest(),
        "originalSha256Verified":True}))
    return path


def test_seven_frame_judge_is_context_aware_and_never_claims_real_video():
    hashes={context:temporal.fingerprint(context) for context in v2.GROUPS}
    assert len(set(hashes.values()))==4
    assert len(temporal.SAMPLE_FRACTIONS)==7
    for context in v2.GROUPS:
        text=temporal.prompt_for(context)
        assert "SEVEN ORDERED" in text
        assert "do NOT see the intervening frames" in text
        assert "weak activity hint" in text.lower()
    assessed=temporal.validate(model_answer())
    assert assessed["sourceFramesSeen"]==7
    assert assessed["videoOrAudioReviewed"] is False
    assert assessed["humanApprovalRequired"] is True
    assert assessed["speciesVerified"] is False
    assert assessed["athleteTechniqueVerified"] is False


def test_second_look_avoids_false_skips_and_false_rare_species_claims():
    low_quality=temporal.validate(model_answer(
        recommendation="skip",observedEvent="uncertain",
        needsContinuousVideoReview=True))
    assert low_quality["recommendation"]=="unsure"
    assert low_quality["rawModelRecommendation"]=="skip"
    assert low_quality["policyOverrides"]
    no_subject=temporal.validate(model_answer(
        recommendation="keep",observedEvent="uncertain",
        ratings={**model_answer()["ratings"],"subjectVisibility":0}))
    assert no_subject["recommendation"]=="unsure"
    assert temporal.should_investigate(first_pass())
    assert temporal.should_investigate(first_pass(
        recommendation="skip",needsTemporalReview=False,
        observedEvent="no",uncertaintyReason="none")) is False


def test_malformed_or_hallucinated_frame_indexes_fail_closed():
    bad=[
        model_answer(frameEvidence=[{"index":7,"observation":"Manta seen"}]),
        model_answer(frameEvidence=[{"index":3,"observation":"One"},
                                    {"index":3,"observation":"Two"}]),
        model_answer(frameEvidence=[]),
        model_answer(ratings={**model_answer()["ratings"],"sharingPotential":True}),
        model_answer(ratings={**model_answer()["ratings"],"sharingPotential":5}),
        model_answer(needsContinuousVideoReview="no"),
        model_answer(sceneType="confirmed_manta_species"),
        {**model_answer(),"verifiedSpecies":"manta ray"},
    ]
    for row in bad:
        with pytest.raises(ValueError):
            temporal.validate(row)


def test_seven_jpegs_from_proxy_not_original_and_single_duration_probe(
        tmp_path,monkeypatch):
    path=tmp_path/"proxy.mp4"
    path.write_bytes(b"proxy"*100)
    seen=[]
    monkeypatch.setattr(temporal,"probe",lambda p:
                        seen.append(("probe",p)) or {"duration":14.0})
    monkeypatch.setattr(temporal,"require_tool",lambda tool:"ffmpeg")
    def fake_call(args,**kwargs):
        class Done:
            returncode=0
        assert args[args.index("-i")+1]==str(path)
        target=Path(args[-1])
        target.write_bytes(b"J"*230)
        seen.append(("ffmpeg",args[args.index("-ss")+1]))
        return Done()
    monkeypatch.setattr(temporal.subprocess,"run",fake_call)
    encoded,times=temporal._samples(path)
    assert len(encoded)==7 and len(times)==7
    assert len(set(times))==7
    assert [x[0] for x in seen].count("probe")==1
    assert [x[0] for x in seen].count("ffmpeg")==7
    assert max(times)<14_000


def test_v2_initial_opinion_is_preserved_when_temporal_cache_is_reused(
        tmp_path,monkeypatch):
    proxy=tmp_path/"short.mp4"
    proxy.write_bytes(b"preview-bytes"*65)
    monkeypatch.setattr(temporal,"_samples",
                        lambda _: (["a"]*7,[980,2800,4760,7000,9240,11200,13020]))
    def transport(payload):
        assert len(payload["messages"][0]["images"])==7
        assert payload["model"]=="gemma3:4b"
        assert "manta" not in json.dumps(payload["messages"][0]["images"])
        return {"message":{"content":json.dumps(model_answer())},
                "total_duration":1234}
    initial=first_pass()
    result=temporal.second_look(proxy,model="gemma3:4b",
                                 context="snorkeling",first=initial,
                                 transport=transport)
    assert result["cached"] is False
    assert result["firstPassComparison"]["previousRecommendation"]=="unsure"
    assert result["sampleTimesMs"][0]==980
    assert result["videoOrAudioReviewed"] is False
    cached=temporal.second_look(proxy,model="gemma3:4b",
                               context="snorkeling",first=initial,
                               transport=lambda x:pytest.fail("already cached"))
    assert cached["cached"] is True
    assert cached["firstPassComparison"]["recommendationChanged"] is False
    assert temporal._record_path(proxy,"gemma3:4b","snorkeling")!=\
           v2._cache_path(proxy,"gemma3:4b","snorkeling")
    assert not v2._cache_path(proxy,"gemma3:4b","snorkeling").exists()
    assert proxy.read_bytes()==b"preview-bytes"*65


def test_dry_run_never_invokes_ollama_or_source_decoder(review_fixture,monkeypatch):
    ws,source,originals,assignments=review_fixture
    rows=list(load_benchmark(ws,assignments)["assignments"].values())
    for a in rows:
        cache_proxy(ws,a)
    monkeypatch.setattr(runner,"cached_adaptive_judgment",
                        lambda path,**k:first_pass())
    monkeypatch.setattr(runner,"second_look",
                        lambda *a,**k:pytest.fail("dry run must not invoke model"))
    report=runner.inspect_temporal_candidates(ws,assignments,model="gemma3:4b")
    assert report["mode"]=="dry_run"
    assert report["selected"]==4
    assert report["sourceVideoReads"]==0
    assert report["newFullSourceShaPasses"]==0
    assert report["selectionRoles"]["control_systematic"]>=1
    assert {p.name:p.read_bytes() for p in source.iterdir()}==originals


def test_source_cache_missing_abstains_and_no_inference(review_fixture,monkeypatch):
    ws,source,originals,assignments=review_fixture
    monkeypatch.setattr(runner,"second_look",
                        lambda *args,**kwargs:pytest.fail("missing proxy"))
    report=runner.inspect_temporal_candidates(ws,assignments,
        model="gemma3:4b",run=True)
    assert report["selected"]==0
    assert report["missingVerifiedPreviews"]>0
    assert report["sourceVideoReads"]==0
    assert {p.name:p.read_bytes() for p in source.iterdir()}==originals


def test_run_second_look_is_bounded_and_leaves_human_reviews_untouched(
        review_fixture,monkeypatch):
    ws,source,originals,assignments=review_fixture
    row=list(load_benchmark(ws,assignments)["assignments"].values())[0]
    cache_proxy(ws,row)
    monkeypatch.setattr(runner,"cached_adaptive_judgment",
                        lambda path,**kwargs:first_pass())
    monkeypatch.setattr(runner,"second_look",
                        lambda path,**kw:{
                            **temporal.validate(model_answer()),
                            "cached":False,
                            "sampleTimesMs":[700,2000,3400,5000,6600,8000,9300],
                            "firstPassComparison":{
                                "recommendationChanged":False}
                        })
    report=runner.inspect_temporal_candidates(ws,assignments,model="gemma3:4b",
        only_review_id=row["reviewId"],run=True)
    assert report["status"]=="completed"
    assert report["selected"]==1
    assert report["newLocalModelCalls"]==1
    assert report["humanReviewsModified"] is False
    assert report["items"][0]["status"]=="suggested"
    with connect(ws) as db:
        assert db.execute("SELECT count(*) FROM benchmark_review_revisions").fetchone()[0]==0
    assert {p.name:p.read_bytes() for p in source.iterdir()}==originals



def test_studio_second_look_is_auth_protected_and_never_edits_reviews(
        review_fixture,monkeypatch):
    from http.client import HTTPConnection
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from adventure_intelligence import studio as app
    from adventure_intelligence.benchmark_feedback import benchmark_feedback_status

    ws,source,originals,assignments=review_fixture
    row=list(load_benchmark(ws,assignments)["assignments"].values())[0]
    proxy=cache_proxy(ws,row)
    monkeypatch.setattr(app,"cached_adaptive_judgment",
                        lambda path,**k:first_pass())
    monkeypatch.setattr(app,"second_look",
                        lambda path,**k:{
                            **temporal.validate(model_answer()),
                            "firstPassComparison":{
                                "previousJudgmentAvailable":True,
                                "previousRecommendation":"unsure",
                                "recommendationChanged":False},
                            "sampleTimesMs":[700,2000,3400,5000,6600,8000,9300],
                            "cached":False,
                        })
    server=ThreadingHTTPServer(("127.0.0.1",0),
        app.make_handler(ws,"private-test-token",assignments,
                         "gemma3:4b","adaptive-v2"))
    server.daemon_threads=True
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    def request(path,*,token=True):
        client=HTTPConnection("127.0.0.1",server.server_port,timeout=12)
        headers={"Content-Type":"application/json",
                 "Host":f"127.0.0.1:{server.server_port}"}
        if token:headers["X-Klip-Token"]="private-test-token"
        client.request("POST",path,
                       body=json.dumps({"reviewId":row["reviewId"]}),
                       headers=headers)
        resp=client.getresponse()
        status=resp.status
        body=json.loads(resp.read())
        client.close()
        return status,body
    try:
        status,_=request("/api/benchmark/temporal-look",token=False)
        assert status==403
        status,out=request("/api/benchmark/temporal-look")
        assert status==200 and out["status"]=="provisional_temporal_second_look"
        assert out["humanApprovalRequired"] is True
        assert out["suggestion"]["sourceFramesSeen"]==7
        assert out["suggestion"]["videoOrAudioReviewed"] is False
        assert benchmark_feedback_status(ws,assignments)["reviewedAssignments"]==0
        proxy.unlink()
        status,out=request("/api/benchmark/temporal-look")
        assert status==400
        assert "preview" in out["error"].lower()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert {p.name:p.read_bytes() for p in source.iterdir()}==originals


def test_cli_temporal_look_has_no_hidden_media_work_in_dry_run(review_fixture,capsys):
    from adventure_intelligence.cli import main
    ws,source,originals,assignments=review_fixture
    assert main(["--workspace",str(ws),"archive-temporal-look",
                 "--assignments",str(assignments),
                 "--model","gemma3:4b","--limit","4"])==0
    displayed=json.loads(capsys.readouterr().out)
    assert displayed["mode"]=="dry_run"
    assert displayed["sourceVideoReads"]==0
    assert displayed["newFullSourceShaPasses"]==0
    assert displayed["missingVerifiedPreviews"]>0
    assert displayed["selected"]==0
    assert {p.name:p.read_bytes() for p in source.iterdir()}==originals

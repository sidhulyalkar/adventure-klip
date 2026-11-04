"""Review priority must be explanatory and retries must target one failed clip."""
from pathlib import Path

import pytest

from adventure_intelligence.review_priority import investigation_priority
from adventure_intelligence import archive_local_triage as triage
from adventure_intelligence.adaptive_judge_v2 import normalize_reply


def result_template(**changes):
    row={
        "scene":"underwater_sand",
        "observedEvent":"yes",
        "recommendation":"keep",
        "summary":"A large dark ray-shaped subject is visible above the seabed.",
        "reason":"The focal subject is visible in two of the three frames.",
        "evidence":["Dark subject above pale sand"],
        "uncertainty":"identity_unclear",
        "needsTemporalReview":False,
        "ratings":{
            "eventInterest":3,"subjectVisibility":3,"ambientClarity":0,
            "narrativePotential":2,"visualQuality":1,
            "sharingPotential":2,"learningPotential":2,
        },
    }
    row.update(changes)
    return row


def test_murky_interesting_animal_beats_clear_empty_sand_without_rarity_claim():
    murky=normalize_reply(result_template())
    sandy=normalize_reply(result_template(
        scene="underwater_sand",observedEvent="no",
        recommendation="skip",summary="Open bright sandy bottom",
        reason="No focal animal visible in the sampled frames.",
        uncertainty="none",ratings={
            "eventInterest":0,"subjectVisibility":0,"ambientClarity":3,
            "narrativePotential":0,"visualQuality":3,
            "sharingPotential":1,"learningPotential":0,
        },
    ))
    assert murky["investigationScore"]>sandy["investigationScore"]
    assert murky["scoreBasis"]["ambientClarityExcluded"] is True
    assert murky["scoreBasis"]["verifiedRarityBonus"]==0
    assert murky["speciesVerified"] is False
    assert sandy["recommendation"]=="skip"
    assert murky["scoreMeaning"]=="uncalibrated_review_order_not_event_probability"


def test_missing_or_partial_visual_scoring_abstains_and_does_not_impute_numbers():
    unknown=investigation_priority({"eventInterest":3,"subjectVisibility":2})
    assert unknown["investigationScore"] is None
    assert unknown["assessedDimensions"]==2
    assert investigation_priority({})["investigationScore"] is None
    assert investigation_priority(None)["investigationScore"] is None
    assert investigation_priority({"eventInterest":True,"subjectVisibility":3,
                                   "narrativePotential":3,"sharingPotential":3,
                                   "learningPotential":2})["investigationScore"] is None


def test_strict_one_review_retry_reports_stage_without_reprocessing_others(
        tmp_path,monkeypatch):
    video_id="a"*64
    first={"reviewId":"1"*20,"assetId":video_id,"startMs":0,"endMs":10000,
           "selectionGroup":"unresolved","role":"control_systematic",
           "basename":"failed-clip.mp4"}
    second={**first,"reviewId":"2"*20,
            "basename":"already-finished.mp4"}
    monkeypatch.setattr(triage,"load_benchmark",lambda *args:{
        "assignments":{first["reviewId"]:first,second["reviewId"]:second}})
    monkeypatch.setattr(triage,"benchmark_feedback_status",
                        lambda *args:{"reviewEntries":{}})
    monkeypatch.setattr(triage,"SourceStream",lambda root:object())
    monkeypatch.setattr(triage,"preview_paths",lambda *args:
                        (tmp_path/"cached-proxy.mp4",tmp_path/"receipt.json"))
    monkeypatch.setattr(triage,"cached_judgment",
                        lambda *args,**kwargs:None)
    seen=[]
    monkeypatch.setattr(triage,"generate_preview",
                        lambda root,source,a:seen.append(a["reviewId"]))
    def bad_model(*args,**kwargs):
        raise ValueError("Local model returned invalid JSON")
    monkeypatch.setattr(triage,"local_judgment",bad_model)
    report=triage.run_local_triage(tmp_path,tmp_path/"assignments.json",
        model="gemma3:4b",only_review_id=first["reviewId"],run=True)
    assert report["selected"]==1
    assert report["failed"]==1
    assert report["status"]=="partial_failure"
    assert report["items"][0]["reviewId"]==first["reviewId"]
    assert report["items"][0]["failureStage"]=="local_judge_sampling_or_response"
    assert report["items"][0]["errorCode"]=="judge_response_validation_error"
    assert "invalid" in report["items"][0]["diagnostic"].lower()
    assert seen==[first["reviewId"]]
    with pytest.raises(ValueError,match="not in"):
        triage.run_local_triage(tmp_path,tmp_path/"assignments.json",
            model="gemma3:4b",only_review_id="f"*20)


def test_targeted_retry_skips_already_reviewed_interval(tmp_path,monkeypatch):
    rid="d"*20
    assignment={"reviewId":rid,"assetId":"a"*64,"startMs":0,"endMs":9000,
                "selectionGroup":"ski","role":"candidate_hypothesis",
                "basename":"already-reviewed.mp4"}
    monkeypatch.setattr(triage,"load_benchmark",
                        lambda *args:{"assignments":{rid:assignment}})
    monkeypatch.setattr(triage,"benchmark_feedback_status",
                        lambda *args:{"reviewEntries":{rid:{"reviewDecision":"keep"}}})
    monkeypatch.setattr(triage,"SourceStream",
                        lambda root:pytest.fail("no media object needed"))
    report=triage.run_local_triage(tmp_path,tmp_path/"assignments.json",
          model="gemma3:4b",only_review_id=rid,run=False)
    assert report["selected"]==0
    assert report["reviewedHumanAlready"]==1


def test_adaptive_success_reports_explainable_rank_and_never_changes_human_truth(
        tmp_path,monkeypatch):
    rid="e"*20
    assignment={"reviewId":rid,"assetId":"a"*64,"startMs":0,"endMs":8000,
                "selectionGroup":"snorkeling","role":"candidate_hypothesis",
                "basename":"ray-candidate.mp4"}
    monkeypatch.setattr(triage,"load_benchmark",
                        lambda *args:{"assignments":{rid:assignment}})
    monkeypatch.setattr(triage,"benchmark_feedback_status",
                        lambda *args:{"reviewEntries":{}})
    monkeypatch.setattr(triage,"SourceStream",lambda root:object())
    monkeypatch.setattr(triage,"preview_paths",
                        lambda *args:(tmp_path/"p.mp4",tmp_path/"p.json"))
    monkeypatch.setattr(triage,"cached_adaptive_judgment",
                        lambda *args,**kwargs:None)
    monkeypatch.setattr(triage,"generate_preview",
                        lambda *args,**kwargs:{"status":"prepared"})
    monkeypatch.setattr(triage,"adaptive_judgment",
                        lambda *args,**kwargs:{
                            **normalize_reply(result_template()),
                            "cached":False,
                            "promptFingerprint":"abc",
                        })
    out=triage.run_local_triage(tmp_path,tmp_path/"reviews.json",
          model="gemma3:4b",policy="adaptive-v2",only_review_id=rid,run=True)
    assert out["status"]=="completed"
    assert out["items"][0]["investigationScore"]>50
    assert out["items"][0]["assessedDimensions"]>=4
    assert out["items"][0]["scoreMeaning"]=="uncalibrated_review_order_not_event_probability"
    assert out["humanLabelsWritten"]==0
    assert out["confirmedEvents"]==0



def test_verified_existing_proxy_skips_source_hashing_in_v2(tmp_path,monkeypatch):
    rid="b"*20
    a={"reviewId":rid,"assetId":"a"*64,"startMs":1000,"endMs":15000,
       "selectionGroup":"snorkeling","role":"candidate_hypothesis",
       "basename":"already-prepared.MP4"}
    monkeypatch.setattr(triage,"load_benchmark",
                        lambda *args:{"assignments":{rid:a}})
    monkeypatch.setattr(triage,"benchmark_feedback_status",
                        lambda *args:{"reviewEntries":{}})
    monkeypatch.setattr(triage,"SourceStream",lambda root:object())
    from adventure_intelligence.studio_preview_judge import VERSION
    from hashlib import sha256
    import json
    proxy=tmp_path/"preview.mp4"
    proxy.write_bytes(b"cached-local-h264-proxy"*30)
    receipt=tmp_path/"preview.json"
    receipt.write_text(json.dumps({
        "version":VERSION,
        "originalSha256Verified":True,
        "assetId":a["assetId"],"startMs":1000,"endMs":15000,
        "previewSha256":sha256(proxy.read_bytes()).hexdigest(),
    }))
    monkeypatch.setattr(triage,"preview_paths",lambda *args:(proxy,receipt))
    monkeypatch.setattr(triage,"cached_adaptive_judgment",
                        lambda *args,**kwargs:None)
    monkeypatch.setattr(triage,"generate_preview",
                        lambda *args,**kwargs:
                        pytest.fail("A verified proxy should not rehash original video"))
    monkeypatch.setattr(triage,"adaptive_judgment",
                        lambda *args,**kwargs:{
                            **normalize_reply(result_template()),"cached":False})
    report=triage.run_local_triage(tmp_path,tmp_path/"frozen-assignments.json",
        model="gemma3:4b",policy="adaptive-v2",only_review_id=rid,run=True)
    assert report["status"]=="completed"
    assert report["items"][0]["investigationScore"]>50
    assert report["humanLabelsWritten"]==0

    # A forged receipt lacking prior full-source SHA verification is rejected
    # and must trigger the original-source verification path instead.
    meta=json.loads(receipt.read_text())
    meta["originalSha256Verified"]=False
    receipt.write_text(json.dumps(meta))
    from adventure_intelligence.studio_preview_judge import _check_cached
    assert _check_cached(proxy,receipt,aid=a["assetId"],
                         start_ms=1000,end_ms=15000) is False

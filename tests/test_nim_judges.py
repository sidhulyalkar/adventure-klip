"""No-network, no-source-upload tests for the NVIDIA VLM judge fleet."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from adventure_intelligence.nim_judges import (
    validate_manifest, evaluate_manifest, _json_reply, summarize_jury,
    _safe_endpoint, DEFAULT_ENDPOINT,
)
from adventure_intelligence.nim_benchmark import plan_benchmark, prepare_benchmark
from adventure_intelligence.nim_calibration import compare_with_humans
from adventure_intelligence.indexing import index_folder


@pytest.fixture
def approved_images(tmp_path):
    root=tmp_path/"approved_frames";root.mkdir()
    # Image header is sufficient to test that arbitrary text is rejected.
    for i in range(4):
        (root/f"frame-{i}.jpg").write_bytes(b"\xff\xd8\xff" + bytes([i])*64 + b"\xff\xd9")
    manifest={"schemaVersion":1,"clips":[{
        "clipId":"clip-001","pool":"candidate","startMs":1000,"endMs":9000,
        "frames":[{"tMs":1500+i*2000,"path":f"frame-{i}.jpg"} for i in range(4)]
    }]}
    return root,manifest

def answer(*,recommendation="keep",insufficient=False,interest=4):
    return {"event_interest":interest,"event_completeness":3,
            "visual_quality":2,"story_value":3,
            "recommendation":recommendation,"insufficient_evidence":insufficient,
            "observed_content":"Visible outdoor activity","rationale":"Useful sequence, uncertain motion",
            "missing_evidence":["intermediate frames","audio"]}

def completion(doc):
    return {"choices":[{"message":{"content":json.dumps(doc)}}],
            "usage":{"prompt_tokens":100,"completion_tokens":120}}


def test_no_upload_without_explicit_authorization(approved_images):
    frames,manifest=approved_images
    calls=[]
    def transport(*args,**kwargs):
        calls.append((args,kwargs))
        raise AssertionError("Network path called during dry run")
    out=evaluate_manifest(manifest,frames_dir=frames,
                          models=["nvidia/vision-model-a","nvidia/vision-model-b"],
                          transport=transport)
    assert not calls
    assert out["mode"]=="dry_run" and out["results"]==[]
    assert out["requestsPlanned"]==2


def test_independent_judges_are_blind_to_pool_and_source_filename(approved_images):
    frames,manifest=approved_images
    manifest["clips"][0]["sourceAssetId"]="x"*64
    manifest["clips"][0]["localSecret"]="/Volumes/private/footage.mp4"
    seen=[]
    def mock_send(payload,**kwargs):
        seen.append(payload)
        return completion(answer(recommendation="keep"))
    out=evaluate_manifest(manifest,frames_dir=frames,models=["model/a","model/b"],
                          allow_external_upload=True,api_key="test-only",
                          transport=mock_send)
    assert len(seen)==2
    for req in seen:
        body=json.dumps(req)
        assert "candidate" not in body
        assert "private" not in body and "footage.mp4" not in body
        assert "sourceAssetId" not in body
        assert 'data:image/jpeg;base64,' in body
        assert req["max_tokens"]<=600 and req["temperature"]==0
    assert out["results"][0]["jury"]["recommendation"]=="keep"
    assert out["results"][0]["jury"]["nonAbstaining"]==2
    assert len(out["results"][0]["frameHashes"])==4


def test_abstention_and_disagreement_never_masquerade_as_certainty(approved_images):
    root,manifest=approved_images
    votes=iter([answer(),answer(recommendation="skip",interest=1)])
    def fake(payload,**kwargs):return completion(next(votes))
    report=evaluate_manifest(manifest,frames_dir=root,models=["model/a","model/b"],
                             api_key="test",allow_external_upload=True,transport=fake)
    assert report["results"][0]["jury"]["status"]=="disagreement_review"
    assert report["results"][0]["jury"]["maxScoreSpread"]>=2
    jury=summarize_jury([
        {"status":"ok","assessment":answer()},
        {"status":"ok","assessment":answer(insufficient=True)},
    ])
    assert jury["status"]=="insufficient_independent_judges"


@pytest.mark.parametrize("invalid",[
    {"event_interest":8},
    {"event_interest":True},
    {"recommendation":"approved"},
    {"insufficient_evidence":"false"},
    {"missing_evidence":["x"*500]},
])
def test_malformed_judge_scores_fail_closed(invalid):
    data=answer();data.update(invalid)
    with pytest.raises(ValueError):
        _json_reply(completion(data))


def test_request_ceiling_and_allowed_endpoints(approved_images):
    frames,manifest=approved_images
    with pytest.raises(ValueError,match="max_calls"):
        evaluate_manifest(manifest,frames_dir=frames,
                          models=["model/a","model/b"],max_calls=1)
    assert _safe_endpoint(DEFAULT_ENDPOINT)==DEFAULT_ENDPOINT
    assert _safe_endpoint("http://127.0.0.1:8000/v1/chat/completions")
    for url in ("http://evil.example/v1/chat/completions",
                "https://integrate.api.nvidia.com.evil.example/v1/chat/completions",
                "https://private.example/v1/chat/completions",
                "file:///Volumes/private/video.mp4"):
        with pytest.raises(ValueError):
            _safe_endpoint(url)


def test_input_path_guard_and_unapproved_types(approved_images,tmp_path):
    root,manifest=approved_images
    (tmp_path/"secret.jpg").write_bytes(b"\xff\xd8\xffhidden")
    manifest["clips"][0]["frames"][0]["path"]="../secret.jpg"
    with pytest.raises(ValueError,match="outside approved"):
        validate_manifest(manifest,root)
    manifest["clips"][0]["frames"][0]["path"]="frame-0.jpg"
    (root/"frame-0.jpg").write_bytes(b"secret text not image")
    with pytest.raises(ValueError,match="JPEG and PNG"):
        validate_manifest(manifest,root)


def test_deterministic_candidate_and_random_sampling_is_not_only_model_picks():
    assets=[{"id":str(i)*64,"duration":30.0} for i in range(1,9)]
    episodes=[{"asset_id":a["id"],"kind":"active_sequence",
               "start_ms":5000,"end_ms":15000} for a in assets]
    one=plan_benchmark(assets,episodes,sample_count=10,seed=42)
    two=plan_benchmark(assets,episodes,sample_count=10,seed=42)
    assert one==two
    assert one["candidateCount"]==5
    assert one["randomCount"]==5
    assert all(0<=f["tMs"]<c["endMs"] for c in one["clips"] for f in c["frames"])
    assert all(len(c["frames"])==6 for c in one["clips"])
    assert len({c["clipId"] for c in one["clips"]})==10


def test_preparation_without_decode_does_not_read_or_modify_source_video(tmp_path,monkeypatch):
    source=tmp_path/"source";source.mkdir()
    original=[]
    for i in range(4):
        p=source/f"DJI_{i}.MP4"
        p.write_bytes(bytes([i+1])*10000);original.append(p)
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda _:{"duration":30,"audio":False})
    work=tmp_path/"workspace"
    index_folder(source,work)
    before=[p.read_bytes() for p in original]
    output=prepare_benchmark(work,output_dir=work/"benchmark_v1",
                             sample_count=4,seed=5,decode_frames=False)
    assert output["mode"]=="plan_only" and output["framesPresent"] is False
    assert (work/"benchmark_v1"/"manifest.json").exists()
    assert all(p.read_bytes()==data for p,data in zip(original,before))
    with pytest.raises(ValueError,match="exists"):
        prepare_benchmark(work,output_dir=work/"benchmark_v1",sample_count=4)


def test_calibration_is_conditional_and_preserves_unreviewed_pool():
    manifest={"schemaVersion":1,"clips":[
        {"clipId":"a","pool":"candidate","sourceAssetId":"asset",
         "startMs":0,"endMs":10000},
        {"clipId":"b","pool":"random","sourceAssetId":"asset",
         "startMs":20000,"endMs":30000},
    ]}
    report={"schemaVersion":1,"results":[
        {"clipId":"a","jury":{"status":"provisional","recommendation":"keep"}},
        {"clipId":"b","jury":{"status":"provisional","recommendation":"skip"}},
    ]}
    example=[{"assetId":"asset","startMs":0,"endMs":10000,"highlight":"keep"}]
    out=compare_with_humans(manifest,report,example)
    assert out["pools"]["candidate"]["tp"]==1
    assert out["pools"]["random"]["unreviewed"]==1
    assert out["pools"]["random"]["modelEvaluated"]==0
    assert out["pools"]["random"]["recallAmongReviewed"] is None

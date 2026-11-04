"""Adaptive local vision judge: safety, context, cache versioning, paired audit."""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from adventure_intelligence import adaptive_judge_v2 as v2
from adventure_intelligence import judge_audit
from adventure_intelligence import studio_preview_judge as legacy


def example(**changes):
    raw={
        "scene":"underwater_sand",
        "observedEvent":"yes",
        "recommendation":"keep",
        "summary":"Large ray-like outline over sand",
        "reason":"A clear subject is visible even though the sandy water is hazy",
        "evidence":["Frame 1: dark wing-like outline","Frame 2: subject against pale sand"],
        "uncertainty":"identity_unclear",
        "needsTemporalReview":False,
        "ratings":{
            "eventInterest":3,"subjectVisibility":2,"ambientClarity":0,
            "narrativePotential":2,"visualQuality":1,
            "learningPotential":2,"sharingPotential":2,
        },
    }
    raw.update(changes)
    return raw


def test_context_prompts_are_distinct_and_evidence_grounded():
    hashes={c:v2.policy_fingerprint(c) for c in v2.GROUPS}
    assert len(set(hashes.values()))==4
    assert v2.context_hint({"selectionGroup":"snorkeling"})=="snorkeling"
    assert v2.context_hint({"selectionGroup":"unreliable"})=="unresolved"
    assert v2.context_hint(None)=="unresolved"
    assert "sandbar" in v2.prompt_for("snorkeling")
    assert "subject" in v2.prompt_for("snorkeling")
    assert "lift" in v2.prompt_for("ski")
    assert "landing" in v2.prompt_for("bike")
    assert "rare" in v2.prompt_for("unresolved")
    for text in [v2.prompt_for(c) for c in v2.GROUPS]:
        assert "three" in text.lower() or "THREE" in text
        assert "unknown" in text.lower()
        assert "candidate_hypothesis" not in text
        assert "control_systematic" not in text
        assert "score" in text.lower()
    with pytest.raises(ValueError):
        v2.prompt_for("skydiving")


def test_hazy_but_visible_ray_can_be_interesting_without_fake_species():
    answer=v2.normalize_reply(example())
    assert answer["recommendation"]=="keep"
    assert answer["ratings"]["ambientClarity"]==0
    assert answer["ratings"]["subjectVisibility"]==2
    assert answer["ratings"]["eventInterest"]==3
    assert answer["speciesVerified"] is False
    assert answer["athleteTechniqueVerified"] is False
    assert answer["reviewedByHuman"] is False
    assert answer["videoOrAudioReviewed"] is False
    assert answer["priority"]=="high"


def test_uncertain_temporal_action_prevents_ai_skip_and_unsupported_keep():
    uncertain=v2.normalize_reply(example(
        scene="ski_terrain_feature",recommendation="skip",
        observedEvent="uncertain",uncertainty="execution_unverified",
        needsTemporalReview=True,
        ratings={k:None for k in v2.DIMENSIONS},
    ))
    assert uncertain["rawModelRecommendation"]=="skip"
    assert uncertain["recommendation"]=="unsure"
    assert uncertain["priority"]=="investigate"
    assert "avoid_false_skip_with_incomplete_temporal_evidence" in uncertain["policyOverrides"]
    overconfident=v2.normalize_reply(example(
        recommendation="keep",observedEvent="uncertain",
        uncertainty="subject_not_visible",needsTemporalReview=True))
    assert overconfident["recommendation"]=="unsure"


def test_invalid_overconfident_or_non_numeric_responses_fail_closed():
    bad=[
        example(ratings={**example()["ratings"],"eventInterest":True}),
        example(ratings={**example()["ratings"],"eventInterest":3.5}),
        example(ratings={**example()["ratings"],"eventInterest":4}),
        example(scene="verified_manta_ray"),
        example(needsTemporalReview="false"),
        example(evidence=["a"*180]),
        example(uncertainty="verified_species"),
        {**example(),"humanApproved":True},
    ]
    for raw in bad:
        with pytest.raises(ValueError):
            v2.normalize_reply(raw)
    assert v2.normalize_reply(example(
        ratings={k:None for k in v2.DIMENSIONS}))["ratings"]["eventInterest"] is None


def test_v2_cache_separate_from_inflight_v1_and_context_sensitive(tmp_path,monkeypatch):
    preview=tmp_path/"preview.mp4"
    preview.write_bytes(b"synthetic-h264-derivative"*200)
    monkeypatch.setattr(legacy,"_jpeg_samples",lambda _:["a","b","c"])
    seen=[]
    def transport(body):
        seen.append(body)
        return {"message":{"content":json.dumps(example())},"total_duration":8000}
    first=v2.adaptive_judgment(preview,model="gemma3:4b",
                               context="snorkeling",transport=transport)
    assert first["cached"] is False
    assert first["policyVersion"]==v2.POLICY
    assert first["promptFingerprint"]==v2.policy_fingerprint("snorkeling")
    again=v2.adaptive_judgment(preview,model="gemma3:4b",
                               context="snorkeling",
                               transport=lambda b:pytest.fail("v2 cache not reused"))
    assert again["cached"] is True
    assert len(seen)==1
    assert v2._cache_path(preview,"gemma3:4b","snorkeling").exists()
    assert legacy._judge_record_path(preview,"gemma3:4b") != v2._cache_path(
        preview,"gemma3:4b","snorkeling")
    assert not legacy._judge_record_path(preview,"gemma3:4b").exists()
    ski=v2.adaptive_judgment(preview,model="gemma3:4b",
                             context="ski",transport=transport)
    assert ski["cached"] is False
    assert len(seen)==2
    # Neither source media nor human reviewed labels are present or touched.
    assert preview.read_bytes()==b"synthetic-h264-derivative"*200
    assert len([x for x in tmp_path.iterdir() if x.name.endswith(".json")])==2


def test_audit_counts_human_keep_that_legacy_would_skip_without_claiming_recall(
        tmp_path,monkeypatch):
    asset="a"*64
    bench={"benchmarkId":"signed-bench","assignments":{
        "b"*20:{"reviewId":"b"*20,"assetId":asset,"startMs":0,
                "endMs":8000,"selectionGroup":"snorkeling",
                "basename":"ray_D.MP4","role":"control_systematic"},
        "c"*20:{"reviewId":"c"*20,"assetId":asset,"startMs":9000,
                "endMs":18000,"selectionGroup":"ski",
                "basename":"turn_D.MP4","role":"candidate_hypothesis"},
    }}
    feedback={"reviewEntries":{"b"*20:{"reviewDecision":"keep"},
                              "c"*20:{"reviewDecision":"skip"}}}
    monkeypatch.setattr(judge_audit,"load_benchmark",lambda *a:bench)
    monkeypatch.setattr(judge_audit,"benchmark_feedback_status",lambda *a:feedback)
    monkeypatch.setattr(judge_audit,"preview_paths",
                        lambda root,aid,start,end:(tmp_path/f"{start}.mp4",None))
    monkeypatch.setattr(judge_audit,"cached_judgment",
                        lambda path,model:{"recommendation":"skip"} if path.name=="0.mp4"
                        else {"recommendation":"keep"})
    monkeypatch.setattr(judge_audit,"cached_adaptive_judgment",
                        lambda path,model,context:{"recommendation":"unsure"} if path.name=="0.mp4"
                        else {"recommendation":"skip"})
    report=judge_audit.compare_judge_policies(tmp_path,tmp_path/"any",model="gemma3:4b")
    assert report["bothPolicyCachesAvailable"]==2
    assert report["byPolicy"]["legacy"]["wouldSkipHumanKeep"]==1
    assert report["byPolicy"]["legacy"]["wouldKeepHumanSkip"]==1
    assert report["byPolicy"]["adaptive-v2"]["wouldSkipHumanKeep"]==0
    assert report["byPolicy"]["adaptive-v2"]["unsureOnHumanKeep"]==1
    assert report["archiveRecall"] is None
    assert report["modelQualityImprovementProven"] is False
    assert report["videoSourceBytesRead"]==0


def test_local_ollama_only_and_model_name_rejection(tmp_path,monkeypatch):
    video=tmp_path/"proxy.mp4"
    video.write_bytes(b"synthetic-bytes")
    monkeypatch.setattr(legacy,"_jpeg_samples",lambda _:["jpegA","jpegB","jpegC"])
    with pytest.raises(ValueError):
        v2.adaptive_judgment(video,model="https://example.com/vlm",
                             transport=lambda x:pytest.fail("external not allowed"))
    result=v2.adaptive_judgment(video,model="gemma3:4b",context="bike",
        transport=lambda payload:{"message":{"content":json.dumps(example(
            scene="bike_technical_feature",observedEvent="uncertain",
            recommendation="skip",uncertainty="between_frames",
            needsTemporalReview=True))}})
    assert result["recommendation"]=="unsure"
    assert result["backend"]=="ollama_loopback_only"
    assert result["humanApprovalRequired"] is True

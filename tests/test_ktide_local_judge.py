"""K-TIDE local sparse vision judge: synthetic sources, network mocked."""
from copy import deepcopy
import json
from pathlib import Path
from urllib import request

import pytest

from adventure_intelligence.ktide_local_judge import (
    PREFIX, capture_dt, provisional_capture_groups, select_judge_pilot,
    validate_judge_proposal, requested_stills, merge_smoke_assignments,
    local_ollama_json, run_judge_pilot,
)
from adventure_intelligence.ktide_planner import (
    ASSIGNMENTS_VERSION, validate_assignments,
)
from adventure_intelligence.ktide_study_contract import validate_training_lineage
from adventure_intelligence.encoder_dataset import VERSION as DATA_VERSION, group_splits


def inventory():
    times=[
        "2024-12-21T10:00:00Z", "2024-12-23T10:00:00Z",
        "2024-12-27T10:00:00Z", "2025-12-24T12:00:00Z",
        "2026-02-21T12:00:00Z", "2026-03-09T12:00:00Z",
        "2026-04-11T12:00:00Z", "2026-04-11T14:00:00Z"]
    return {
        "version":"klip-ktide-feature-inventory-v1",
        "provider":"histogram","entries":[{
            "sourceSha256":f"{i+1:064x}",
            "captureTimeMetadata":t,
            "durationMs":90000,
            "usableCachedSamples":24,
            "likelyWindowReady":True,
        } for i,t in enumerate(times)]}


def judgment():
    return {
        "subsport":"snorkeling_reef","sceneSummary":"Camera shows reef and blue water.",
        "sceneContext":"underwater_reef",
        "tags":["reef","water"],
        "featureCandidates":[{"dimension":"water_visibility",
                              "frameIndices":[0,1],
                              "observation":"Patchy clear water between reef shapes"}],
        "frameObservations":[
            {"frameIndex":0,"observation":"Blue underwater view"},
            {"frameIndex":1,"observation":"Reef shapes visible"},
        ],
        "eventHints":[{"kind":"continued underwater view",
                      "firstFrameIndex":0,"lastFrameIndex":1}],
        "uncertainties":["Species unknown; sparse sampling"],
        "cameraQuality":"mixed","confidence":0.63,
    }


def test_capture_periods_are_provisional_and_group_conservatively():
    source=inventory()["entries"]
    groups=provisional_capture_groups(source)
    assert len(set(groups.values()))==5
    assert groups[source[0]["sourceSha256"]]==groups[source[2]["sourceSha256"]]
    assert groups[source[6]["sourceSha256"]]==groups[source[7]["sourceSha256"]]
    assert all(s.startswith(PREFIX) for s in groups.values())


def test_capture_dates_are_not_inferred_independent_truth():
    entries=deepcopy(inventory()["entries"])
    entries[0]["captureTimeMetadata"]="not-time"
    entries[1]["captureTimeMetadata"]=None
    groups=provisional_capture_groups(entries)
    assert groups[entries[0]["sourceSha256"]]==PREFIX+"unknown-capture"
    assert groups[entries[1]["sourceSha256"]]==PREFIX+"unknown-capture"


def test_sparse_selection_balances_capture_groups_not_sources():
    plan=select_judge_pilot(inventory(),limit=5)
    assert len(plan["selected"])==5
    assert plan["provisionalCaptureGroups"]==5
    assert plan["groupIndependenceVerified"] is False
    assert plan["rawMediaRead"] is False


def test_proposal_schema_refuses_hallucinated_out_of_window_events():
    assert validate_judge_proposal(judgment(),4)["subsport"]=="snorkeling_reef"
    bad=judgment()
    bad["eventHints"][0]["lastFrameIndex"]=30
    with pytest.raises(ValueError,match="outside observed"):
        validate_judge_proposal(bad,4)
    bad=judgment()
    bad["subsport"]="fictional_treasure"
    with pytest.raises(ValueError,match="Unsupported sub-sport"):
        validate_judge_proposal(bad,4)


def test_deterministic_still_times_and_invalid_duration():
    assert requested_stills(100000,4)==[20000,40000,60000,80000]
    with pytest.raises(ValueError):
        requested_stills(-5,4)


def test_provisional_assignments_forbidden_without_explicit_smoke_optin():
    plan=select_judge_pilot(inventory(),limit=8)
    proposals=[{"sourceSha256":r["sourceSha256"],
                "judgeProposal":judgment()} for r in plan["selected"]]
    assignments=merge_smoke_assignments(plan,proposals)
    assert assignments["version"]==ASSIGNMENTS_VERSION
    assert len(assignments["sources"])==8
    assert len({r["groupId"] for r in assignments["sources"]})==5
    assert all(r["goal"]=="nature_encounter" for r in assignments["sources"])
    with pytest.raises(ValueError,match="Provisional"):
        validate_assignments(assignments)
    assert len(validate_assignments(assignments,
                                    allow_provisional_smoke=True))==8


def test_vision_judge_preserves_existing_owner_overrides():
    plan=select_judge_pilot(inventory(),limit=5)
    first=plan["selected"][0]["sourceSha256"]
    old={"version":ASSIGNMENTS_VERSION,"sources":[{
        "sourceSha256":first,"groupId":"verified-vacation-group",
        "goal":"personal_memory","subsport":"skiing_trees"}]}
    assignments=merge_smoke_assignments(plan,[
        {"sourceSha256":first,"judgeProposal":judgment()}],existing=old)
    assert assignments["sources"][0]=={
        "sourceSha256":first,"groupId":"verified-vacation-group",
        "goal":"personal_memory","subsport":"skiing_trees",
    }


def test_local_loopback_api_only_and_structured_image_evidence(monkeypatch):
    seen={}
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def read(self,n): return json.dumps({
            "message":{"content":json.dumps(judgment())}}).encode()
    def fake_request(req,timeout):
        seen["url"]=req.full_url
        seen["request"]=json.loads(req.data)
        return Response()
    monkeypatch.setattr(request,"urlopen",fake_request)
    frames=[
        {"requestedMs":1000,"imageBytes":b"FAKEPNG"},
        {"requestedMs":2000,"imageBytes":b"FAKEPNG"},
    ]
    with pytest.raises(ValueError,match="loopback"):
        local_ollama_json(frames,endpoint="https://example.com",model="gemma3:4b")
    assert local_ollama_json(frames,model="gemma3:4b")["subsport"]=="snorkeling_reef"
    assert seen["url"]=="http://127.0.0.1:11434/api/chat"
    assert seen["request"]["stream"] is False
    assert seen["request"]["format"]=="json"
    assert len(seen["request"]["messages"][0]["images"])==2


def test_local_judge_run_has_safe_model_hypotheses_only(tmp_path):
    ws=tmp_path/"workspace"
    ws.mkdir()
    plan=select_judge_pilot(inventory(),limit=5)
    called=[]
    def source(root,aid):
        called.append(aid)
        return Path("/nowhere/never-opened.mp4")
    def frames(path,duration,count):
        return [{"frameIndex":i,"requestedMs":(i+1)*1000,
                 "decodedPtsMs":None,"timestampBasis":"requested_seek_pts_unavailable",
                 "imageSha256":"a"*64,"imageBytes":b"FAKEPNG"}
                for i in range(count)]
    result=run_judge_pilot(ws,plan,frame_count=4,judge=lambda f:judgment(),
                           source_loader=source,frame_loader=frames)
    assert len(called)==5
    assert result["successes"]==5
    assert result["hypothesisSummary"]["suggestedFeatureDimensions"] == {"water_visibility":5}
    assert result["hypothesisSummary"]["sceneContexts"] == {"underwater_reef":5}
    assert result["hypothesisSummary"]["allStatisticsAreModelProposals"]
    assert result["scientificHeldoutEligible"] is False
    assert result["sourceVideoModified"] is False
    assert all(x["judgeStatus"]=="model_hypothesis" for x in result["proposals"])
    assert all("sourcePath" not in x for x in result["proposals"])


def test_a_bad_model_response_never_becomes_training_truth(tmp_path):
    ws=tmp_path/"workspace"
    ws.mkdir()
    plan=select_judge_pilot(inventory(),limit=3)
    result=run_judge_pilot(
        ws,plan,frame_count=2,
        judge=lambda f:{"subsport":"fabricated"},
        source_loader=lambda r,aid:Path("/unopened.mp4"),
        frame_loader=lambda p,d,n:[{
            "frameIndex":i,"requestedMs":i*1000,"decodedPtsMs":None,
            "timestampBasis":"unknown","imageSha256":"a"*64,
            "imageBytes":b"fake"} for i in range(n)])
    assert result["successes"]==0
    assert all(x["judgeStatus"]=="failed" for x in result["proposals"])
    assert all(x["judgeProposal"] is not None for x in result["proposals"]) is False


def test_retrieval_evaluation_refuses_provisional_group_splits():
    manifest={"version":DATA_VERSION,"featureModelKey":"histogram-v1",
              "embeddingDim":48,"samples":[]}
    for i in range(4):
        manifest["samples"].append({
            "sampleId":f"sample-{i}","sourceSha256":f"{i+1:064x}",
            "groupId":f"provisional_capture-{i}",
            "goal":"sport_action","subsport":"general_adventure",
            "startMs":0,"endMs":20000,
            "shard":f"shards/{i+1:024x}.npz",
            "shardSha256":"e"*64,"sampleCount":4,
        })
    training={"version":"klip-ktide-training-run-v1",
              "datasetManifestSha256":"d"*64,
              "teacherModelKey":"histogram-v1",
              "testNotUsedForSelection":True,
              "featureCacheOnly":True,"rawMediaRead":False,
              "seed":17,"splitAssignments":group_splits(manifest,seed=17)}
    with pytest.raises(ValueError,match="Provisional capture groups"):
        validate_training_lineage(training,manifest,"d"*64)



def test_feature_candidates_are_only_model_hypotheses_without_numeric_utility():
    proposal=validate_judge_proposal(judgment(),4)
    assert proposal["featureCandidates"][0]["dimension"]=="water_visibility"
    assert proposal["featureCandidates"][0]["measuredValue"] is None
    assert proposal["featureCandidates"][0]["evidenceSource"]=="model_hypothesis"
    broken=judgment()
    broken["featureCandidates"][0]["frameIndices"]=[9]
    with pytest.raises(ValueError,match="invalid sampled"):
        validate_judge_proposal(broken,4)
    broken=judgment()
    broken["featureCandidates"][0]["dimension"]="wildlife_speed_meters_per_second"
    with pytest.raises(ValueError,match="Unsupported scene dimension"):
        validate_judge_proposal(broken,4)

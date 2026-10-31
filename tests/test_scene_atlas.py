"""Regression suite for iterative human reviews, audio triage and visual diversity."""
from __future__ import annotations

import math
from pathlib import Path
import json

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.examples import (
    review_example, review_inventory, latest_examples,
)
from adventure_intelligence.audio_triage import audio_window
from adventure_intelligence.scene_atlas import (
    unit_vector, similarity, clip_feature, frames_for_asset,
    enrich_candidates, diverse_representatives,
)
from adventure_intelligence.semantic_jobs import encoded, digest
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.storyboard import plan_storyboard
from adventure_intelligence.acoustics import acoustic_metrics, AUDIO_VERSION
from adventure_intelligence.artifacts import write_artifact


@pytest.fixture
def sample(tmp_path, monkeypatch):
    folder=tmp_path/"DJI Originals"; folder.mkdir()
    videos=[]
    for name in ("DJI_A.MP4","DJI_B.MP4"):
        path=folder/name
        path.write_bytes(("ORIGINAL/"+name).encode())
        videos.append(path)
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda path: {"duration":40,"audio":False})
    workspace=tmp_path/"workspace"
    result=index_folder(folder,workspace,activity="ski")
    assert len(result["asset_ids"])==2
    return workspace,result["asset_ids"],videos


def add_semantics(db,workspace,aid,model,provider,frames):
    config_id="conf-"+provider+"-"+aid[:10]
    content={"assetId":aid,"configId":config_id,"provider":provider,
             "modelVersion":model,
             "config":{"providerConfig":{"resolvedRevision":"commit123",
                                         "imageProcessor":{"size":224}}},
             "frames":frames}
    raw=encoded(content)
    path=workspace/"semantics"
    path.mkdir(parents=True,exist_ok=True)
    filename=config_id+".json"
    (path/filename).write_text(raw)
    db.execute("""INSERT INTO semantic_jobs
      (asset_id,config_id,config_json,status,filename,sha256)
      VALUES (?,?,'{}','completed',?,?)""",(aid,config_id,filename,digest(raw)))
    db.execute("INSERT OR REPLACE INTO semantic_active VALUES (?,?,?)",
               (aid,provider,config_id))


def test_review_revisions_no_source_modification(sample):
    workspace,aids,videos=sample
    aid=aids[0]
    one=review_example(workspace,asset_id=aid,start_seconds=1,
                       end_seconds=8,label="ski_lift",decision="uncertain",
                       highlight="neutral",note="Could be a lift")
    assert one["status"]=="reviewed"
    two=review_example(workspace,asset_id=aid,start_seconds=1,
                       end_seconds=8,label="ski_lift",decision="confirmed",
                       highlight="skip",note="Definitely lift")
    assert one["exampleId"]==two["exampleId"]
    assert one["revisionId"]!=two["revisionId"]
    same=review_example(workspace,asset_id=aid,start_seconds=1,
                       end_seconds=8,label="ski_lift",decision="confirmed",
                       highlight="skip",note="Definitely lift")
    assert same["status"]=="unchanged"
    inventory=review_inventory(workspace)
    assert inventory["reviewed"]==1 and inventory["revisions"]==2
    assert inventory["examples"][0]["decision"]=="confirmed"
    assert inventory["examples"][0]["highlight"]=="skip"
    assert inventory["examples"][0]["revisionCount"]==2
    assert all(v.read_bytes()==("ORIGINAL/"+v.name).encode() for v in videos)


def test_rejects_invalid_reviews(sample):
    work,aids,videos=sample
    args={"asset_id":aids[0],"start_seconds":1,"end_seconds":3,
          "label":"ski_lift","decision":"confirmed"}
    for update in ({"end_seconds":42},{"start_seconds":float("nan")},
                   {"decision":"verified_by_ai"},{"label":"Ski Lift"},
                   {"highlight":"publish"}):
        with pytest.raises(ValueError):
            review_example(work,**{**args,**update})
    assert review_inventory(work)["reviewed"]==0


def test_overlap_conflict_flagged_for_re_review(sample):
    work,aids,_=sample
    for start,end,decision,highlight in (
        (1,9,"confirmed","keep"),(2,8,"rejected","skip")):
        review_example(work,asset_id=aids[0],start_seconds=start,end_seconds=end,
                       label="carving_flow",decision=decision,highlight=highlight)
    summary=review_inventory(work)
    assert len(summary["overlapConflicts"])==1
    assert summary["examples"][0]["reviewSplit"]==summary["examples"][1]["reviewSplit"]


def test_cheap_audio_is_never_an_action_label():
    stream=[{"t_ms":i*1000,"end_ms":(i+1)*1000,"rms":.002}
            for i in range(9)]
    result=audio_window(stream,0,9000)
    assert result["available"] is True
    assert not result["suggestVisualInspection"]
    assert "never alone excludes" in result["interpretation"]
    assert not audio_window(None,0,2000)["available"]
    stream[4]["rms"]=.4
    loud=audio_window(stream,0,9000)
    assert loud["unusualAcousticChange"] and loud["suggestVisualInspection"]


def test_audio_v2_features_silence_clipping_and_missing_fft():
    from array import array
    silence=array("h",[0]*8000).tobytes()
    metrics,_=acoustic_metrics(silence)
    assert metrics["clippingFraction"]==0
    loud=array("h",[32767]*8000).tobytes()
    metrics,_=acoustic_metrics(loud)
    assert metrics["clippingFraction"]==1.0
    assert metrics["peak"]>.99
    assert "v2" in AUDIO_VERSION


def test_cross_model_and_invalid_embeddings_not_compared():
    assert unit_vector([0,0]) is None
    assert unit_vector([float("nan"),0]) is None
    assert similarity([1,0],[1]) is None
    assert clip_feature([],None,0,1000)["reason"]=="semantic_model_unavailable"
    vector=clip_feature([{"tMs":500,"embedding":[4,0]},
                         {"tMs":1000,"embedding":[2,0]}],
                        "model",0,2000)
    assert vector["available"] and vector["sampleCount"]==2
    assert abs(vector["vector"][0]-1)<1e-8


def test_visual_diversity_prefers_distinct_representative():
    base={"duration":12.0,"startMs":0,"endMs":12000,"score":.9}
    clips=[
        {**base,"sourceAssetId":"a","_modelKey":"m","_vector":[1.,0.],"score":.95},
        {**base,"sourceAssetId":"b","_modelKey":"m","_vector":[.999,.045],"score":.93},
        {**base,"sourceAssetId":"c","_modelKey":"m","_vector":[0.,1.],"score":.86},
    ]
    chosen,meta=diverse_representatives(clips,max_seconds=24)
    assert [x["sourceAssetId"] for x in chosen]==["a","c"]
    assert meta["clusters"]==2
    assert all("_vector" not in x for x in chosen)
    clips[1]["_modelKey"]="different-model"
    chosen,meta=diverse_representatives(clips,max_seconds=24)
    assert chosen[1]["sourceAssetId"]=="b"


def test_active_verified_semantic_provenance_and_audio(sample):
    workspace,aids,videos=sample
    with connect(workspace) as db:
        for aid in aids:
            add_semantics(db,workspace,aid,"siglip-version","siglip2",[
                {"tMs":5000,"embedding":[.9,.1],"objects":[]},
                {"tMs":9000,"embedding":[1.,0.],"objects":[]},
            ])
        add_semantics(db,workspace,aids[0],"owl-version","owlv2",[
            {"tMs":5000,"objects":[{"label":"tree","score":.8}]}])
        write_artifact(db,workspace,aids[0],AUDIO_VERSION,[
            {"t_ms":i*1000,"end_ms":(i+1)*1000,"rms":.001}
            for i in range(40)])
        frames,key=frames_for_asset(db,workspace,aids[0],"siglip2")
        assert frames and key
        feature=clip_feature(frames,key,0,12000)
        assert feature["available"]
        candidate={"sourceAssetId":aids[0],"startMs":0,"endMs":12000,
                   "score":.9,"duration":12.0,"reasonCodes":[]}
        enriched,meta=enrich_candidates(db,workspace,[candidate])
        assert len(enriched)==1
        scene=enriched[0]
        assert "tree" in scene["visualEvidence"]["objectProposals"]
        assert scene["audioTriage"]["available"]
        assert scene["visualEvidence"]["modelKey"]
    assert all(v.read_bytes()==("ORIGINAL/"+v.name).encode() for v in videos)


def test_confirmed_skip_trims_cinematic_candidate(sample):
    workspace,aids,videos=sample
    with connect(workspace) as db:
        db.execute("""INSERT INTO episodes
          (id,asset_id,kind,start_ms,end_ms,score,event_ids_json,evidence_json,origin,version)
          VALUES ('ep',?,'sustained_motion_candidate',0,40000,.97,'[]','{}','derived','v1')""",
          (aids[0],))
    review_example(workspace,asset_id=aids[0],start_seconds=0,end_seconds=20,
                   label="ski_lift",decision="confirmed",highlight="skip")
    plan=plan_storyboard(workspace,"motion-review",max_seconds=40,
                         ranking="context",diversity="visual",provider="siglip2")
    assert all(c["startMs"]>=20300 for c in plan["segments"])
    assert plan["quality"]["atlas"]["provider"]=="siglip2"
    assert all(v.read_bytes()==("ORIGINAL/"+v.name).encode() for v in videos)

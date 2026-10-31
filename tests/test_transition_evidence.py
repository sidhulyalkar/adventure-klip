"""Edge-aware metadata and safe selective video transitions.

All semantic documents in tests are synthetic, with deliberately identical or
opposed vectors. No test asserts real-world object/species accuracy.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.examples import review_example
from adventure_intelligence.semantic_jobs import digest, encoded
from adventure_intelligence.transition_evidence import EdgeEvidence, compare_edges
from adventure_intelligence.sequence_editor import save_sequence, sequence_state, export_sequence
from adventure_intelligence.studio import studio_clip_metadata
from adventure_intelligence.reels import transition_styles, render_reel


@pytest.fixture
def world(tmp_path,monkeypatch):
    source=tmp_path/"originals"
    source.mkdir()
    a=source/"DJI_A.MP4"
    b=source/"DJI_B.MP4"
    a.write_bytes(b"original-a"*57)
    b.write_bytes(b"original-b"*63)
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda path: {"duration":14.0,"audio":False})
    root=tmp_path/"workspace"
    ids=index_folder(source,root,activity="unknown")["asset_ids"]
    before={p.name:p.read_bytes() for p in (a,b)}
    return root,ids,(a,b),before


def cache_document(db,root,aid,provider,frames,revision="frozen-r1"):
    job_id=provider+"-"+aid[:12]
    doc={"assetId":aid,"configId":job_id,"provider":provider,
         "modelVersion":"fake-v1",
         "config":{"providerConfig":{"resolvedRevision":revision,
                                     "imageProcessor":{"size":224}}},
         "frames":frames}
    raw=encoded(doc)
    target=root/"semantics"
    target.mkdir(exist_ok=True)
    (target/(job_id+".json")).write_text(raw)
    db.execute("""INSERT INTO semantic_jobs(asset_id,config_id,config_json,
                 status,filename,sha256)
                 VALUES (?,?,'{}','completed',?,?)""",
               (aid,job_id,job_id+".json",digest(raw)))
    db.execute("INSERT INTO semantic_active VALUES (?,?,?)",(aid,provider,job_id))


def populate(root,ids,*,different_model=False,with_owl=True):
    with connect(root) as db:
        for i,aid in enumerate(ids):
            vec=[1.,0.] if i==0 else [-1.,0.]
            # Head of second clip vs tail of first should be directly compared.
            cache_document(db,root,aid,"siglip2",
                [{"tMs":2000,"embedding":vec},{"tMs":8200,"embedding":vec}],
                "frozen-r2" if different_model and i else "frozen-r1")
            cache_document(db,root,aid,"context",
                [{"tMs":2000,"sceneScores":{
                   "method":"siglip2_image_text_cosine",
                   "similarities":{"submerged":.6,"at_surface":.3}}},
                 {"tMs":8200,"sceneScores":{
                   "method":"siglip2_image_text_cosine",
                   "similarities":{"submerged":.61,"at_surface":.32}}}])
            if with_owl:
                cache_document(db,root,aid,"owlv2",
                    [{"tMs":2000,"objects":[{"label":"fish","score":.44,"reviewed":False},
                          {"label":"shark","score":float(".9") if i==0 else .13}]},
                     {"tMs":8200,"objects":[{"label":"fish","score":.55},
                          {"label":"inaccurate species","score":1.1}]}])


def test_no_model_means_unknown_not_invented_object(world):
    root,ids,videos,before=world
    with connect(root) as db:
        edge=EdgeEvidence(db,root)
        a={"sourceAssetId":ids[0],"startMs":1000,"endMs":11000}
        b={"sourceAssetId":ids[1],"startMs":1000,"endMs":11000}
        result=compare_edges(edge,a,b)
    assert result["suggestedTransition"]=="cut"
    assert result["edgeVisualSimilarity"] is None
    assert result["sharedObjectQueries"]==[]
    assert result["outgoing"]["objectSampleCount"]==0
    assert result["outgoing"]["sceneContext"]==[]
    assert result["outgoing"]["embeddingAvailable"] is False
    assert {p.name:p.read_bytes() for p in videos}==before


def test_verified_caches_provide_edge_objects_without_identity_claim(world):
    root,ids,videos,before=world
    populate(root,ids)
    with connect(root) as db:
        reader=EdgeEvidence(db,root)
        clip_a={"sourceAssetId":ids[0],"startMs":1000,"endMs":11000}
        clip_b={"sourceAssetId":ids[1],"startMs":1000,"endMs":11000}
        result=compare_edges(reader,clip_a,clip_b,
                             first_activity="ski",second_activity="snorkeling")
    assert result["suggestedTransition"]=="fade"
    assert result["edgeVisualSimilarity"]==-1.
    assert "fish" in result["sharedObjectQueries"]
    assert result["outgoing"]["objectObservations"][0]["verified"] is False
    assert result["outgoing"]["objectObservations"][0]["identity"]=="unknown"
    assert not any(x["label"]=="inaccurate species"
                   for x in result["outgoing"]["objectObservations"])
    assert result["outgoing"]["contextSampleCount"]==1
    assert result["outgoing"]["sceneContext"]
    assert result["outgoing"]["audio"]["available"] is False
    assert {p.name:p.read_bytes() for p in videos}==before


def test_incompatible_model_cannot_create_false_similarity(world):
    root,ids,videos,before=world
    populate(root,ids,different_model=True)
    with connect(root) as db:
        reader=EdgeEvidence(db,root)
        a={"sourceAssetId":ids[0],"startMs":1000,"endMs":11000}
        b={"sourceAssetId":ids[1],"startMs":1000,"endMs":11000}
        result=compare_edges(reader,a,b)
    assert result["edgeVisualSimilarity"] is None
    assert result["visualComparable"] is False
    assert result["suggestedTransition"]=="cut"


def test_sequence_plan_adds_edge_advice_and_smart_styles(world):
    root,ids,videos,before=world
    populate(root,ids)
    examples=[]
    for aid in ids:
        r=review_example(root,asset_id=aid,start_seconds=1,end_seconds=11,
            label="highlight_moment",decision="confirmed",highlight="keep")
        examples.append(r["exampleId"])
    saved=save_sequence(root,entries=[{"exampleId":e} for e in examples],
                        expected_revision=None,name="Summer reel")
    state=sequence_state(root)
    assert state["joinHints"][0]["suggestedTransition"]=="fade"
    assert "fish" in state["joinHints"][0]["objectQueries"]
    assert state["joinHints"][0]["edgeEvidence"]["incoming"]["objectSampleCount"]==1
    doc=export_sequence(root,expected_revision=saved["revisionId"])
    plan=json.loads(Path(doc["path"]).read_text())
    assert plan["transitions"][0]["style"]=="fade"
    assert plan["transitionPolicy"].startswith("edge-evidence")
    assert transition_styles(plan,"smart")==["fade"]
    assert transition_styles(plan,"cut")==["cut"]
    assert transition_styles(plan,"fade")==["fade"]
    for invalid in ([{"style":"swipe"}],[],[{"style":"fade",
                   "leftExampleId":"ex-"+"0"*24,"rightExampleId":examples[1]}]):
        changed={**plan,"transitions":invalid}
        with pytest.raises(ValueError):
            transition_styles(changed,"smart")
    assert {p.name:p.read_bytes() for p in videos}==before


def test_smart_render_applies_fades_selectively_without_source_writes(world,monkeypatch):
    root,ids,videos,before=world
    for aid in ids:
        review_example(root,asset_id=aid,start_seconds=1,end_seconds=11,
            label="highlight_moment",decision="confirmed",highlight="keep")
    from adventure_intelligence.examples import latest_examples
    with connect(root) as db:
        refs=[x["example_id"] for x in latest_examples(db)]
    saved=save_sequence(root,entries=[{"exampleId":e} for e in refs],
                        expected_revision=None)
    doc=export_sequence(root,expected_revision=saved["revisionId"])
    plan=json.loads(Path(doc["path"]).read_text())
    plan["transitions"][0]["style"]="fade"
    commands=[]
    def fake_run(cmd,**kw):
        commands.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"fake-encoded-output")
    monkeypatch.setattr("adventure_intelligence.reels.require_tool",lambda x:"fake-ffmpeg")
    monkeypatch.setattr("adventure_intelligence.reels.subprocess.run",fake_run)
    output=root/"reels"/"selective-test.mp4"
    result=render_reel(root,plan,output,transition="smart")
    assert result["joinStyles"]==["fade"] and result["fadedJoins"]==1
    assert len(commands)==3
    assert "fade=t=out" in ",".join(commands[0])
    assert "fade=t=in" in ",".join(commands[1])
    assert output.read_bytes()==b"fake-encoded-output"
    assert {p.name:p.read_bytes() for p in videos}==before


def test_clip_metadata_endpoint_validates_bounds_and_never_exposes_vectors(world):
    root,ids,videos,before=world
    populate(root,ids)
    doc=studio_clip_metadata(root,ids[0],1000,11000)
    assert doc["status"]=="unverified_model_evidence"
    assert doc["head"]["objectSampleCount"]==1
    assert doc["tail"]["objectSampleCount"]==1
    assert "_vector" not in str(doc)
    with pytest.raises(ValueError):
        studio_clip_metadata(root,"../etc/passwd",1000,11000)
    with pytest.raises(ValueError):
        studio_clip_metadata(root,ids[0],-1,11000)
    with pytest.raises(ValueError):
        studio_clip_metadata(root,ids[0],1000,25000)
    assert {p.name:p.read_bytes() for p in videos}==before

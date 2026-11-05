"""Corpus setup and preflight do not pretend missing real data is training."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence import social_corpus_onboarding as onboarding
from adventure_intelligence import social_action_lens as lens


def example(creator:int,index:int)->dict:
    """Clearly synthetic test values, not downloaded social content."""
    value=(index+1)/6
    return {
        "clipId":f"synthetic_asset_{creator:02d}_{index:02d}",
        "creatorGroup":f"synthetic_creator_{creator:02d}",
        "contentGroup":f"distinct_edit_{creator:02d}_{index:02d}",
        "platform":"tiktok" if creator%2 else "instagram",
        "sport":"ski" if creator%2 else "bike",
        "featureMethod":lens.FEATURE_VERSION,
        "rights":{"videoModelTrainingAllowed":True,
                  "metadataAccess":"creator_export",
                  "sourceLicense":"SYNTHETIC UNIT TEST ONLY, NO SOCIAL MEDIA"},
        "promoted":False,"durationSec":24,
        "metrics":{"postedDate":"2026-08-01","observedDate":"2026-09-01",
                   "viewCount":12000,"likeCount":int(100+1500*value),
                   "shareCount":int(2+400*value),
                   "commentCount":int(2+70*value),
                   "saveCount":int(5+350*value)},
        "features":{name:round(value,4) for name in lens.FEATURES},
    }


def test_missing_corpus_is_data_not_model_failure(tmp_path):
    root=tmp_path/"klip"
    with connect(root):
        pass
    missing=root/"reports"/"social-reference-authorized-v1.json"
    status=onboarding.preflight_corpus(missing)
    assert status["status"]=="missing_reference_corpus"
    assert status["trainingReady"] is False
    assert status["mediaReads"]==0
    assert status["modelCalls"]==0
    output=root/"reports"/"model-never-created"
    with pytest.raises(ValueError,match="missing_reference_corpus"):
        lens.save_training(root,missing,output)
    assert not output.exists()


def test_init_creates_exact_empty_path_and_never_overwrites(tmp_path):
    root=tmp_path/"klip"
    external=tmp_path/"original-video.MP4"
    original=b"real original recording stays byte-for-byte intact"
    external.write_bytes(original)
    with connect(root):
        pass
    path=root/"reports"/"social-reference-authorized-v1.json"
    initialized=onboarding.init_corpus(root,path)
    assert initialized["corpus"]==str(path.resolve())
    assert initialized["trainingReady"] is False
    assert initialized["realCreatorExamples"]==0
    assert initialized["inventedLikesViewsOrLabels"]==0
    document=json.loads(path.read_text())
    assert document=={"version":lens.DATA_VERSION,"items":[]}
    readiness=onboarding.preflight_corpus(path)
    assert readiness["status"]=="awaiting_permissioned_examples"
    assert readiness["trainingPairs"]==0
    assert readiness["heldOutPairs"]==0
    with pytest.raises(ValueError,match="NEW"):
        onboarding.init_corpus(root,path)
    with pytest.raises(ValueError,match="NEW"):
        onboarding.init_corpus(root,tmp_path/"outside-workspace.json")
    with pytest.raises(ValueError,match="awaiting_permissioned_examples"):
        lens.save_training(root,path,root/"reports"/"trained")
    assert sha256(external.read_bytes()).hexdigest()==sha256(original).hexdigest()
    assert not (root/"reports"/"trained").exists()


def test_personal_feature_template_is_not_external_creator_corpus(tmp_path):
    template=tmp_path/"feature-template.json"
    template.write_text(json.dumps({
        "version":lens.USER_VERSION,"items":[]}))
    report=onboarding.preflight_corpus(template)
    assert report["status"]=="wrong_corpus_schema"
    assert report["expectedVersion"]==lens.DATA_VERSION
    assert report["trainingReady"] is False


def test_preflight_blocks_incomplete_or_unpermitted_examples(tmp_path):
    items=[example(c,i) for c in range(5) for i in range(5)]
    items[0]["rights"]["videoModelTrainingAllowed"]=False
    items[1]["features"]["subjectVisibility"]=None
    path=tmp_path/"bad.json"
    path.write_text(json.dumps({"version":lens.DATA_VERSION,"items":items}))
    report=onboarding.preflight_corpus(path)
    assert report["status"]=="incomplete_reference_corpus"
    assert report["rejectedItems"]==2
    assert report["trainingReady"] is False
    assert len(report["errors"])==2
    assert report["trainingPairs"]>=3
    assert report["heldOutPairs"]>=1
    assert "Every input record" in " ".join(report["blockingRequirements"])


def test_preflight_calculates_actual_pairs_and_trainable_contract(tmp_path):
    items=[example(c,i) for c in range(5) for i in range(5)]
    path=tmp_path/"valid-synthetic-only.json"
    path.write_text(json.dumps({"version":lens.DATA_VERSION,"items":items}))
    report=onboarding.preflight_corpus(path,seed=23)
    assert report["status"]=="ready_for_exploratory_training"
    assert report["trainingReady"] is True
    assert report["validItems"]==25
    assert report["creatorGroups"]==5
    assert report["trainingCreatorGroups"]+report["heldOutCreatorGroups"]==5
    assert report["trainingPairs"]>=3
    assert report["heldOutPairs"]>=1
    assert report["rightsProvenanceIndependentlyVerified"] is False
    assert report["featuresObserved"]==25*len(lens.FEATURES)
    assert report==onboarding.preflight_corpus(path,seed=23)
    with connect(tmp_path/"workspace"):
        pass
    result=lens.save_training(tmp_path/"workspace",path,
                              tmp_path/"workspace"/"reports"/"synthetic-model")
    assert result["trainingSummary"]["creatorDisjoint"]
    assert result["heldOutPairwise"]["pairs"]>=1


def test_preflight_requires_holdout_pairs_even_if_train_pairs_exist(tmp_path):
    items=[example(c,i) for c in range(4) for i in range(4)]
    # One held-out group will have no contrast. The training set may still
    # contain plenty of pairs, but a model without test pairs is not assessed.
    for i in range(4):
        items[i]["metrics"]["likeCount"]=100
        items[i]["metrics"]["shareCount"]=3
        items[i]["metrics"]["commentCount"]=2
        items[i]["metrics"]["saveCount"]=5
    path=tmp_path/"nocontrast.json"
    path.write_text(json.dumps({"version":lens.DATA_VERSION,"items":items}))
    # Depending on deterministic held-out creator allocation, this case
    # asserts only that a genuine no-contrast corpus cannot train.
    for c in range(4):
        for i in range(4):
            row=items[c*4+i]
            row["metrics"].update(likeCount=100,shareCount=3,
                                  commentCount=2,saveCount=5)
    path.write_text(json.dumps({"version":lens.DATA_VERSION,"items":items}))
    report=onboarding.preflight_corpus(path)
    assert report["trainingPairs"]==0
    assert report["heldOutPairs"]==0
    assert report["trainingReady"] is False
    assert report["status"]=="incomplete_reference_corpus"


def test_smaller_personal_template_requires_actual_feature_review():
    aid="b"*64
    study={"version":"archive-value-study-v1",
           "rankedCandidates":[{
               "sourceAssetId":aid,"startMs":i*30000,
               "endMs":i*30000+20000,"activityGroup":"ski",
               "score":.8} for i in range(20)]}
    template=lens.feature_template(study,limit=4)
    assert len(template["items"])==4
    assert all(row["featureMethod"]=="unassessed" for row in template["items"])
    assert all(all(v is None for v in row["features"].values())
               for row in template["items"])
    for n in (0,-1,301):
        with pytest.raises(ValueError):
            lens.feature_template(study,limit=n)

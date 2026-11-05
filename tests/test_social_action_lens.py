"""Social Lens tests use synthetic engagement patterns; never real creator media."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence import social_action_lens as lens


def _features(value:float)->dict:
    return {name:round(min(1.0,max(0.0,value)),4)
            for name in lens.FEATURES}


def _social_item(creator:int,which:int)->dict:
    # Synthetic, purposely monotonic rubric and engagement, never observed data.
    value=(which+1)/6
    return {
        "clipId":f"synthetic_clip_{creator:02d}_{which:02d}",
        "creatorGroup":f"pseudonym_{creator:02d}",
        "contentGroup":f"take_family_{creator:02d}_{which:02d}",
        "platform":"tiktok" if creator%2==0 else "instagram",
        "sport":"ski" if creator%2==0 else "snorkeling",
        "featureMethod":lens.FEATURE_VERSION,"features":_features(value),
        "rights":{"videoModelTrainingAllowed":True,
                  "sourceLicense":"SYNTHETIC UNIT TEST, NOT REAL MEDIA",
                  "metadataAccess":"creator_export"},
        "promoted":False,"durationSec":20,
        "metrics":{"postedDate":"2026-08-01","observedDate":"2026-09-01",
                   "viewCount":10000,
                   "likeCount":int(100+800*value),
                   "shareCount":int(3+250*value),
                   "saveCount":int(5+300*value),
                   "commentCount":int(2+80*value)},
    }


def corpus()->dict:
    return {"version":lens.DATA_VERSION,
            "items":[_social_item(c,n) for c in range(5) for n in range(5)]}


def test_synthetic_training_creator_disjoint_and_repeatable(tmp_path):
    document=corpus()
    file=tmp_path/"licensed_corpus.json"
    file.write_text(json.dumps(document))
    first=lens.train_social_head(file,seed=5)
    second=lens.train_social_head(file,seed=5)
    assert first==second
    assert first["featureVersion"]==lens.FEATURE_VERSION
    assert first["trainingSummary"]["creatorDisjoint"]
    assert first["trainingSummary"]["trainPairs"]>=3
    assert first["trainingSummary"]["heldOutPairs"]>=1
    assert first["heldOutPairwise"]["accuracy"]>=.8
    assert first["predictedViews"] is None
    assert first["calibratedPersonalKeepProbability"] is None
    assert not any("synthetic_clip_" in json.dumps(first)
                   for _ in (0,))


def test_rights_promoted_and_feature_provenance_are_hard_rejections(tmp_path):
    for change in [
        lambda x:x["rights"].update(videoModelTrainingAllowed=False),
        lambda x:x["rights"].update(sourceLicense=""),
        lambda x:x["rights"].update(metadataAccess="scraped"),
        lambda x:x.update(promoted=True),
        lambda x:x["features"].update(subjectVisibility=None),
        lambda x:x["features"].pop("imageClarity"),
        lambda x:x.update(featureMethod="unverified_model_guesses"),
        lambda x:x["metrics"].update(viewCount=2),
        lambda x:x["metrics"].update(shareCount=-1),
        lambda x:x["metrics"].update(observedDate="2026-08-02"),
    ]:
        doc=corpus()
        change(doc["items"][0])
        file=tmp_path/"wrong.json"
        file.write_text(json.dumps(doc))
        with pytest.raises(ValueError):
            lens.train_social_head(file)
    doc=corpus()
    doc["items"][1]["clipId"]=doc["items"][0]["clipId"]
    file.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match="Duplicate"):
        lens.load_training_corpus(file)


def test_matched_pairs_exclude_same_edit_and_wrong_age_or_platform():
    items=[lens._social_record(x) for x in corpus()["items"]]
    assert lens.make_pairs(items)
    a,b=deepcopy(items[:2])
    b["contentGroup"]=a["contentGroup"]
    assert lens.make_pairs([a,b])==[]
    b["contentGroup"]="other_edit_group"
    b["ageDays"]=a["ageDays"]+40
    assert lens.make_pairs([a,b])==[]
    b["ageDays"]=a["ageDays"]
    b["platform"]="instagram"
    assert lens.make_pairs([a,b])==[]
    b["platform"]=a["platform"]
    b["creatorGroup"]="different_creator"
    assert lens.make_pairs([a,b])==[]


def test_clip_comparison_and_null_template_never_fabricate_features(tmp_path):
    corpus_file=tmp_path/"licensed.json"
    corpus_file.write_text(json.dumps(corpus()))
    model=lens.train_social_head(corpus_file)
    aid="a"*64
    ski="ski"
    moments=[
        {"sourceAssetId":aid,"startMs":i*30000,"endMs":i*30000+28000,
         "score":score,"activityGroup":ski}
        for i,score in enumerate((.9,.8,.7,.6))]
    study={"version":"archive-value-study-v1","rankedCandidates":moments}
    template=lens.feature_template(study)
    assert len(template["items"])==4
    assert template["items"][0]["features"]["imageClarity"] is None
    empty=lens.compare_to_klip(model,study,template,top_k=2)
    assert empty["featureCompleteComparableCount"]==0
    assert empty["humanPreferenceAccuracy"] is None
    assert empty["socialPredictedViews"] is None

    # Best social feature row deliberately has lowest Klip score.
    for i,row in enumerate(template["items"]):
        row["features"]=_features((i+1)/5)
        row["featureMethod"]=lens.FEATURE_VERSION
    compared=lens.compare_to_klip(model,study,template,top_k=2)
    assert compared["featureCompleteComparableCount"]==4
    assert compared["bySport"]["ski"]["topK"]==2
    assert compared["bySport"]["ski"]["topKOverlap"]==0
    assert len(compared["bySport"]["ski"]["socialPromoted"])==2
    assert not compared["klipVsSocialWinnerVerified"]

    template["items"][0]["features"]["subjectVisibility"]=None
    with pytest.raises(ValueError):
        lens.compare_to_klip(model,study,template)
    template["items"][0]["features"]["subjectVisibility"]=.25
    template["items"][1]["sport"]="snorkeling"
    with pytest.raises(ValueError,match="Cross-sport"):
        lens.compare_to_klip(model,study,template)


def test_save_train_template_compare_are_local_non_destructive(tmp_path):
    workspace=tmp_path/"workspace"
    original=tmp_path/"outside-original.mp4"
    original.write_bytes(b"private original bytes should not be read or modified")
    original_digest=sha256(original.read_bytes()).hexdigest()
    with connect(workspace):
        pass
    corpus_file=tmp_path/"licensed.json"
    corpus_file.write_text(json.dumps(corpus()))
    training=lens.save_training(workspace,corpus_file,
                               workspace/"reports"/"social-trained")
    assert training["sourceVideosDownloaded"]==0
    study_file=workspace/"reports"/"value-study"/"study.json"
    study_file.parent.mkdir(parents=True)
    aid="a"*64
    rows=[{"sourceAssetId":aid,"startMs":i*30000,"endMs":i*30000+28000,
           "score":.9-.1*i,"activityGroup":"ski"}
          for i in range(4)]
    study_file.write_text(json.dumps({
        "version":"archive-value-study-v1","rankedCandidates":rows}))
    templ=lens.save_feature_template(workspace,study_file,
                                    workspace/"reports"/"feature-template")
    assert templ["observedFeatures"]==0
    p=Path(templ["template"])
    values=json.loads(p.read_text())
    for i,row in enumerate(values["items"]):
        row["featureMethod"]=lens.FEATURE_VERSION
        row["features"]=_features((i+1)/5)
    reviewed=workspace/"reports"/"rubric-reviewed.json"
    reviewed.write_text(json.dumps(values))
    report=lens.save_comparison(workspace,Path(training["model"]),study_file,
                                reviewed,workspace/"reports"/"compare",top_k=2)
    assert report["compared"]==4
    assert not report["rankingAccuracyClaim"]
    assert report["sourceVideosRead"]==0
    assert sha256(original.read_bytes()).hexdigest()==original_digest
    with pytest.raises(ValueError,match="NEW"):
        lens.save_training(workspace,corpus_file,workspace/"reports"/"social-trained")


def test_no_causal_claim_or_metrics_imputation(tmp_path):
    corpus_file=tmp_path/"licensed.json"
    document=corpus()
    document["items"][0]["metrics"].pop("saveCount")
    corpus_file.write_text(json.dumps(document))
    with pytest.raises(ValueError,match="engagement"):
        lens.train_social_head(corpus_file)
    document["items"][0]["metrics"]["saveCount"]=0
    document["items"][0]["metrics"]["viewCount"]=201
    document["items"][0]["metrics"]["likeCount"]=1
    document["items"][0]["metrics"]["shareCount"]=1
    document["items"][0]["metrics"]["commentCount"]=1
    corpus_file.write_text(json.dumps(document))
    model=lens.train_social_head(corpus_file)
    assert model["calibratedSocialEngagementProbability"] is None
    assert model["heldOutPairwise"]["pairs"]>=1

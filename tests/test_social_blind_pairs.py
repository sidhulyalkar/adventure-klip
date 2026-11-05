"""Synthetic, private A/B review qualification. No human labels are fabricated."""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from pathlib import Path
import json

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence import social_blind_pairs as pairs
from adventure_intelligence.social_action_lens import USER_VERSION,FEATURE_VERSION,FEATURES
from adventure_intelligence.social_temporal_evidence import VERSION as TEMPORAL_VERSION
from adventure_intelligence.studio_preview_judge import preview_paths


def fixture_study(tmp_path,*,measured=12):
    root=tmp_path/"workspace"
    with connect(root):
        pass
    candidate_rows=[]
    scored=[]
    temporal=[]
    sports=["ski","bike","snorkeling"]
    for i in range(12):
        sport=sports[i%3]
        aid=f"{i+1:064x}"
        start=1000
        end=21000
        score=round(.91-(i//3)*.17-(i%3)*.02,4)
        candidate_rows.append({
            "assetId":aid,"sport":sport,
            "startMs":start,"endMs":end,"featureVersion":FEATURE_VERSION,
            "featureMethod":"unassessed",
            "features":{name:None for name in FEATURES},
        })
        scored.append({
            "sourceAssetId":aid,"startMs":start,"endMs":end,
            "score":score,"activityGroup":sport,
        })
        if i>=measured:
            continue
        video,receipt=preview_paths(root,aid,start,end)
        video.parent.mkdir(parents=True,exist_ok=True)
        data=(f"synthetic H264 receipt only {i}".encode())*8
        video.write_bytes(data)
        receipt.write_text(json.dumps({
            "version":"studio-preview-local-judge-v1",
            "assetId":aid,"startMs":start,"endMs":end,
            "originalSha256Verified":True,
            "previewSha256":sha256(data).hexdigest(),
        }))
        temporal.append({
            "assetId":aid,"sport":sport,
            "startMs":start,"endMs":end,
            "previewVerified":True,
            "status":"temporal_visual_metrics_observed",
            "visualTemporal":{"frameCount":30,"frameChangeMedian":.15},
        })
    feature={"version":USER_VERSION,"featureVersion":FEATURE_VERSION,
             "items":candidate_rows}
    study={"version":"archive-value-study-v1",
           "rankedCandidates":scored}
    root_report=root/"reports"/"study"
    root_report.mkdir(parents=True)
    study_file=root_report/"study.json"
    feature_file=root_report/"feature-template.json"
    temporal_file=root_report/"temporal-evidence.json"
    feature_file.write_text(json.dumps(feature))
    study_file.write_text(json.dumps(study))
    temporal_file.write_text(json.dumps({
        "version":TEMPORAL_VERSION,
        "mode":"local_preview_temporal_extraction",
        "sourceTemplateSha256":sha256(feature_file.read_bytes()).hexdigest(),
        "candidates":temporal,
    }))
    return root,study_file,feature_file,temporal_file


def test_blind_pairs_source_distinct_balanced_and_repeatable(tmp_path):
    root,study,features,temporal=fixture_study(tmp_path)
    report,selected=pairs.make_pair_study(root,study,features,temporal,
                                           seed=10,max_pairs=10)
    assert report["pairCount"]>1
    assert report["reviewIsBlinded"] is True
    assert report["originalMediaOpened"] is False
    assert report["personalRankingAccuracy"] is None
    assert report["coverage"]["eligibleUnique"]==12
    assert len({p["pairId"] for p in selected})==len(selected)
    use=Counter()
    for row in selected:
        assert row["sport"] in ("ski","bike","snorkeling")
        assert row["a"]["assetId"]!=row["b"]["assetId"]
        assert row["a"]["sport"]==row["sport"]==row["b"]["sport"]
        use.update((row["a"]["clipId"],row["b"]["clipId"]))
        assert row["higherKlipSide"] in ("a","b","tie")
    assert max(use.values())<=2
    assert selected==pairs.make_pair_study(
        root,study,features,temporal,seed=10,max_pairs=10)[1]


def test_browser_is_blind_playable_and_local_only(tmp_path):
    root,study,features,temporal=fixture_study(tmp_path)
    destination=root/"reports"/"pairs-v1"
    saved=pairs.write_pair_study(root,study,features,temporal,destination,
                                 seed=7,max_pairs=8)
    assert saved["pairs"]>0
    assert saved["originalVideosRead"]==0
    manifest=Path(saved["manifest"])
    assert saved["manifestSha256"]==sha256(manifest.read_bytes()).hexdigest()
    html=Path(saved["html"]).read_text()
    assert "Klip · A/B preference study" in html
    assert "Export JSON reviews" in html
    assert "Play verified interval" not in html
    assert "../studio-preview-cache-v1/" in html
    assert "klipScore" not in html
    assert "higherKlipSide" not in html
    assert "localhost" not in html
    assert "http://" not in html and "https://" not in html
    with pytest.raises(ValueError,match="NEW"):
        pairs.write_pair_study(root,study,features,temporal,destination)


def test_frozen_export_evaluation_reports_decisive_denominator(tmp_path):
    root,study,features,temporal=fixture_study(tmp_path)
    destination=root/"reports"/"pairs-v1"
    saved=pairs.write_pair_study(root,study,features,temporal,destination,
                                 max_pairs=9)
    report=json.loads(Path(saved["manifest"]).read_text())
    votes=[]
    for i,pair in enumerate(report["pairs"]):
        mode=("agree","disagree","tie","neither")[i%4]
        preferred=pair["higherKlipSide"]
        decision=(preferred if mode=="agree" else
                  ("b" if preferred=="a" else "a") if mode=="disagree"
                  else mode)
        votes.append({"pairId":pair["pairId"],
                      "choice":decision,
                      "reasons":["sustained_action"] if mode!="neither" else [],
                      "notes":"synthetic test label"})
    reviews=root/"reports"/"export.json"
    reviews.write_text(json.dumps({
        "version":pairs.REVIEW_VERSION,
        "manifestSha256":saved["manifestSha256"],"reviews":votes,
    }))
    result=pairs.evaluate_pair_reviews(saved["manifest"],reviews)
    assert result["pairsAssessed"]==len(votes)
    assert result["pairsUnreviewed"]==0
    assert result["accuracyClaimValid"] is False
    assert result["modelTrainingPerformed"] is False
    assert result["videoFilesOpened"]==0
    assert result["distinctSourceAssetsAssessed"]>=3
    assert result["agreementWithKlipOnDecisivePairs"] is not None
    assert result["outcomeCounts"]["disagrees_with_klip"]>=1
    assert result["reasonCounts"]["sustained_action"]>=1
    output=pairs.write_pair_results(root,saved["manifest"],reviews,
                                   root/"reports"/"results-v1")
    assert output["pairsAssessed"]==len(votes)
    assert Path(output["report"]).is_file()


def test_missing_coverage_refuses_to_fake_rank_preference(tmp_path):
    root,study,features,temporal=fixture_study(tmp_path,measured=1)
    report,selected=pairs.make_pair_study(root,study,features,temporal)
    assert selected==[]
    assert report["pairCount"]==0
    output=pairs.write_pair_study(root,study,features,temporal,
                                  root/"reports"/"empty-pairs")
    assert output["status"]=="no_comparable_verified_pairs"
    assert output["pairs"]==0


def test_preview_tamper_and_mismatched_feature_fingerprint_are_blocked(tmp_path):
    root,study,features,temporal=fixture_study(tmp_path)
    video,_=preview_paths(root,f"{1:064x}",1000,21000)
    video.write_bytes(video.read_bytes()+b"tamper")
    report,selected=pairs.make_pair_study(root,study,features,temporal)
    assert report["coverage"]["excluded"]["staleOrTamperedPreview"]==1
    assert report["coverage"]["eligibleUnique"]==11
    doc=json.loads(temporal.read_text())
    doc["sourceTemplateSha256"]="f"*64
    temporal.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match="different feature template"):
        pairs.make_pair_study(root,study,features,temporal)


def test_corrupted_reviews_and_invalid_pair_budget_abstain(tmp_path):
    root,study,features,temporal=fixture_study(tmp_path)
    for n in (0,-1,41):
        with pytest.raises(ValueError):
            pairs.make_pair_study(root,study,features,temporal,max_pairs=n)
    output=pairs.write_pair_study(root,study,features,temporal,
                                  root/"reports"/"pairs")
    manifest=json.loads(Path(output["manifest"]).read_text())
    first=manifest["pairs"][0]
    review={
        "version":pairs.REVIEW_VERSION,
        "manifestSha256":output["manifestSha256"],
        "reviews":[{"pairId":first["pairId"],"choice":"a",
                    "reasons":[],"notes":""}],
    }
    reviews=root/"reports"/"vote.json"
    reviews.write_text(json.dumps(review))
    assert pairs.evaluate_pair_reviews(output["manifest"],reviews)["pairsAssessed"]==1
    wrong=json.loads(json.dumps(review))
    wrong["manifestSha256"]="f"*64
    reviews.write_text(json.dumps(wrong))
    with pytest.raises(ValueError,match="fingerprint"):
        pairs.evaluate_pair_reviews(output["manifest"],reviews)
    wrong=json.loads(json.dumps(review))
    wrong["reviews"][0]["reasons"]=["unsupported_fact"]
    reviews.write_text(json.dumps(wrong))
    with pytest.raises(ValueError,match="Invalid"):
        pairs.evaluate_pair_reviews(output["manifest"],reviews)
    wrong=json.loads(json.dumps(review))
    wrong["reviews"]*=2
    reviews.write_text(json.dumps(wrong))
    with pytest.raises(ValueError,match="duplicate"):
        pairs.evaluate_pair_reviews(output["manifest"],reviews)
    wrong=json.loads(json.dumps(review))
    wrong["reviews"][0]["choice"]="A is absolutely amazing"
    reviews.write_text(json.dumps(wrong))
    with pytest.raises(ValueError,match="Invalid"):
        pairs.evaluate_pair_reviews(output["manifest"],reviews)

"""Synthetic, source-safe end-to-end tests for action highlight annotation."""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from pathlib import Path
import json

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.cli import main
from adventure_intelligence.highlight_curation_pilot import (
    make_curation_pilot, save_curation_pilot,
)
from adventure_intelligence.highlight_curation_review import (
    import_curation_reviews, REVIEW_VERSION,
)
from adventure_intelligence.highlight_curation_validate import (
    audit_highlight_curation_manifest,
)
from adventure_intelligence.social_action_lens import USER_VERSION, FEATURE_VERSION
from adventure_intelligence.studio_preview_judge import preview_paths


@pytest.fixture
def dataset(tmp_path):
    root=tmp_path/"workspace"
    ids=[sha256(f"private-synthetic-source-{i}".encode()).hexdigest()
         for i in range(20)]
    # Source file paths deliberately point to UNMOUNTED external storage.
    # There is no file to decode, and planning must not check it.
    with connect(root) as db:
        for i,aid in enumerate(ids):
            activity=("unknown","ski","bike","snorkeling")[i%4]
            db.execute("INSERT INTO assets(id,sha256,stored_path,size_bytes,suffix,duration,activity) "
                       "VALUES(?,?,?,?,?,?,?)",
                       (aid,aid,f"/Volumes/UNMOUNTED_DJI/DCIM/VID_{i:04}.MP4",
                        8_000_000_000,".MP4",None if i==19 else 80.,activity))
        db.commit()
    rows=[]
    feature_rows=[]
    for i in range(7):
        aid=ids[i]
        row={"sourceAssetId":aid,"startMs":1000,
             "endMs":21000,"score":round(.9-.09*i,4),
             "activityGroup":(
                 ("ski","bike","snorkeling")[i%3] if i%4
                 else "unresolved"),
             "kind":"synthetic_hypothesis"}
        rows.append(row)
        if i<4:
            feature_rows.append({
                "assetId":aid,"startMs":1000,"endMs":21000,
                "sport":"other" if row["activityGroup"]=="unresolved"
                        else row["activityGroup"],
                "featureVersion":FEATURE_VERSION,
                "features":{},"featureMethod":"unassessed"})
    study=root/"reports"/"frozen"/"study.json"
    template=root/"reports"/"template"/"feature-template.json"
    study.parent.mkdir(parents=True)
    template.parent.mkdir(parents=True)
    study.write_text(json.dumps({
        "version":"archive-value-study-v1",
        "rankedCandidates":rows,
        "rankedCandidatesTruncated":0,
    }))
    template.write_text(json.dumps({
        "version":USER_VERSION,"featureVersion":FEATURE_VERSION,
        "items":feature_rows,
    }))
    return root,ids,study,template


def _add_preview(root,aid,start=1000,end=21000):
    video,receipt=preview_paths(root,aid,start,end)
    video.parent.mkdir(parents=True,exist_ok=True)
    # Synthetic receipt-confirmed bytes only; this is NOT a playable MP4.
    blob=(f"verified-synthetic-proxy-{aid}".encode())*6
    video.write_bytes(blob)
    receipt.write_text(json.dumps({
        "version":"studio-preview-local-judge-v1",
        "assetId":aid,"startMs":start,"endMs":end,
        "previewSha256":sha256(blob).hexdigest(),
        "originalSha256Verified":True,
    }))
    return video


def _review(rid,name,choice="keep",outcome="unknown",visibility="ambiguous"):
    return {
        "recordId":rid,"annotatorId":name,
        "role":"expert_coach" if name.endswith("a") else "casual_spectator",
        "goal":"athletic_skill","choice":choice,
        "outcome":outcome,"outcomeVisible":visibility,
        "technicalQuality":4,"aestheticQuality":3,
        "eventReadability":2,
        "rationale":"Visible event and outcome unclear from this limited view",
    }


def _save_reviews(path,digest,reviews):
    path.write_text(json.dumps({
        "version":REVIEW_VERSION,
        "manifestSha256":digest,
        "reviews":reviews,
    }))
    return path


def test_plans_full_source_roster_and_unrepresented_control_without_sdcard(dataset,monkeypatch):
    root,ids,study,template=dataset
    old_stat,old_resolve,old_is_file=Path.stat,Path.resolve,Path.is_file
    def guard(old):
        def checked(self,*args,**kwargs):
            if str(self).startswith("/Volumes/UNMOUNTED_DJI"):
                pytest.fail("Original source path should not be probed")
            return old(self,*args,**kwargs)
        return checked
    monkeypatch.setattr(Path,"stat",guard(old_stat))
    monkeypatch.setattr(Path,"is_file",guard(old_is_file))
    monkeypatch.setattr(Path,"resolve",guard(old_resolve))
    report,manifest=make_curation_pilot(root,study,template,limit=12,seed=104)
    assert report["catalogSourceRecords"]==20
    assert report["catalogMetadataInvalidSources"]==1
    assert report["observedEventGroundTruthCreated"]==0
    assert report["physicalSourceMediaReads"]==0
    assert report["selectedRecords"]==len(manifest["records"])
    assert report["selectedUniqueSources"]>=6
    assert report["selectionRoles"].get("unrepresented_source_control",0)>0
    assert len({r["recordId"] for r in manifest["records"]})==len(manifest["records"])
    for row in manifest["records"]:
        assert row["event"]["observedOutcome"]=="unknown"
        assert row["sportContext"]["executionEvidenceStatus"]=="unknown"
        assert row["rights"]["rightsStatus"]=="pending"
        assert row["observerJudgments"]==[]
        assert row["split"]["partition"]=="unassigned"
        assert row["split"]["outingGroupId"]=="unresolved-outing-all"
        assert row["filming"]["technicalQuality"] is None
    again=make_curation_pilot(root,study,template,limit=12,seed=104)
    assert (report,manifest)==again


def test_creates_blind_review_without_source_reads_and_only_verified_cache(dataset,tmp_path):
    root,ids,study,template=dataset
    _add_preview(root,ids[0])
    dest=root/"reports"/"curation-v1"
    result=save_curation_pilot(root,study,template,dest,limit=12)
    assert result["candidateWindows"]>0
    assert result["verifiedReviewProxies"]==1
    assert result["originalSourceReads"]==0
    assert result["trainingEligibleRecords"]==0
    html=Path(result["reviewPage"]).read_text()
    assert "Blind Action Review" in html
    assert "Export JSON reviews" in html
    assert 'media/' in html
    assert "Heuristic" not in html
    assert "source_original" not in html
    assert "/Volumes/" not in html
    assert sha256(Path(result["manifest"]).read_bytes()).hexdigest()==result["manifestSha256"]
    preflight=audit_highlight_curation_manifest(Path(result["manifest"]))
    assert preflight["records"]==result["candidateWindows"]
    assert preflight["trainingIsAuthorizedByThisTool"] is False
    assert preflight["humanVerifiedOpinionRecords"]==0
    assert not any((root/"reports"/"curation-v1"/"media").glob("*.MOV"))
    with pytest.raises(ValueError,match="NEW"):
        save_curation_pilot(root,study,template,dest,limit=12)


def test_import_two_independent_humans_without_collapsing_disagreement(dataset,tmp_path):
    root,ids,study,template=dataset
    _add_preview(root,ids[0])
    pilot=root/"reports"/"pilot"
    created=save_curation_pilot(root,study,template,pilot,limit=12)
    manifest=json.loads(Path(created["manifest"]).read_text())
    presentation=json.loads((pilot/"presentation.json").read_text())
    ready=[rid for rid,entry in presentation["records"].items()
           if entry["verifiedProxyReady"]]
    assert len(ready)==1
    rid=ready[0]
    a=_save_reviews(tmp_path/"a.json",created["manifestSha256"],
                    [_review(rid,"reviewer-a","keep","unknown","no")])
    b=_save_reviews(tmp_path/"b.json",created["manifestSha256"],
                    [_review(rid,"reviewer-b","skip","failed","yes")])
    summary=import_curation_reviews(root,pilot,[a,b],root/"reports"/"import")
    assert summary["humanSubmittedJudgments"]==2
    assert summary["multiRaterDisagreements"]==1
    assert summary["trainingEligibleRecords"]==0
    assert summary["originalSourceReads"]==0
    rows=json.loads(Path(summary["curatedManifest"]).read_text())["records"]
    item=next(x for x in rows if x["recordId"]==rid)
    assert len(item["observerJudgments"])==2
    assert item["event"]["observedOutcome"]=="unknown"
    assert item["filming"]["technicalQuality"] is None
    assert item["rights"]["sourceTrainingAuthorized"] is False
    assert item["evidenceStatus"]["humanRatingsFrozen"] is False
    check=audit_highlight_curation_manifest(Path(summary["curatedManifest"]))
    assert check["humanVerifiedOpinionRecords"]==2
    assert check["trainingIsAuthorizedByThisTool"] is False


def test_bad_reviews_or_missing_proxy_cannot_forge_event_grounding(dataset,tmp_path):
    root,ids,study,template=dataset
    _add_preview(root,ids[0])
    pilot=root/"reports"/"bad-review-pilot"
    created=save_curation_pilot(root,study,template,pilot,limit=12)
    pres=json.loads((pilot/"presentation.json").read_text())["records"]
    ready=next(r for r in pres if pres[r]["verifiedProxyReady"])
    missing=next(r for r in pres if not pres[r]["verifiedProxyReady"])
    cases=[
        [_review(missing,"reviewer-a")],
        [_review(ready,"reviewer-a","keep","completed","no")],
        [_review(ready,"reviewer-a","keep","unknown","ambiguous"),
         _review(ready,"reviewer-a","skip","unknown","ambiguous")],
    ]
    for i,reviews in enumerate(cases):
        f=_save_reviews(tmp_path/f"bad{i}.json",created["manifestSha256"],reviews)
        with pytest.raises(ValueError):
            import_curation_reviews(root,pilot,[f],root/"reports"/f"rejected-{i}")
    wrong=_save_reviews(tmp_path/"other.json","0"*64,[_review(ready,"reviewer-a")])
    with pytest.raises(ValueError,match="does not match"):
        import_curation_reviews(root,pilot,[wrong],root/"reports"/"wrong")
    linked=pilot/"media"/(ready+".mp4")
    linked.write_bytes(b"tampered-proxy"*30)
    f=_save_reviews(tmp_path/"valid-shape.json",created["manifestSha256"],
                    [_review(ready,"reviewer-a")])
    with pytest.raises(ValueError,match="proxy changed"):
        import_curation_reviews(root,pilot,[f],root/"reports"/"tampered")


def test_study_template_mismatch_detected_and_controls_not_truth(dataset):
    root,ids,study,template=dataset
    doc=json.loads(template.read_text())
    doc["items"][0]["startMs"]=3000
    template.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match="Baseline candidate missing"):
        make_curation_pilot(root,study,template,limit=12)


def test_cli_pilot_and_import_surface(dataset,tmp_path,capsys):
    root,ids,study,template=dataset
    pilot=root/"reports"/"cli-pilot"
    assert main(["--workspace",str(root),"highlight-curation-pilot",
                 "--study",str(study),"--features",str(template),
                 "--limit","8","--output-dir",str(pilot)])==0
    report=json.loads(capsys.readouterr().out)
    assert report["candidateWindows"]>=4
    assert report["originalSourceReads"]==0
    assert report["verifiedReviewProxies"]==0

"""Qualification of pale-sand targeted refinement and explicit human labels."""
from io import BytesIO
import json

import pytest

from adventure_intelligence.activity_intelligence import run_activity_scan, _blue_cue
from adventure_intelligence.activity_refinement import (
    _scores, _summarize, _candidates, _review_html, refine_activities,
)
from adventure_intelligence.activity_confirm import confirm_activity
from adventure_intelligence.catalog import connect
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.cli import main


class FirstPassUncertain:
    def __init__(self,*,allow_model_download=False):
        self.revision="fixed-commit-id"
        self.processor_config={"test":"v1"}
        self.text_vectors={
            "snorkeling":[[1.,0.,0.,0.]],"ski":[[1.,0.,0.,0.]],
            "bike":[[1.,0.,0.,0.]],"other":[[1.,0.,0.,0.]]}
    def image_vectors(self,imgs):
        return [[1.,0.,0.,0.] for _ in imgs]


class RefineUnderwater:
    def __init__(self,*,allow_model_download=False):
        from adventure_intelligence.activity_intelligence import _hash
        self.revision="fixed-commit-id"
        self.processor_hash=_hash({"test":"v1"})
        self.text_vectors={
            "underwater":[[1.,0.,0.,0.]],
            "snorkel_surface":[[0.,0.,0.,1.]],
            "kayak_boat":[[0.,1.,0.,0.]],
            "beach_wading":[[0.,0.,1.,0.]],
            "ski":[[-1.,0.,0.,0.]],
            "bike":[[0.,-1.,0.,0.]],
            "other":[[0.,0.,0.,-1.]]}
    def image_vectors(self,imgs):
        return [[1.,0.,0.,0.] for _ in imgs]


@pytest.fixture
def setup(tmp_path,monkeypatch):
    src=tmp_path/"camera"
    src.mkdir()
    original=b"short-immutable-video" * 1000
    file=src/"DJI_20241227144333_0089_D.MP4"
    file.write_bytes(original)
    ws=tmp_path/"workspace"
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda p:{"duration":90.,"audio":False,
                                  "capture_time":None})
    aid=index_folder(src,ws,activity="unknown")["asset_ids"][0]
    def sand_png(path,t,width=384):
        from PIL import Image
        image=Image.new("RGB",(384,216),(222,215,190))
        buffer=BytesIO()
        image.save(buffer,format="PNG")
        return buffer.getvalue(),{"decodedPtsMs":t+20,
                                  "timestampBasis":"decoded_frame_pts"}
    monkeypatch.setattr("adventure_intelligence.activity_intelligence.SiglipActivityClassifier",
                        FirstPassUncertain)
    monkeypatch.setattr("adventure_intelligence.activity_intelligence.extract_frame_png",
                        sand_png)
    first=ws/"reports"/"activity-overnight-v1"
    result=run_activity_scan(ws,first,frames=5)
    assert result["processedThisRun"]==1
    assert "unknown" in json.loads((first/"records.jsonl").read_text())["record"]["decision"]["proposedActivity"]
    return ws,aid,file,original,first,sand_png


def test_white_sand_blue_fraction_zero_but_underwater_review_candidate(setup,monkeypatch):
    ws,aid,file,raw,first,decoder=setup
    png,_=decoder(file,1000)
    assert _blue_cue(png)["lowerHalfBlueFraction"]==0.
    monkeypatch.setattr("adventure_intelligence.activity_refinement.RefineModel",
                        RefineUnderwater)
    monkeypatch.setattr("adventure_intelligence.activity_refinement.extract_frame_png",
                        decoder)
    folder=ws/"reports"/"sandbar-pass"
    result=refine_activities(ws,first,folder,mode="sampled")
    assert result["processedThisRun"]==1
    assert result["statusCounts"]["underwater_candidate_needs_review"]==1
    refined=json.loads((folder/"records.jsonl").read_text())["record"]
    assert refined["decision"]["readyForCatalogApply"] is False
    assert refined["decision"]["confidence"] is None
    assert refined["decision"]["underwaterVotes"]==5
    assert refined["sourceMediaRechecked"] is True
    assert len(refined["observations"])==5
    assert all(o["sourceDecodedPtsMs"]==o["requestedMs"]+20
               for o in refined["observations"])
    assert refined["frameDecodeErrors"]==[]
    assert len(refined["images"])==3
    assert all((folder/p).is_file() for p in refined["images"])
    assert file.read_bytes()==raw
    with connect(ws) as db:
        assert db.execute("SELECT activity FROM assets WHERE id=?",(aid,)).fetchone()[0]=="unknown"
    assert (folder/"review.html").is_file()
    rerun=refine_activities(ws,first,folder,mode="sampled")
    assert rerun["processedThisRun"]==0


def test_cached_thumbnail_mode_reads_no_source_video(setup,monkeypatch):
    ws,aid,file,raw,first,decoder=setup
    monkeypatch.setattr("adventure_intelligence.activity_refinement.RefineModel",
                        RefineUnderwater)
    monkeypatch.setattr("adventure_intelligence.activity_refinement.extract_frame_png",
                        lambda *a,**kw: pytest.fail("cached thumbnail must not trigger decode"))
    folder=ws/"exports"/"thumbnail-rescore"
    result=refine_activities(ws,first,folder,mode="thumbnail")
    assert result["processedThisRun"]==1
    refined=json.loads((folder/"records.jsonl").read_text())["record"]
    assert refined["sourceMediaRechecked"] is False
    assert refined["evidenceSource"]=="first_pass_single_thumbnail_only"
    assert refined["decision"]["validFrames"]==1
    assert refined["decision"]["status"]=="insufficient_evidence"
    assert file.read_bytes()==raw


def test_kayak_is_not_snorkeling(setup):
    scores={
        "underwater":[[0.,0.,0.]],"snorkel_surface":[[0.,0.,1.]],
        "kayak_boat":[[1.,0.,0.]],"beach_wading":[[0.,1.,0.]],
        "ski":[[-1.,0.,0.]],"bike":[[0.,-1.,0.]],
        "other":[[0.,0.,-1.]]}
    row=_scores([1.,0.,0.],scores)
    assert row["vote"]=="kayak_boat"
    assert row["relativeMargin"]>=1.
    result=_summarize([row,row,row,row,row])
    assert result["status"]=="surface_water_activity_needs_review"
    assert result["readyForCatalogApply"] is False


def test_first_pass_candidate_filter_is_explicit():
    records={
        "a":{"decision":{"status":"ambiguous_needs_review","proposedActivity":"unknown"}},
        "b":{"decision":{"status":"ambiguous_needs_review","proposedActivity":"snorkeling"}},
        "c":{"decision":{"status":"strong_candidate","proposedActivity":"ski"}},
        "d":{"decision":{"status":"ambiguous_needs_review","proposedActivity":"other"}},
    }
    base={"errorsThisRun":[{"assetId":"e"*64}]}
    found=_candidates(records,base,include_snorkeling_ambiguous=False,
                      include_other_ambiguous=False,include_failed=True)
    assert [x["assetId"] for x in found]==["a","e"*64]
    expanded=_candidates(records,base,include_snorkeling_ambiguous=True,
                         include_other_ambiguous=True,include_failed=True)
    assert {x["assetId"] for x in expanded}=={"a","b","d","e"*64}


def test_fails_closed_when_a_new_video_fails_decoding(setup,monkeypatch):
    ws,aid,file,raw,first,decoder=setup
    monkeypatch.setattr("adventure_intelligence.activity_refinement.RefineModel",
                        RefineUnderwater)
    monkeypatch.setattr("adventure_intelligence.activity_refinement.extract_frame_png",
                        lambda *a,**kw: (_ for _ in ()).throw(
                            ValueError("FFmpeg failed to decode the requested video frame")))
    folder=ws/"reports"/"failed-refine"
    report=refine_activities(ws,first,folder)
    assert report["processedThisRun"]==0
    assert report["errorsThisRun"]==1
    assert not (folder/"records.jsonl").read_text().strip()
    with connect(ws) as db:
        assert db.execute("SELECT activity FROM assets WHERE id=?",(aid,)).fetchone()[0]=="unknown"


def test_manual_confirmation_requires_review_and_preserves_source(setup):
    ws,aid,file,raw,first,decoder=setup
    before=confirm_activity(ws,aid,"snorkeling")
    assert before["applied"] is False
    with pytest.raises(ValueError,match="reviewed"):
        confirm_activity(ws,aid,"snorkeling",apply=True)
    approved=confirm_activity(ws,aid,"snorkeling",reviewed=True,apply=True)
    assert approved["applied"]
    assert approved["videoSourceModified"] is False
    from pathlib import Path
    assert Path(approved["backup"]).is_file()
    assert Path(approved["audit"]).is_file()
    assert file.read_bytes()==raw
    with connect(ws) as db:
        assert db.execute("SELECT activity FROM assets WHERE id=?",(aid,)).fetchone()[0]=="snorkeling"
    with pytest.raises(ValueError,match="different activity"):
        confirm_activity(ws,aid,"bike",reviewed=True,apply=True)


def test_gallery_escapes_untrusted_filenames_and_review_is_html_only():
    candidate={
        "assetId":"f"*64,"basename":'<img src=x onerror=alert(1)>.MP4',
        "images":["thumbnails/safe.jpg"],"reason":"no_decisive_first_pass_votes",
        "decision":{"status":"underwater_candidate_needs_review",
                    "validFrames":5,"votes":{"underwater":5}}}
    html=_review_html([candidate])
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "catalog changes were made" in html


def test_cli_refine_and_confirm_dry_run(setup,monkeypatch,capsys):
    ws,aid,file,raw,first,decoder=setup
    monkeypatch.setattr("adventure_intelligence.activity_refinement.RefineModel",
                        RefineUnderwater)
    monkeypatch.setattr("adventure_intelligence.activity_refinement.extract_frame_png",
                        decoder)
    output=ws/"reports"/"cli-refine"
    assert main(["--workspace",str(ws),"activity-refine",
                 "--input-dir",str(first),"--output-dir",str(output),
                 "--limit","1"])==0
    printed=json.loads(capsys.readouterr().out)
    assert printed["processedThisRun"]==1
    assert printed["catalogActivityLabelsChanged"] is False
    assert main(["--workspace",str(ws),"activity-confirm",
                 "--asset",aid,"--activity","snorkeling"])==0
    data=json.loads(capsys.readouterr().out)
    assert data["applied"] is False



def test_explicit_asset_adaptive_plan_after_reviewed_confirmation(setup):
    from adventure_intelligence.adaptive_sampling import build_plan,validate_plan
    ws,aid,file,raw,first,decoder=setup
    with pytest.raises(ValueError,match="missing, invalid-duration, or mismatched"):
        build_plan(ws,asset_ids=[aid],activity="snorkeling",limit=1,max_frames=8)
    confirm_activity(ws,aid,"snorkeling",reviewed=True,apply=True)
    plan=build_plan(ws,asset_ids=[aid],activity="snorkeling",limit=1,
                    max_frames=8,coverage_seconds=30,interest_seconds=2)
    assert len(plan["assets"])==1
    assert plan["assets"][0]["assetId"]==aid
    assert plan["parameters"]["selectedAssetIds"]==[aid]
    assert set(validate_plan(plan))=={aid}
    assert file.read_bytes()==raw
    with pytest.raises(ValueError,match="limit must be positive"):
        build_plan(ws,asset_ids=[aid],activity="snorkeling",limit=0,max_frames=8)

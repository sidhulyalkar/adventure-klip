"""Activity classification qualification using synthetic cache and mocked image model."""
from io import BytesIO
import json
from pathlib import Path

import pytest

from adventure_intelligence.activity_intelligence import (
    _blue_cue, _decision, _frame_scores, _sample_times, _review_html,
    run_activity_scan,
)
from adventure_intelligence.activity_apply import apply_activity_proposals
from adventure_intelligence.catalog import connect
from adventure_intelligence.cli import main
from adventure_intelligence.indexing import index_folder


class FakeActivityModel:
    def __init__(self, *, allow_model_download=False):
        self.revision="fake-pinned-siglip2-revision"
        self.processor_config={"mock_image_processor":"v1"}
        self.text_vectors={
            "snorkeling":[[1.,0.,0.,0.]],
            "ski":[[0.,1.,0.,0.]],
            "bike":[[0.,0.,1.,0.]],
            "other":[[0.,0.,0.,1.]],
        }
    def image_vectors(self, images):
        return [[1.,0.,0.,0.] for _ in images]


@pytest.fixture
def videos(tmp_path, monkeypatch):
    camera=tmp_path/"camera"
    camera.mkdir()
    originals=[]
    for i in range(2):
        video=camera/f"DJI_FAKE_{i}.MP4"
        content=(f"intact-video-{i}".encode())*4096
        video.write_bytes(content)
        originals.append((video,content))
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda path:{"duration":120.,"audio":False,"capture_time":None})
    ws=tmp_path/"workspace"
    ids=index_folder(camera,ws,activity="unknown")["asset_ids"]
    return ws,ids,originals


def _png():
    from PIL import Image
    img=Image.new("RGB",(64,48),(3,95,157))
    out=BytesIO()
    img.save(out,"PNG")
    return out.getvalue()


def test_blue_water_is_only_cue_and_other_scene_remains_other():
    blue=_blue_cue(_png())
    assert blue["bluePixelFraction"] > .9
    assert blue["lowerHalfBlueFraction"] > .9
    all_other=[{"tMs":i*20000,"scores":{"other":1.,"snorkeling":0.},
                "vote":"other","margin":.12} for i in range(5)]
    result=_decision(all_other,120000)
    assert result["status"]=="likely_other"
    assert result["proposedActivity"]=="other"
    assert result["readyForAutoApply"] is False


def test_temporal_support_prevents_short_or_conflicting_auto_labels():
    samples=[{"tMs":i*18000,"scores":{"snorkeling":.5},
              "vote":"snorkeling","margin":.07} for i in range(5)]
    assert _decision(samples,90000)["status"]=="strong_candidate"
    assert _decision(samples,20000)["status"]=="ambiguous_needs_review"
    samples[1]["vote"]="bike"
    assert _decision(samples,90000)["status"]=="ambiguous_needs_review"
    assert _sample_times(90000,5)==sorted(set(_sample_times(90000,5)))


def test_frame_similarity_ranking_not_fake_probabilities():
    tv={"snorkeling":[[1.,0.]],"ski":[[0.,1.]],
        "bike":[[-1.,0.]],"other":[[0.,-1.]]}
    scores=_frame_scores(tv,[[1.,0.]])
    assert scores[0]["vote"]=="snorkeling"
    assert scores[0]["margin"]==1.
    with pytest.raises(ValueError,match="dimensions"):
        _frame_scores(tv,[[1.,0.,0.]])


def test_resumable_scan_makes_no_catalog_changes_and_writes_review(videos,monkeypatch):
    ws,ids,originals=videos
    monkeypatch.setattr("adventure_intelligence.activity_intelligence.SiglipActivityClassifier",
                        FakeActivityModel)
    calls=[]
    def fake_decode(path,t,width=384):
        calls.append((str(path),t))
        return _png(),{"decodedPtsMs":t+20,"timestampBasis":"decoded_frame_pts"}
    monkeypatch.setattr("adventure_intelligence.activity_intelligence.extract_frame_png",
                        fake_decode)
    dest=ws/"reports"/"activity-pilot"
    first=run_activity_scan(ws,dest,frames=5)
    assert first["processedThisRun"]==2
    assert first["totalCompleted"]==2
    assert len(calls)==10
    records=[json.loads(s)["record"] for s in (dest/"records.jsonl").read_text().splitlines()]
    assert {r["assetId"] for r in records}==set(ids)
    assert all(r["decision"]["status"]=="strong_candidate" for r in records)
    assert all(r["sourceVerifiedSha256"]==r["assetId"] for r in records)
    assert all(r["thumbnailBlueCue"]["interpretation"]=="color_only_not_underwater_evidence"
               for r in records)
    assert (dest/"review.html").is_file()
    assert (dest/"thumbnails"/f"{ids[0]}.jpg").is_file()
    with connect(ws) as db:
        assert {r[0] for r in db.execute("SELECT DISTINCT activity FROM assets")}=={"unknown"}
    again=run_activity_scan(ws,dest,frames=5)
    assert again["processedThisRun"]==0
    assert len(calls)==10
    assert all(video.read_bytes()==raw for video,raw in originals)


def test_apply_is_explicit_bounded_and_online_backup_exists(videos,monkeypatch):
    ws,ids,originals=videos
    monkeypatch.setattr("adventure_intelligence.activity_intelligence.SiglipActivityClassifier",
                        FakeActivityModel)
    monkeypatch.setattr("adventure_intelligence.activity_intelligence.extract_frame_png",
                        lambda path,t,width=384:(_png(),{
                            "decodedPtsMs":t+15,"timestampBasis":"decoded_frame_pts"}))
    output=ws/"reports"/"activity-scan"
    run_activity_scan(ws,output)
    preview=apply_activity_proposals(ws,output,activity="snorkeling",max_assets=1)
    assert preview["dryRun"]
    assert len(preview["selected"])==1
    with connect(ws) as db:
        assert db.execute("SELECT COUNT(*) FROM assets WHERE activity='unknown'").fetchone()[0]==2
    confirmed=apply_activity_proposals(ws,output,activity="snorkeling",
                                       max_assets=1,apply=True)
    assert confirmed["dryRun"] is False
    assert len(confirmed["applied"])==1
    assert Path(confirmed["backup"]).is_file()
    assert Path(confirmed["audit"]).is_file()
    with connect(ws) as db:
        assert db.execute("SELECT COUNT(*) FROM assets WHERE activity='snorkeling'").fetchone()[0]==1
        assert db.execute("SELECT COUNT(*) FROM assets WHERE activity='unknown'").fetchone()[0]==1
    assert all(video.read_bytes()==raw for video,raw in originals)
    again=apply_activity_proposals(ws,output,activity="snorkeling",
                                   max_assets=1,apply=True)
    assert len(again["applied"])==1  # Second distinct eligible candidate.


def test_failed_source_identity_never_applied(videos,monkeypatch):
    ws,ids,originals=videos
    monkeypatch.setattr("adventure_intelligence.activity_intelligence.SiglipActivityClassifier",
                        FakeActivityModel)
    monkeypatch.setattr("adventure_intelligence.activity_intelligence.extract_frame_png",
                        lambda path,t,width=384:(_png(),{
                            "decodedPtsMs":t+15,"timestampBasis":"decoded_frame_pts"}))
    output=ws/"reports"/"report"
    run_activity_scan(ws,output,limit=1)
    target,_=originals[0]
    target.write_bytes(b"changed-source-after-classification")
    outcome=apply_activity_proposals(ws,output,max_assets=1,apply=True)
    assert outcome["applied"]==[]
    assert outcome["skipped"]
    with connect(ws) as db:
        assert db.execute("SELECT COUNT(*) FROM assets WHERE activity='snorkeling'").fetchone()[0]==0


def test_html_untrusted_basename_is_escaped():
    obs={"assetId":"a"*64,"basename":"<script>alert(7)</script>.MP4",
         "decision":{"status":"strong_candidate","proposedActivity":"snorkeling",
                     "medianWinningMargin":.7,"winnerVotes":5,"validSamples":5}}
    html=_review_html([obs])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_cli_smoke_offline(videos,monkeypatch,capsys):
    ws,ids,originals=videos
    monkeypatch.setattr("adventure_intelligence.activity_intelligence.SiglipActivityClassifier",
                        FakeActivityModel)
    monkeypatch.setattr("adventure_intelligence.activity_intelligence.extract_frame_png",
                        lambda path,t,width=384:(_png(),{
                            "decodedPtsMs":t+15,"timestampBasis":"decoded_frame_pts"}))
    folder=ws/"reports"/"cli-scan"
    assert main(["--workspace",str(ws),"activity-scan",
                 "--output-dir",str(folder),"--limit","1"])==0
    result=json.loads(capsys.readouterr().out)
    assert result["processedThisRun"]==1
    assert main(["--workspace",str(ws),"activity-apply","--report-dir",str(folder),
                 "--max-assets","1"])==0
    result=json.loads(capsys.readouterr().out)
    assert result["dryRun"] is True
    assert main(["--workspace",str(ws),"activity-apply","--report-dir",str(folder),
                 "--max-assets","1","--apply"])==0
    result=json.loads(capsys.readouterr().out)
    assert len(result["applied"])==1



def test_resumed_model_revision_mismatch_refused(videos,monkeypatch):
    ws,ids,originals=videos
    monkeypatch.setattr("adventure_intelligence.activity_intelligence.SiglipActivityClassifier",
                        FakeActivityModel)
    monkeypatch.setattr("adventure_intelligence.activity_intelligence.extract_frame_png",
                        lambda path,t,width=384:(_png(),{
                            "decodedPtsMs":t+12,"timestampBasis":"decoded_frame_pts"}))
    report=ws/"reports"/"revision-locked"
    first=run_activity_scan(ws,report,frames=5,limit=1)
    assert first["processedThisRun"]==1

    class ChangedModel(FakeActivityModel):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.revision="different-unsafe-model-revision"

    monkeypatch.setattr("adventure_intelligence.activity_intelligence.SiglipActivityClassifier",
                        ChangedModel)
    with pytest.raises(ValueError,match="different model revision"):
        run_activity_scan(ws,report,frames=5,limit=1)
    assert len((report/"records.jsonl").read_text().splitlines())==1


def test_tampered_model_provenance_not_accepted_for_catalog_promotion(
        videos,monkeypatch):
    ws,ids,originals=videos
    monkeypatch.setattr("adventure_intelligence.activity_intelligence.SiglipActivityClassifier",
                        FakeActivityModel)
    monkeypatch.setattr("adventure_intelligence.activity_intelligence.extract_frame_png",
                        lambda path,t,width=384:(_png(),{
                            "decodedPtsMs":t+12,"timestampBasis":"decoded_frame_pts"}))
    report=ws/"reports"/"tamper-model-lock"
    run_activity_scan(ws,report,limit=1)
    file=report/"model-lock.json"
    data=json.loads(file.read_text())
    data["processorHash"]="tampered"
    file.write_text(json.dumps(data))
    with pytest.raises(ValueError,match="incompatible model revisions"):
        apply_activity_proposals(ws,report,apply=True)
    with connect(ws) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM assets WHERE activity='unknown'").fetchone()[0]==2

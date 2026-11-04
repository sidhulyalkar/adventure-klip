"""Real frozen-plan review benchmarks: positive hypotheses, controls and missing truth."""
from pathlib import Path
import json

import pytest

from adventure_intelligence.archive_review import (
    _choose_windows,seed_archive_reviews,CRITERIA,
)
from adventure_intelligence.archive_pilot import run_archive_pilot
from adventure_intelligence.archive_status import inspect_archive_campaign
from adventure_intelligence.catalog import connect
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.cli import main


@pytest.fixture
def campaign(tmp_path,monkeypatch):
    media=tmp_path/"camera"
    media.mkdir()
    originals={}
    for i in range(1,9):
        path=media/f"DJI_{i:04d}_D.MP4"
        raw=(f"immutable-source-{i:03d}".encode())*400
        path.write_bytes(raw)
        originals[path.name]=raw
    root=tmp_path/"workspace"
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda p:{"duration":96.,"audio":False,
                                  "capture_time":None})
    index_folder(media,root,activity="unknown")
    with connect(root) as db:
        rows=list(db.execute("SELECT id FROM assets ORDER BY stored_path"))
        activities=["snorkeling","snorkeling","ski","ski","bike","bike",
                    "unknown","unknown"]
        for row,label in zip(rows,activities):
            db.execute("UPDATE assets SET activity=? WHERE id=?",(label,row["id"]))
        db.commit()
    pilot=run_archive_pilot(root,batch_size=8,max_frames=16,step_seconds=12)
    return root,media,originals,pilot


def test_review_seed_is_read_only_deterministic_and_balanced(campaign,capsys):
    root,media,originals,pilot=campaign
    folder=root/"reports"/"review-seed-01"
    code=main(["--workspace",str(root),"archive-review",
               "--output-dir",str(folder)])
    assert code==0
    response=json.loads(capsys.readouterr().out)
    assert response["assets"]==8
    assert response["sourceFramesDecoded"]==0
    assert response["modelInferenceCalls"]==0
    assignments=json.loads((folder/"assignments.json").read_text())
    assert assignments["sourceAssetCount"]==8
    assert assignments["reviewIntervalCount"]>8
    assert assignments["selectionGroups"]["snorkeling"]>=2
    assert assignments["selectionGroups"]["ski"]>=2
    assert assignments["selectionGroups"]["bike"]>=2
    assert assignments["selectionGroups"]["unresolved"]>=2
    assert set(assignments["selectionRoles"])=={
        "candidate_hypothesis","control_systematic"}
    for row in assignments["assignments"]:
        assert row["truthStatus"]=="unreviewed"
        assert 0<=row["startMs"]<row["endMs"]<=96000
        assert row["observedEvent"] is None
        assert row["sourceFileUrl"].startswith("file://")
        assert row["analysisCoverage"]=="not_started"
    review=json.loads((folder/"review-template.json").read_text())
    assert len(review)==assignments["reviewIntervalCount"]
    assert all(set(r["ratings"])==set(CRITERIA) for r in review)
    assert all(all(v is None for v in r["ratings"].values()) for r in review)
    assert (folder/"index.html").is_file()
    assert {p.name:p.read_bytes() for p in media.iterdir()}==originals
    assert main(["--workspace",str(root),"archive-review",
                 "--output-dir",str(folder)])==2
    capsys.readouterr()


def test_review_source_hypothesis_not_equated_to_truth(campaign):
    root,media,originals,pilot=campaign
    folder=root/"reports"/"review-seed-02"
    result=seed_archive_reviews(root,root/"reports"/"archive-pilot-v1",folder,
                                max_assets=2)
    rows=json.loads((folder/"assignments.json").read_text())["assignments"]
    assert result["assets"]==2
    assert all(r["truthStatus"]=="unreviewed" for r in rows)
    assert all(r["notGroundTruth"] for r in rows)
    assert {x["role"] for x in rows}=={"candidate_hypothesis","control_systematic"}
    assert all(not r["speciesVerified"] and not r["techniqueVerified"] for r in rows)


def test_short_recording_never_makes_fake_independent_control():
    item={"durationMs":8500,"frames":[
        {"tMs":1000,"reasons":["coverage_probe"]},
        {"tMs":5000,"reasons":["motion_change_unverified"]}]}
    ranges=_choose_windows(item,half_ms=7000)
    assert len(ranges)==1
    assert ranges[0]["startMs"]==0
    assert ranges[0]["endMs"]<=8500


def test_candidate_uses_interest_reasons_not_brightness_or_sport():
    item={"durationMs":120000,"frames":[
        {"tMs":0,"reasons":["coverage_probe"]},
        {"tMs":30000,"reasons":["motion_change_unverified"]},
        {"tMs":60000,"reasons":["coverage_probe"]},
        {"tMs":90000,"reasons":["coverage_probe"]}]}
    ranges=_choose_windows(item,half_ms=7000)
    assert ranges[0]["anchorMs"]==30000
    assert ranges[0]["role"]=="candidate_hypothesis"
    assert "motion_change_unverified" in ranges[0]["schedulerReasons"]
    assert ranges[1]["role"]=="control_systematic"
    assert ranges[1]["anchorMs"]==90000


def test_invalid_output_fails_before_source_processing(campaign):
    root,media,originals,pilot=campaign
    with pytest.raises(ValueError,match="new output"):
        seed_archive_reviews(root,root/"reports"/"archive-pilot-v1",root/"outside")
    with pytest.raises(ValueError,match="max-assets"):
        seed_archive_reviews(root,root/"reports"/"archive-pilot-v1",
                             root/"reports"/"test2",max_assets=0)
    assert {p.name:p.read_bytes() for p in media.iterdir()}==originals

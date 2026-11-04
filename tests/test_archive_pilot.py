"""Archive pilot orchestration: read-only audit, stable batches, opt-in bounded run."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from adventure_intelligence.archive_pilot import (
    inventory, _first_pass_proposals, _order_assets, run_archive_pilot,
)
from adventure_intelligence.activity_intelligence import _hash
from adventure_intelligence.catalog import connect
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.adaptive_sampling import validate_plan
from adventure_intelligence.cli import main


@pytest.fixture
def archives(tmp_path,monkeypatch):
    camera=tmp_path/"DJI_SSD"
    camera.mkdir()
    # Files are small synthetic originals. Never decode in audit/plan mode.
    paths=[]
    for index in range(1,14):
        p=camera/f"DJI_20241227_{index:04d}_D.MP4"
        p.write_bytes((f"original-{index:03d}-".encode())*300)
        paths.append(p)
    root=tmp_path/"current"
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda p:{"duration":50.65,"audio":False,
                                  "capture_time":None})
    index_folder(camera,root,activity="unknown")
    with connect(root) as db:
        assets=list(db.execute("SELECT id,stored_path FROM assets ORDER BY stored_path"))
        for i,asset in enumerate(assets):
            if i<2:
                activity="snorkeling"
            elif i<4:
                activity="ski"
            elif i<6:
                activity="bike"
            else:
                activity="unknown"
            db.execute("UPDATE assets SET activity=? WHERE id=?",(activity,asset["id"]))
        db.commit()
    source_paths={Path(asset["stored_path"]).name:asset["id"] for asset in assets}
    legacy=tmp_path/"legacy"
    index_folder(camera,legacy,activity="unknown")
    return root,legacy,camera,source_paths


def test_read_only_inventory_has_full_but_nonverified_summary(archives):
    root,legacy,camera,ids=archives
    report,eligible=inventory(root)
    assert report["assetCount"]==13
    assert report["accessibleValidAssets"]==13
    assert report["activityCounts"]=={
        "bike":2,"ski":2,"snorkeling":2,"unknown":7}
    assert report["sourceFramesDecoded"]==0
    assert report["sourceByteHashingPerformed"] is False
    assert report["catalogModified"] is False
    assert len(eligible)==13
    with pytest.raises(ValueError,match="Missing Klip catalog"):
        inventory(root.parent/"not-a-workspace")


def test_freezes_signed_roster_and_plan_across_subsequent_cache_changes(archives):
    root,legacy,camera,ids=archives
    first=run_archive_pilot(root,legacy=legacy,batch_size=8)
    assert first["status"]=="planned"
    assert first["batchSize"]==8
    assert first["backend"] is None
    assert first["sourceMediaModified"] is False
    assert first["legacyComparison"]["sharedSourceHashes"]==13
    planfile=Path(first["plan"])
    manifest=Path(first["manifest"])
    roster=Path(first["frozenRoster"])
    assert planfile.is_file() and manifest.is_file() and roster.is_file()
    initial_plan=planfile.read_bytes()
    schedule=validate_plan(json.loads(initial_plan))
    assert len(schedule)==8
    assert len(set(schedule))==8
    assert first["scheduledFrames"]<=8*24
    groups=json.loads(manifest.read_text())["selectionGroups"]
    assert all(groups.get(k,0)>0 for k in ("snorkeling","ski","bike","unresolved"))
    # New semantic cache should never reorder the already-frozen campaign.
    chosen=list(schedule)
    with connect(root) as db:
        for aid in chosen[:2]:
            db.execute("INSERT INTO analyses(asset_id,stage,version,status) "
                       "VALUES (?,?,?,?)",(aid,"baseline","synthetic","completed"))
        db.commit()
    again=run_archive_pilot(root,legacy=legacy,batch_size=8)
    assert again["plan"]==first["plan"]
    assert planfile.read_bytes()==initial_plan
    next_batch=run_archive_pilot(root,batch=2,batch_size=8)
    later=validate_plan(json.loads(Path(next_batch["plan"]).read_text()))
    assert len(later)==5
    assert not set(schedule)&set(later)
    assert run_archive_pilot(root,batch=3,batch_size=8)["status"]=="no_more_eligible_assets"
    assert all(path.read_bytes().startswith(b"original-") for path in camera.iterdir())


def test_one_command_runs_only_opt_in_histogram_and_resumes(archives,monkeypatch,capsys):
    root,legacy,camera,ids=archives
    calls=[]
    def fake_generate(workspace,**kwargs):
        calls.append(kwargs)
        assert kwargs["backend"]=="histogram"
        assert kwargs["decode_mode"]=="batched"
        assert kwargs["schedule_id"]
        assert set(kwargs["sample_schedule"])==kwargs["asset_ids"]
        return {"processed":8,"cached":0,"errors":[]}
    monkeypatch.setattr("adventure_intelligence.archive_pilot.generate_semantics",
                        fake_generate)
    args=["--workspace",str(root),"archive-pilot",
          "--legacy",str(legacy)]
    assert main(args)==0
    prep=json.loads(capsys.readouterr().out)
    assert prep["status"]=="planned"
    assert calls==[]
    assert main(args+["--run"])==0
    completed=json.loads(capsys.readouterr().out)
    assert completed["status"]=="completed"
    assert completed["run"]["processed"]==8
    assert len(calls)==1
    assert main(args+["--run"])==0
    assert len(calls)==2
    assert Path(completed["plan"]).read_bytes()==Path(prep["plan"]).read_bytes()


def test_failed_histogram_run_returns_nonzero_and_retains_plan(archives,monkeypatch,capsys):
    root,legacy,camera,ids=archives
    monkeypatch.setattr("adventure_intelligence.archive_pilot.generate_semantics",
        lambda *a,**kw:{"processed":4,"cached":0,
                         "errors":[{"error":"synthetic final frame failure"}]})
    assert main(["--workspace",str(root),"archive-pilot","--run"])==1
    result=json.loads(capsys.readouterr().out)
    assert result["status"]=="partial_failure"
    assert Path(result["plan"]).is_file()
    assert Path(result["frozenRoster"]).is_file()


def test_invalid_batch_or_outside_workspace_fails_before_media_processing(archives,tmp_path):
    root,legacy,camera,ids=archives
    with pytest.raises(ValueError,match="batch-size"):
        run_archive_pilot(root,batch_size=17)
    with pytest.raises(ValueError,match="Output directory"):
        run_archive_pilot(root,output_dir=tmp_path/"bad")
    first=run_archive_pilot(root,batch_size=8)
    with pytest.raises(ValueError,match="Frozen archive campaign"):
        run_archive_pilot(root,batch_size=6)
    first_id=json.loads(Path(first["manifest"]).read_text())["selection"][0]["assetId"]
    with connect(root) as db:
        row=db.execute("SELECT stored_path FROM assets WHERE id=?",(first_id,)).fetchone()
        source=Path(row["stored_path"])
    source.rename(source.with_suffix(".elsewhere"))
    with pytest.raises(ValueError,match="inaccessible or changed"):
        run_archive_pilot(root,batch_size=8)


def test_unconfirmed_activity_proposals_are_selection_hints_not_labels(archives):
    root,legacy,camera,ids=archives
    report=root/"reports"/"activity-overnight-v1"
    report.mkdir(parents=True)
    cfg={"frames":5,"version":"test"}
    (report/"config.json").write_text(json.dumps(cfg))
    raw=[]
    with connect(root) as db:
        unknown=list(db.execute(
            "SELECT id FROM assets WHERE activity='unknown' ORDER BY id"))
    for item in unknown[:4]:
        candidate={
            "assetId":item["id"],"configHash":_hash(cfg),
            "decision":{"proposedActivity":"snorkeling",
                        "status":"ambiguous_needs_review"}}
        raw.append(json.dumps({"record":candidate,"sha256":_hash(candidate)}))
    (report/"records.jsonl").write_text("\n".join(raw)+"\n")
    suggestions=_first_pass_proposals(root)
    assert len(suggestions)==4
    plan=run_archive_pilot(root,batch_size=8)
    saved=json.loads(Path(plan["manifest"]).read_text())
    assert saved["selectionGroups"]["snorkeling"]>=2
    with connect(root) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM assets WHERE activity='unknown'").fetchone()[0]==7
    for aid in suggestions:
        assert suggestions[aid]["catalogConfirmed"] is False



def test_multi_batch_cli_stops_safely_when_roster_exhausted(archives,monkeypatch,capsys):
    root,legacy,camera,ids=archives
    attempts=[]
    def fake_generate(*args,**kwargs):
        attempts.append(kwargs["schedule_id"])
        return {"processed":len(kwargs["asset_ids"]),"cached":0,"errors":[]}
    monkeypatch.setattr("adventure_intelligence.archive_pilot.generate_semantics",
                        fake_generate)
    result=main(["--workspace",str(root),"archive-pilot","--batch-count","4",
                 "--run"])
    assert result==0
    output=json.loads(capsys.readouterr().out)
    assert output["status"]=="completed"
    assert len(output["batches"])==3
    assert [b["status"] for b in output["batches"]]==[
        "completed","completed","no_more_eligible_assets"]
    assert len(attempts)==2
    assert len(set(attempts))==2
    assert output["batches"][0]["batchSize"]==8
    assert output["batches"][1]["batchSize"]==5
    assert main(["--workspace",str(root),"archive-pilot","--batch-count","9"])==2
    assert "--batch-count" in capsys.readouterr().err
    assert main(["--workspace",str(root),"archive-pilot","--batch-count","2",
                 "--output-dir",str(root/"reports"/"other")])==2
    assert "ambiguous" in capsys.readouterr().err

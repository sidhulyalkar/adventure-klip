"""Coverage audit for frozen studies without probing removable camera media."""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import json
import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.cli import main
from adventure_intelligence.archive_discovery_coverage import (
    inspect_discovery_coverage, save_discovery_coverage
)
from adventure_intelligence.social_action_lens import USER_VERSION, FEATURE_VERSION


def fixture_catalog(tmp_path):
    ws=tmp_path/"ws"
    ids=[sha256(f"source-{i}".encode()).hexdigest() for i in range(10)]
    with connect(ws) as db:
        for i,aid in enumerate(ids):
            group=("unknown","ski","bike","snorkeling")[i%4]
            db.execute("INSERT INTO assets(id,sha256,stored_path,size_bytes,suffix,duration,activity)"
                       " VALUES(?,?,?,?,?,?,?)",
                       (aid,aid,f"/Volumes/UNMOUNTED_SD/DCIM/{i:03}.MP4",
                        8000000000,".MP4",None if i==9 else 60.0,group))
        for i in (0,1,3,5):
            db.execute("INSERT INTO episodes(id,asset_id,kind,start_ms,end_ms,score,"
                       "event_ids_json,evidence_json,origin,version)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (f"episode-{i}",ids[i],"active_sequence",1000,30000,.73,
                        "[]","{}","detector","fixture"))
        db.execute("INSERT INTO semantic_jobs(asset_id,config_id,config_json,status)"
                   " VALUES(?,?,?,?)",
                   (ids[4],"cfg",'{"provider":"siglip2"}',"completed"))
        db.execute("INSERT INTO semantic_active(asset_id,provider,config_id)"
                   " VALUES(?,?,?)",(ids[4],"siglip2","cfg"))
        db.execute("INSERT INTO analyses(asset_id,stage,version,status)"
                   " VALUES(?,?,?,?)",(ids[7],"histogram","v1","completed"))
        db.commit()
    study=ws/"reports"/"frozen"/"study.json"
    features=ws/"reports"/"features"/"feature-template.json"
    study.parent.mkdir(parents=True)
    features.parent.mkdir(parents=True)
    ranked=[
        {"sourceAssetId":ids[0],"startMs":1000,"endMs":21000,
         "activityGroup":"unresolved","score":.8},
        {"sourceAssetId":ids[1],"startMs":2000,"endMs":30000,
         "activityGroup":"ski","score":.9},
    ]
    selections=[
        {"assetId":ids[0],"startMs":1000,"endMs":21000,"sport":"other"},
        {"assetId":ids[1],"startMs":2000,"endMs":30000,"sport":"ski"},
    ]
    study.write_text(json.dumps({"version":"archive-value-study-v1",
        "rankedCandidates":ranked,"rankedCandidatesTruncated":0}))
    features.write_text(json.dumps({"version":USER_VERSION,
        "featureVersion":FEATURE_VERSION,"items":selections}))
    return ws,ids,study,features


def test_metadata_only_audit_does_not_read_or_stat_sdcard(tmp_path,monkeypatch):
    ws,ids,study,features=fixture_catalog(tmp_path)
    old_is_file,old_stat,old_resolve=(Path.is_file,Path.stat,Path.resolve)
    def guarded(old):
        def check(self,*args,**kwargs):
            if str(self).startswith("/Volumes/UNMOUNTED_SD"):
                pytest.fail("Read-only audit must not touch original camera media")
            return old(self,*args,**kwargs)
        return check
    monkeypatch.setattr(Path,"is_file",guarded(old_is_file))
    monkeypatch.setattr(Path,"stat",guarded(old_stat))
    monkeypatch.setattr(Path,"resolve",guarded(old_resolve))
    report=inspect_discovery_coverage(ws,study,features,source_budget=8)
    assert report["sourceRosterSize"]==10
    assert report["rankedCandidateSourceCount"]==2
    assert report["selectedPilotWindows"]==2
    assert report["selectedPilotWindowActivity"]=={"other":1,"ski":1}
    assert report["originalStudyMetadataInvalidInCurrentCatalog"]==1
    assert report["discoveryTierCounts"]["metadata_repair"]==1
    assert report["discoveryTierCounts"]["unrepresented_with_completed_semantic_job"]==1
    assert len(report["explorationSourcePlan"])==7
    assert ids[4] in {r["sourceAssetId"] for r in report["explorationSourcePlan"]}
    assert ids[9] not in {r["sourceAssetId"] for r in report["explorationSourcePlan"]}
    assert all(r["sourceMediaReadAuthorized"] is False
               for r in report["explorationSourcePlan"])
    assert report["storageAndEvaluationSafety"]["originalVideoReads"]==0
    assert report["storageAndEvaluationSafety"]["externalDrivePathStatCalls"]==0
    out=save_discovery_coverage(ws,study,features,ws/"reports"/"metadata-only",
                                source_budget=8)
    assert out["unrepresentedSourcesInPlan"]==7
    assert out["sourceVideoReads"]==0
    saved=json.loads(Path(out["coverage"]).read_text())
    assert saved["explorationSourcePlan"]==report["explorationSourcePlan"]
    assert inspect_discovery_coverage(ws,study,features,
                   source_budget=8)["explorationSourcePlan"]==report["explorationSourcePlan"]


def test_wrong_crosswalk_rejected_before_any_write(tmp_path):
    ws,ids,study,features=fixture_catalog(tmp_path)
    bad=json.loads(features.read_text())
    bad["items"][0]["startMs"]=4000
    features.write_text(json.dumps(bad))
    out=ws/"reports"/"no-write"
    with pytest.raises(ValueError,match="absent from frozen ranked study"):
        save_discovery_coverage(ws,study,features,out)
    assert not out.exists()
    bad["items"][0]["startMs"]=1000
    bad["items"][0]["sport"]="bike"
    features.write_text(json.dumps(bad))
    with pytest.raises(ValueError,match="activity differs"):
        save_discovery_coverage(ws,study,features,out)
    assert not out.exists()


def test_cli_source_budget_and_safe_report_paths(tmp_path,capsys):
    ws,ids,study,features=fixture_catalog(tmp_path)
    target=ws/"reports"/"cli"
    assert main(["--workspace",str(ws),"archive-discovery-audit",
                 "--study",str(study),"--features",str(features),
                 "--source-budget","3","--output-dir",str(target)])==0
    summary=json.loads(capsys.readouterr().out)
    assert summary["unrepresentedSourcesInPlan"]==3
    assert summary["externalDrivePathStatCalls"]==0
    assert Path(summary["coverage"]).is_file()
    with pytest.raises(ValueError,match="NEW"):
        save_discovery_coverage(ws,study,features,target)
    with pytest.raises(ValueError,match="budget"):
        inspect_discovery_coverage(ws,study,features,source_budget=201)
    with connect(ws) as db:
        db.execute("UPDATE assets SET stored_path=? WHERE id=?",
                   (str(ws/"reports"/"UNSAFE"/"video.MP4"),ids[0]))
        db.commit()
    with pytest.raises(ValueError,match="media directory"):
        save_discovery_coverage(ws,study,features,ws/"reports"/"UNSAFE"/"new")

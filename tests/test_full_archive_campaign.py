"""Regression qualification for source-safe full archive campaign.

No original SD-card path is accessed in any test. Actual source SHA reads
are exercised only against synthetic, tiny, healthy workspace originals.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence import full_archive_campaign as campaign


@pytest.fixture
def sources(tmp_path):
    root=tmp_path/"workspace"
    originals=root/"originals"
    originals.mkdir(parents=True)
    body=b"synthetic-test-video-identity"*100
    local=originals/"recording.mp4"
    local.write_bytes(body)
    aid=sha256(body).hexdigest()
    external="b"*64
    invalid="c"*64
    with connect(root) as db:
        db.execute(
            "INSERT INTO assets(id,sha256,stored_path,size_bytes,suffix,duration,activity)"
            " VALUES (?,?,?,?,?,?,?)",
            (aid,aid,str(local),len(body),".mp4",60.0,"ski"))
        db.execute(
            "INSERT INTO assets(id,sha256,stored_path,size_bytes,suffix,duration,activity)"
            " VALUES (?,?,?,?,?,?,?)",
            (external,external,"/Volumes/UNMOUNTED_DJI/DCIM/0001.MP4",
             8_600_000_000,".MP4",140.0,"snorkeling"))
        db.execute(
            "INSERT INTO assets(id,sha256,stored_path,size_bytes,suffix,duration,activity)"
            " VALUES (?,?,?,?,?,?,?)",
            (invalid,invalid,str(tmp_path/"unverified"/"uncertain.MP4"),
             2_000_000,".MP4",None,"unknown"))
    study=root/"reports"/"old"/"study.json"
    study.parent.mkdir(parents=True)
    study.write_text(json.dumps({
        "version":"archive-value-study-v1",
        "rankedCandidates":[{"sourceAssetId":aid,"startMs":1000,"endMs":13000}],
    }))
    return root,local,aid,external,study,body


def test_audit_covers_every_asset_without_stat_or_resolve_of_bad_sdcard(sources,monkeypatch):
    root,local,aid,external,study,body=sources
    original_is_file=Path.is_file
    original_stat=Path.stat
    original_resolve=Path.resolve
    def protect(method):
        def check(self,*args,**kwargs):
            if str(self).startswith("/Volumes/UNMOUNTED_DJI"):
                pytest.fail("Catalog audit attempted filesystem access to unmounted camera media")
            return method(self,*args,**kwargs)
        return check
    monkeypatch.setattr(Path,"is_file",protect(original_is_file))
    monkeypatch.setattr(Path,"stat",protect(original_stat))
    monkeypatch.setattr(Path,"resolve",protect(original_resolve))
    output=root/"reports"/"campaign-audit"
    result=campaign.create_campaign_audit(root,output,study=study)
    assert result["catalogAssets"]==3
    assert result["studyRepresentedSources"]==1
    assert result["studyUnrepresentedSources"]==2
    assert result["sourceOriginalReads"]==0
    assert result["readyForFullArchiveClaim"] is False
    audited=json.loads(Path(result["audit"]).read_text())
    assert audited["sourcePathClasses"]["mounted_external_unverified"]==1
    assert audited["sourcePathClasses"]["managed_workspace_original"]==1
    assert audited["catalogQualityGaps"]["missingOrInvalidDuration"]==1
    assert audited["sourcePathsActuallyVerified"]==0
    assert audited["sourcesWithoutExistingEpisodes"]==3
    assert audited["frozenStudyCoverage"]["rankedWindows"]==1
    assert audited["readinessGates"][0]["passed"] is False
    assert [r["sourceAssetSha256"] for r in audited["assets"]].count(aid)==1
    assert len(audited["assets"])==3
    with pytest.raises(ValueError,match="NEW"):
        campaign.create_campaign_audit(root,output,study=study)


def test_explicit_internal_coarse_scan_sha_once_checkpoint_and_resume(sources,monkeypatch):
    root,local,aid,external,study,body=sources
    audit=campaign.create_campaign_audit(root,root/"reports"/"audit")
    monkeypatch.setattr(campaign,"probe",lambda path:{"duration":60.0})
    seen=[]
    def fake_series(path,**kwargs):
        seen.append((path,kwargs["fps"]))
        return [{"t_ms":1000,"change":.2},
                {"t_ms":13000,"change":.7},
                {"t_ms":30000,"change":.4},
                {"t_ms":45000,"change":.5}]
    monkeypatch.setattr(campaign,"motion_series",fake_series)
    out=root/"reports"/"scan"
    status=campaign.scan_campaign(
        root,Path(audit["audit"]),out,max_assets=1,fps=1)
    assert status["status"]=="coarse_scanned_all_eligible_local_sources"
    assert status["eligibleTrustedLocalSourcePaths"]==1
    assert status["totalCatalogSourceCount"]==3
    assert status["eligibleSourceSubsetNotEntireArchive"] is True
    assert status["completedSources"]==1
    assert status["sourceHashPasses"]==1
    assert status["sourceMediaReadsWereExplicitlyAuthorized"] is True
    assert len(seen)==1
    report=json.loads((out/"per-source"/(aid+".json")).read_text())
    assert report["verifiedOriginalSha256"]==aid
    assert report["shaVerifiedDuringThisScan"] is True
    assert report["noActionOutcomePredicted"] is True
    assert any(x["role"]=="uniform_timeline_control"
               for x in report["candidateHypotheses"])
    assert any(x["role"]=="motion_change_hypothesis"
               for x in report["candidateHypotheses"])
    assert all(x["humanApprovalRequired"] if "humanApprovalRequired" in x else
               not x["highlightApprovedByHuman"]
               for x in report["candidateHypotheses"])
    resumed=campaign.scan_campaign(
        root,Path(audit["audit"]),out,max_assets=1,fps=1,resume=True)
    assert resumed["justCompleted"]==0
    assert resumed["sourceHashPasses"]==1
    assert len(seen)==1
    with pytest.raises(ValueError,match="frozen audit"):
        campaign.scan_campaign(root,Path(audit["audit"]),
                               out,fps=2,resume=True)


def test_scan_quarantines_invalid_source_and_explicitly_retries(sources,monkeypatch):
    root,local,aid,external,study,body=sources
    audit=campaign.create_campaign_audit(root,root/"reports"/"audit")
    monkeypatch.setattr(campaign,"probe",lambda path:{"duration":60.0})
    monkeypatch.setattr(campaign,"motion_series",lambda path,**kw:[])
    local.write_bytes(b"same-size-but-wrong-identity" + b"x"*(len(body)-len(b"same-size-but-wrong-identity")))
    out=root/"reports"/"scan"
    first=campaign.scan_campaign(root,Path(audit["audit"]),out,max_assets=1)
    assert first["status"]=="blocked_or_failed"
    assert first["failedSources"]==1
    assert first["completedSources"]==0
    assert not (out/"per-source"/(aid+".json")).exists()
    local.write_bytes(body)
    skipped=campaign.scan_campaign(root,Path(audit["audit"]),out,max_assets=1,resume=True)
    assert skipped["justCompleted"]==0
    retried=campaign.scan_campaign(
        root,Path(audit["audit"]),out,max_assets=1,resume=True,retry_failed=True)
    assert retried["status"]=="coarse_scanned_all_eligible_local_sources"
    assert retried["completedSources"]==1
    assert retried["sourceHashPasses"]==1


def test_no_scan_opens_removable_sources_without_trusted_root(sources,monkeypatch):
    root,local,aid,external,study,body=sources
    audit=campaign.create_campaign_audit(root,root/"reports"/"audit")
    orig_stat=Path.stat
    orig_resolve=Path.resolve
    orig_is_file=Path.is_file
    def protect(method):
        def guarded(self,*a,**kw):
            if str(self).startswith("/Volumes/UNMOUNTED_DJI"):
                pytest.fail("Untrusted SDXC source filesystem was accessed")
            return method(self,*a,**kw)
        return guarded
    monkeypatch.setattr(Path,"stat",protect(orig_stat))
    monkeypatch.setattr(Path,"resolve",protect(orig_resolve))
    monkeypatch.setattr(Path,"is_file",protect(orig_is_file))
    monkeypatch.setattr(campaign,"probe",lambda p:{"duration":60.0})
    monkeypatch.setattr(campaign,"motion_series",lambda p,**kw:[{"t_ms":1000,"change":.3}])
    done=campaign.scan_campaign(
        root,Path(audit["audit"]),root/"reports"/"scan",max_assets=1)
    assert done["completedSources"]==1
    assert done["eligibleTrustedLocalSourcePaths"]==1
    assert done["totalCatalogSourceCount"]==3
    with pytest.raises(ValueError,match="mounted SSD|Mounted external|Mounted SSD"):
        campaign._require_local_path(
            "/Volumes/UNMOUNTED_DJI/DCIM/0001.MP4",root,
            Path("/Volumes/UNMOUNTED_DJI"),False)


def test_coarse_scan_no_local_sources_fails_before_output_creation(sources):
    root,local,aid,external,study,body=sources
    local.unlink()
    # A file missing from the managed workspace gets recorded as blocked,
    # not a false highlighted event.
    audit=campaign.create_campaign_audit(root,root/"reports"/"audit")
    result=campaign.scan_campaign(
        root,Path(audit["audit"]),root/"reports"/"scan",max_assets=1)
    assert result["status"]=="blocked_or_failed"
    assert result["blockedSourcePaths"]==1
    assert result["completedSources"]==0
    assert not (root/"reports"/"scan"/"per-source"/(aid+".json")).exists()


def test_untrusted_broad_root_never_accepted(sources):
    root,local,aid,external,study,body=sources
    with pytest.raises(ValueError,match="specific footage directory"):
        campaign._require_local_path(local,root,Path("/"),False)

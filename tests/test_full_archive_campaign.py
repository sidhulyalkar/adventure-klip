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
    assert retried["sourceHashPasses"]==2  # initial mismatch and explicit retry


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


def test_frozen_audit_refuses_catalog_mutation_until_new_audit(sources):
    root,local,aid,external,study,body=sources
    audit=campaign.create_campaign_audit(root,root/"reports"/"audit")
    with connect(root) as db:
        db.execute("UPDATE assets SET activity='bike' WHERE id=?",(aid,))
    with pytest.raises(ValueError,match="Catalog changed"):
        campaign.scan_campaign(
            root,Path(audit["audit"]),root/"reports"/"scan",max_assets=1)
    assert not (root/"reports"/"scan").exists()


def _scanned_local_campaign(sources,monkeypatch):
    root,local,aid,external,study,body=sources
    audit=campaign.create_campaign_audit(root,root/"reports"/"audit")
    monkeypatch.setattr(campaign,"probe",lambda _:{"duration":60.0})
    monkeypatch.setattr(campaign,"motion_series",lambda path,**kwargs:[
        {"t_ms":1100,"change":.2},
        {"t_ms":16000,"change":.6},
        {"t_ms":30000,"change":.4},
        {"t_ms":46000,"change":.5},
    ])
    scanned=campaign.scan_campaign(
        root,Path(audit["audit"]),root/"reports"/"scan",max_assets=1)
    assert scanned["completedSources"]==1
    return root,local,aid,Path(audit["audit"]),root/"reports"/"scan"


def test_campaign_packet_cached_only_never_opens_originals(sources,monkeypatch):
    from adventure_intelligence.full_archive_review_packet import prepare_campaign_packet
    root,local,aid,audit,scans=_scanned_local_campaign(sources,monkeypatch)
    old_stat=Path.stat
    old_resolve=Path.resolve
    old_is_file=Path.is_file
    def forbid_original(func):
        def wrapped(self,*args,**kwargs):
            if str(self)==str(local) or str(self).startswith("/Volumes/UNMOUNTED_DJI"):
                pytest.fail("Cached-only review packet must not touch original video")
            return func(self,*args,**kwargs)
        return wrapped
    monkeypatch.setattr(Path,"stat",forbid_original(old_stat))
    monkeypatch.setattr(Path,"resolve",forbid_original(old_resolve))
    monkeypatch.setattr(Path,"is_file",forbid_original(old_is_file))
    result=prepare_campaign_packet(
        root,audit,scans,root/"reports"/"packet-only-cache",
        limit=5,per_source=4,prepare_missing=False)
    assert result["status"]=="blocked_no_verified_previews"
    assert result["distinctOriginalHashPasses"]==0
    assert result["newPreviewDerivatives"]==0
    assert result["candidateWindows"]>0
    assert result["verifiedReviewableClips"]==0
    assert result["missingVerifiedPreviews"]==result["candidateWindows"]
    assert (Path(result["directory"])/"index.html").is_file()
    manifest=json.loads(Path(result["manifest"]).read_text())
    assert all(r["rights"]["rightsStatus"]=="pending" for r in manifest["records"])
    assert all(r["event"]["observedOutcome"]=="unknown" for r in manifest["records"])


def test_campaign_packet_generates_several_previews_with_one_source_sha_pass(
        sources,monkeypatch):
    from adventure_intelligence import full_archive_review_packet as packet
    from adventure_intelligence.studio_preview_judge import preview_paths
    root,local,aid,audit,scans=_scanned_local_campaign(sources,monkeypatch)
    made=[]
    def fake_generate(workspace,source,asset,start,end):
        assert source==local.resolve()
        assert asset==aid
        path,receipt=preview_paths(workspace,asset,start,end)
        path.parent.mkdir(parents=True,exist_ok=True)
        payload=(f"synthetic-derived-proxy-{start}-{end}".encode())*5
        path.write_bytes(payload)
        receipt.write_text(json.dumps({
            "version":"studio-preview-local-judge-v1",
            "assetId":asset,"startMs":start,"endMs":end,
            "previewSha256":sha256(payload).hexdigest(),
            "originalSha256Verified":True,
        }))
        made.append((start,end))
        return True,None
    monkeypatch.setattr(packet,"_generate_cached",fake_generate)
    result=packet.prepare_campaign_packet(
        root,audit,scans,root/"reports"/"packet-with-local-previews",
        limit=5,per_source=4,prepare_missing=True,workers=2)
    assert result["status"]=="review_packet_ready"
    assert result["distinctOriginalHashPasses"]==1
    assert result["verifiedReviewableClips"]==len(made)>1
    assert result["newPreviewDerivatives"]==len(made)
    recorded=json.loads(
        (Path(result["directory"])/"selection-report.json").read_text())
    assert recorded["selectionIsBalancedExplorationNotQualityRanking"]
    assert recorded["selectionRoles"].get("uniform_timeline_control",0)>0
    assert recorded["selectionRoles"].get("motion_change_hypothesis",0)>0
    assert recorded["sourceOriginalReads"]==1
    assert recorded["trainingAuthorized"] is False
    assert (Path(result["directory"])/"index.html").is_file()
    manifest=json.loads(Path(result["manifest"]).read_text())
    assert all(x["observerJudgments"]==[] for x in manifest["records"])


def test_campaign_packet_reuses_verified_existing_previews_without_source_reads(
        sources,monkeypatch):
    from adventure_intelligence import full_archive_review_packet as packet
    from adventure_intelligence.studio_preview_judge import preview_paths
    root,local,aid,audit,scans=_scanned_local_campaign(sources,monkeypatch)
    scan=json.loads((scans/"per-source"/(aid+".json")).read_text())
    window=scan["candidateHypotheses"][0]
    a,b=window["startMs"],window["endMs"]
    preview,receipt=preview_paths(root,aid,a,b)
    preview.parent.mkdir(parents=True,exist_ok=True)
    blob=b"verified-preexisting-derived-video"*5
    preview.write_bytes(blob)
    receipt.write_text(json.dumps({
        "version":"studio-preview-local-judge-v1",
        "assetId":aid,"startMs":a,"endMs":b,
        "previewSha256":sha256(blob).hexdigest(),
        "originalSha256Verified":True,
    }))
    def forbid(*args,**kwargs):
        pytest.fail("Packet should not generate from source media")
    monkeypatch.setattr(packet,"_hash_verified",forbid)
    monkeypatch.setattr(packet,"_generate_cached",forbid)
    result=packet.prepare_campaign_packet(
        root,audit,scans,root/"reports"/"packet-reused",limit=5,
        prepare_missing=False)
    assert result["verifiedReviewableClips"]>=1
    assert result["distinctOriginalHashPasses"]==0
    assert result["newPreviewDerivatives"]==0


def test_campaign_packet_refuses_tampered_source_scan_receipt(sources,monkeypatch):
    from adventure_intelligence.full_archive_review_packet import prepare_campaign_packet
    root,local,aid,audit,scans=_scanned_local_campaign(sources,monkeypatch)
    src=scans/"per-source"/(aid+".json")
    data=json.loads(src.read_text())
    data["candidateHypotheses"][0]["role"]="confirmed_ski_trick"
    src.write_text(json.dumps(data))
    with pytest.raises(ValueError,match="report changed"):
        prepare_campaign_packet(
            root,audit,scans,root/"reports"/"must-not-exist",limit=8)
    assert not (root/"reports"/"must-not-exist").exists()


def test_full_campaign_launch_shell_syntax():
    import shutil, subprocess
    shell=shutil.which("bash")
    if not shell:
        pytest.skip("bash unavailable")
    path=Path(__file__).resolve().parents[1]/"scripts"/"klip_full_archive_highlights.sh"
    assert path.is_file()
    check=subprocess.run([shell,"-n",str(path)],capture_output=True,timeout=15)
    assert check.returncode==0,check.stderr.decode("utf-8","replace")
    source=path.read_text()
    assert "ENABLE_ORIGINAL_READS" in source
    assert "PHASE" in source
    assert "highlight-campaign-audit" in source
    assert "highlight-campaign-scan" in source
    assert "--prepare-previews" not in source


def test_zero_copy_reindex_never_rehashes_old_stalled_sdcard(tmp_path,monkeypatch):
    from adventure_intelligence import indexing
    root=tmp_path/"workspace"
    staged=tmp_path/"safe-staged-footage"
    staged.mkdir()
    source=staged/"actual.MP4"
    blob=b"verified-local-test-data"*150
    source.write_bytes(blob)
    aid=sha256(blob).hexdigest()
    with connect(root) as db:
        db.execute(
            "INSERT INTO assets(id,sha256,stored_path,size_bytes,suffix,duration,activity)"
            " VALUES (?,?,?,?,?,?,?)",
            (aid,aid,"/Volumes/UNMOUNTED_DJI/DCIM/missing.MP4",
             len(blob),".MP4",60.0,"unknown"))
    previous=indexing.verified_digest
    touched=[]
    def only_safe(path,*args,**kwargs):
        touched.append(str(path))
        if str(path).startswith("/Volumes/"):
            pytest.fail("Indexer must not rehash an old removable-media catalog path")
        return previous(path,*args,**kwargs)
    monkeypatch.setattr(indexing,"verified_digest",only_safe)
    result=indexing.index_folder(staged,root)
    assert result["duplicates"]==1
    assert result["errors"]==[]
    assert touched==[str(source)]
    with connect(root) as db:
        stored=db.execute("SELECT stored_path FROM assets WHERE id=?",
                          (aid,)).fetchone()[0]
    assert stored==str(source)
    assert not any(x.startswith("/Volumes") for x in touched)


def test_zero_eligible_local_ollama_jobs_explicitly_report_blocker(tmp_path,monkeypatch):
    from adventure_intelligence import cached_secondlook_batches as second
    root=tmp_path/"workspace"
    assignments=root/"reports"/"frozen-assignments.json"
    assignments.parent.mkdir(parents=True)
    with connect(root):
        pass
    assignments.write_text(json.dumps({"version":"synthetic"}))
    calls=[]
    def no_work(workspace,path,*,model,limit,run,exclude_review_ids):
        calls.append(True)
        return {"selected":0,"eligibleUnreviewed":0,"items":[],
                "missingVerifiedPreviews":20,"awaitingAdaptiveV2":3,
                "firstPassNotFlaggedForInvestigation":0,
                "newLocalModelCalls":0,"cachedSecondLooksReused":0,
                "failed":0}
    monkeypatch.setattr(second,"inspect_temporal_candidates",no_work)
    out=root/"reports"/"zero-input-result"
    result=second.run_cached_secondlook_batches(
        root,assignments,out,batches=15)
    assert len(calls)==1
    assert result["status"]=="blocked_no_eligible_model_inputs"
    assert result["zeroModelCallsNotScientificCompletion"] is True
    assert result["inputGate"]["missingVerifiedPreviews"]==20
    assert result["inputGate"]["awaitingAdaptiveV2"]==3
    assert result["newLocalModelOutputs"]==0
    assert result["sourceVideoReads"]==0

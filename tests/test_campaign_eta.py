"""The scan ETA is metadata-only and never reads a source video."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT=Path(__file__).resolve().parents[1]/"scripts"/"klip_campaign_eta.py"
spec=importlib.util.spec_from_file_location("klip_campaign_eta",SCRIPT)
eta=importlib.util.module_from_spec(spec)
spec.loader.exec_module(eta)


def campaign(tmp_path, n_done=3, n_total=5, with_timing=True):
    root=tmp_path/"workspace"/"reports"/"campaign"
    (root/"audit").mkdir(parents=True)
    (root/"coarse-scan"/"per-source").mkdir(parents=True)
    audit={"catalogAssetCount":n_total,"assets":[]}
    state={"assets":{}}
    for i in range(n_total):
        aid=f"{i+1:064x}"
        audit["assets"].append({
            "sourceAssetSha256":aid,
            "sourceBytesFromCatalog":100*1048576,
            "durationMsFromCatalog":60000,
        })
        if i<n_done:
            record={
                "sourceAssetSha256":aid,
                "verifiedOriginalSha256":aid,
                "shaVerifiedDuringThisScan":True,
                "originalSizeBytes":100*1048576,
                "sourceDurationMs":60000,
                "candidateHypotheses":[],
            }
            if with_timing:
                record["measuredSourceShaSeconds"]=10.0
                record["measuredDecoderWallSeconds"]=20.0
            raw=json.dumps(record).encode()
            (root/"coarse-scan"/"per-source"/f"{aid}.json").write_bytes(raw)
            state["assets"][aid]={
                "status":"coarse_completed",
                "reportSha256":sha256(raw).hexdigest(),
                "attemptWallSeconds":33.0,
            }
    audit_bytes=json.dumps(audit).encode()
    (root/"audit"/"campaign-audit.json").write_bytes(audit_bytes)
    state["campaignAuditSha256"]=sha256(audit_bytes).hexdigest()
    (root/"coarse-scan"/"scan-state.json").write_text(json.dumps(state))
    return root, audit, state


def test_uses_per_asset_measured_sha_and_decode_rates(tmp_path):
    root,_,_=campaign(tmp_path)
    report=eta.campaign_eta(root,now=datetime(2026,11,7,12,0,tzinfo=timezone.utc))
    assert report["catalog"]==5
    assert report["completed"]==3
    assert report["pending"]==2
    assert report["attemptedPct"]==60.0
    assert report["remainingSecondsEstimate"]==66
    assert report["remainingSecondsFast"]==66
    assert report["remainingSecondsSlow"]==66
    assert report["etaLocal"]=="2026-11-07T12:01+00:00"
    assert report["sourceMediaRead"] is False
    assert "completed 3/5" in eta.format_progress(report)


def test_first_unmeasured_sources_do_not_invent_eta(tmp_path):
    root,_,_=campaign(tmp_path,n_done=2)
    r=eta.campaign_eta(root)
    assert r["remainingSecondsEstimate"] is None
    assert r["measuredSources"]==2
    assert "ETA unavailable" in eta.format_progress(r)


def test_missing_timings_are_not_guessed(tmp_path):
    root,_,_=campaign(tmp_path,with_timing=False)
    r=eta.campaign_eta(root)
    assert r["remainingSecondsEstimate"] is None
    assert r["completed"]==3


def test_completed_campaign_has_zero_pending_even_if_time_unmeasured(tmp_path):
    root,_,_=campaign(tmp_path,n_done=5,n_total=5,with_timing=False)
    r=eta.campaign_eta(root)
    assert r["pending"]==0
    assert r["remainingSecondsEstimate"]==0


def test_failed_sources_are_not_predicted_as_automatic_retries(tmp_path):
    root,audit,state=campaign(tmp_path)
    aid=audit["assets"][3]["sourceAssetSha256"]
    state["assets"][aid]={"status":"failed","attemptWallSeconds":2.0}
    (root/"coarse-scan"/"scan-state.json").write_text(json.dumps(state))
    r=eta.campaign_eta(root)
    assert r["pending"]==1
    assert r["failed"]==1
    assert r["remainingSecondsEstimate"]==33
    assert "failed 1" in eta.format_progress(r)


def test_corrupted_receipt_is_not_counted_as_verified(tmp_path):
    root,audit,_=campaign(tmp_path)
    aid=audit["assets"][0]["sourceAssetSha256"]
    path=root/"coarse-scan"/"per-source"/f"{aid}.json"
    path.write_text('{"bad":"tampered"}')
    r=eta.campaign_eta(root)
    assert r["completed"]==2
    assert r["invalidReceipts"]==1
    assert r["remainingSecondsEstimate"] is None


def test_symlinked_source_report_is_rejected_without_opening_link(tmp_path):
    root,audit,_=campaign(tmp_path)
    aid=audit["assets"][0]["sourceAssetSha256"]
    path=root/"coarse-scan"/"per-source"/f"{aid}.json"
    path.unlink()
    path.symlink_to("/Volumes/DO_NOT_OPEN/DCIM/untrusted.MP4")
    r=eta.campaign_eta(root)
    assert r["invalidReceipts"]==1


def test_mismatched_frozen_audit_rejects_eta(tmp_path):
    root,audit,_=campaign(tmp_path)
    audit["catalogAssetCount"]=999
    (root/"audit"/"campaign-audit.json").write_text(json.dumps(audit))
    with pytest.raises(ValueError,match="frozen campaign audit"):
        eta.campaign_eta(root)


def test_progress_does_not_look_at_original_path(tmp_path):
    root,audit,_=campaign(tmp_path)
    audit_path=root/"audit"/"campaign-audit.json"
    data=json.loads(audit_path.read_text())
    for item in data["assets"]:
        item["storedPathAsCatalogued"]="/Volumes/DO_NOT_OPEN/DCIM/clip.MP4"
    raw=json.dumps(data).encode()
    audit_path.write_bytes(raw)
    state_path=root/"coarse-scan"/"scan-state.json"
    state=json.loads(state_path.read_text())
    state["campaignAuditSha256"]=sha256(raw).hexdigest()
    state_path.write_text(json.dumps(state))
    r=eta.campaign_eta(root)
    assert r["completed"]==3
    assert r["sourceMediaRead"] is False

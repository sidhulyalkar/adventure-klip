"""Regression gates for the packet-only metadata preflight."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import pytest

from adventure_intelligence.full_archive_campaign import AUDIT_VERSION, SCAN_VERSION

SCRIPT=Path(__file__).resolve().parents[1]/"scripts"/"klip_packet_candidate_preflight.py"
SPEC=importlib.util.spec_from_file_location("klip_packet_candidate_preflight",SCRIPT)
mod=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def inputs(tmp_path, monkeypatch, *, complete=True, bad_windows=False):
    root=tmp_path/"workspace"
    audit_path=root/"reports"/"campaign"/"audit"/"campaign-audit.json"
    scan_dir=root/"reports"/"campaign"/"coarse-scan"
    audit_path.parent.mkdir(parents=True)
    scan_dir.mkdir(parents=True)
    report={
        "catalogAssetCount":232,
        "goNoGo":{"coarseScannedEveryCatalogSource":complete},
        "scan":{"completed":232 if complete else 231,
                "failed":0,"blocked":0,"reportIntegrityFailures":0,
                "interruptedOrUnattempted":0 if complete else 1,
                "scanDurationCoveragePercentOfEntireCatalog":100.0},
    }
    monkeypatch.setattr(mod, "campaign_progress",lambda *a,**kw: report)
    audit={"version":AUDIT_VERSION}
    state={"version":SCAN_VERSION,"campaignAuditSha256":"a"*64}
    def load(path):
        return (audit,"a"*64) if path == audit_path else (state,"b"*64)
    monkeypatch.setattr(mod,"_load_json",load)
    seen=[]
    def candidates(audit_arg,state_arg,scan_path,limit,per_source):
        seen.append((limit,per_source))
        if bad_windows:
            raise ValueError(
                "Unbounded or unsupported coarse source interval: "
                "source=0123456789ab index=3 durationMs=400 window=(0,1000)"
            )
        return ([{
            "asset":{"sourceAssetSha256":"1"*64,"catalogActivity":"ski"},
            "startMs":10000,"endMs":30000,
            "selectionRole":"uniform_timeline_control",
        }],{
            "legacySubsecondProposalsExcluded":1,
            "legacySubsecondSourceIds":["2"*64]
        })
    monkeypatch.setattr(mod,"_candidates",candidates)
    return root,audit_path,scan_dir,seen


def test_metadata_preflight_discloses_legacy_candidates(tmp_path,monkeypatch):
    root,audit_path,scans,seen=inputs(tmp_path,monkeypatch)
    report=mod.preflight(root,audit_path,scans,limit=120,per_source=4)
    assert report["status"]=="metadata_preflight_passed_no_video_opened"
    assert report["selectedWindows"]==1
    assert report["selectedSourceCount"]==1
    assert report["selectedRoleCounts"]=={"uniform_timeline_control":1}
    assert report["windowValidation"]["legacySubsecondProposalsExcluded"]==1
    assert report["sourceVideoOpened"] is False
    assert report["previewMediaGenerated"] is False
    assert seen==[(120,4)]


def test_unqualified_scan_blocks_before_window_selection(tmp_path,monkeypatch):
    root,audit_path,scans,seen=inputs(tmp_path,monkeypatch,complete=False)
    with pytest.raises(ValueError,match="not fully qualified"):
        mod.preflight(root,audit_path,scans)
    assert seen==[]


def test_unrecognized_bad_window_blocks_before_ssd_work(tmp_path,monkeypatch):
    root,audit_path,scans,_=inputs(tmp_path,monkeypatch,bad_windows=True)
    with pytest.raises(ValueError,match="source=0123456789ab index=3"):
        mod.preflight(root,audit_path,scans)


def test_no_sources_or_invalid_budget_prevents_packet_preflight(tmp_path,monkeypatch):
    root,audit_path,scans,_=inputs(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match="limit"):
        mod.preflight(root,audit_path,scans,limit=0)
    with pytest.raises(ValueError,match="per-source"):
        mod.preflight(root,audit_path,scans,per_source=0)


def test_outside_workspace_report_root_is_refused(tmp_path,monkeypatch):
    root,audit_path,scans,_=inputs(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match="private workspace"):
        mod.preflight(root,tmp_path/"elsewhere"/"audit.json",scans)

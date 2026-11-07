"""Regression tests for metadata-only source/clip proposal coverage.

All source paths point to deliberately unavailable mount names. Nothing
reads, resolves, hashes or decodes original camera footage.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from adventure_intelligence.campaign_discovery_audit import (
    inspect_discovery, choose_balanced_review, discovery_selection_report,
    _workspace_report,
)
from adventure_intelligence.full_archive_campaign import AUDIT_VERSION, SCAN_VERSION


def _campaign(tmp_path: Path, count: int = 8, done: int | None = None):
    done = count if done is None else done
    ws = tmp_path / "ws"
    root = ws / "reports" / "campaign"
    (root / "audit").mkdir(parents=True)
    (root / "coarse-scan" / "per-source").mkdir(parents=True)
    assets = []
    state = {
        "version": SCAN_VERSION,
        "assets": {},
    }
    names = ("ski", "bike", "snorkeling", "unknown")
    for i in range(count):
        aid = f"{i + 1:064x}"
        assets.append({
            "sourceAssetSha256": aid,
            "sourceBytesFromCatalog": 1234,
            "durationMsFromCatalog": 120000,
            "catalogActivity": names[i % len(names)],
            "storedPathAsCatalogued": "/Volumes/DONT_READ/" + aid + ".mp4",
        })
        if i >= done:
            continue
        receipt = {
            "version": SCAN_VERSION,
            "sourceAssetSha256": aid,
            "verifiedOriginalSha256": aid,
            "shaVerifiedDuringThisScan": True,
            "sourceDurationMs": 120000,
            "originalSizeBytes": 1234,
            "candidateHypotheses": [
                {"role": "uniform_timeline_control", "startMs": 0, "endMs": 20000},
                {"role": "motion_change_hypothesis", "startMs": 15000, "endMs": 35000,
                 "motionChangeEvidence": 0.12},
            ],
        }
        body = (json.dumps(receipt, sort_keys=True) + "\n").encode()
        (root / "coarse-scan" / "per-source" / (aid + ".json")).write_bytes(body)
        state["assets"][aid] = {
            "status": "coarse_completed", "reportSha256": sha256(body).hexdigest(),
        }
    audit = {
        "version": AUDIT_VERSION, "workspace": str(ws),
        "catalogAssetCount": count, "assets": assets,
    }
    audit_bytes = (json.dumps(audit, sort_keys=True) + "\n").encode()
    (root / "audit" / "campaign-audit.json").write_bytes(audit_bytes)
    state["campaignAuditSha256"] = sha256(audit_bytes).hexdigest()
    (root / "coarse-scan" / "scan-state.json").write_text(json.dumps(state))
    return ws, root, audit, state


def _inspect(ws, root):
    return inspect_discovery(ws, root / "audit" / "campaign-audit.json",
                             root / "coarse-scan")


def test_union_coverage_is_not_video_scan_coverage(tmp_path):
    ws, root, _, _ = _campaign(tmp_path, count=4)
    summary, candidates = _inspect(ws, root)
    assert summary["verifiedCompletedSources"] == 4
    assert summary["proposedWindowUnionMs"] == 4 * 35000
    assert summary["totalCatalogDurationMs"] == 4 * 120000
    assert summary["proposedWindowCoveragePercentOfCatalog"] == 29.17
    assert summary["proposalCount"] == 8
    assert summary["uniformControls"] == 4
    assert summary["motionChangeHypotheses"] == 4
    assert summary["sourceMediaRead"] is False
    assert all(x["observedEvent"] == "unknown" for x in candidates)


def test_first_review_round_spreads_across_unique_sources(tmp_path):
    ws, root, _, _ = _campaign(tmp_path, count=8)
    summary, proposals = _inspect(ws, root)
    plan = discovery_selection_report(summary, proposals, limit=5, per_source=2)
    assert plan["selectedWindows"] == 5
    assert plan["selectedDistinctSources"] == 5
    assert set(plan["selectedActivityStrata"]) == {"ski", "bike", "snorkeling", "unknown"}
    assert set(plan["selectedRoles"]) == {
        "motion_change_hypothesis", "uniform_timeline_control"
    }
    assert plan["status"] == "qualified_full_archive_candidate_roster"
    assert all(x["humanApprovalRequired"] for x in plan["rows"])


def test_second_pass_spreads_roles_and_has_no_near_duplicate(tmp_path):
    ws, root, _, _ = _campaign(tmp_path, count=4)
    _, proposals = _inspect(ws, root)
    selection = choose_balanced_review(proposals, limit=8, per_source=2)
    assert len(selection) == 8
    counts = {}
    for x in selection:
        counts[x["sourceAssetSha256"]] = counts.get(x["sourceAssetSha256"], 0) + 1
    assert sorted(counts.values()) == [2, 2, 2, 2]
    assert len({(x["sourceAssetSha256"], x["startMs"], x["endMs"])
                for x in selection}) == 8
    assert selection == choose_balanced_review(proposals, limit=8, per_source=2)


def test_partial_scan_remains_exploratory(tmp_path):
    ws, root, _, _ = _campaign(tmp_path, count=6, done=3)
    summary, proposals = _inspect(ws, root)
    plan = discovery_selection_report(summary, proposals, limit=30)
    assert summary["unattemptedSources"] == 3
    assert summary["verifiedCompletedSources"] == 3
    assert not summary["fullArchiveReadyForHumanPacket"]
    assert plan["status"] == "exploratory_partial_scan_roster_do_not_freeze_as_final"


def test_failed_and_blocked_are_not_invented_negatives(tmp_path):
    ws, root, audit, state = _campaign(tmp_path, count=4, done=2)
    state["assets"][audit["assets"][2]["sourceAssetSha256"]] = {"status": "failed"}
    state["assets"][audit["assets"][3]["sourceAssetSha256"]] = {
        "status": "blocked_missing_local_media"
    }
    (root/"coarse-scan"/"scan-state.json").write_text(json.dumps(state))
    summary, proposals = _inspect(ws, root)
    assert summary["verifiedCompletedSources"] == 2
    assert summary["failedSources"] == 1
    assert summary["blockedSources"] == 1
    assert summary["unattemptedSources"] == 0
    assert all(x["observedEvent"] == "unknown" for x in proposals)
    assert not summary["fullArchiveReadyForHumanPacket"]


def test_tampered_source_receipt_is_invalid_and_not_selected(tmp_path):
    ws, root, audit, state = _campaign(tmp_path, count=4)
    aid = audit["assets"][0]["sourceAssetSha256"]
    (root/"coarse-scan"/"per-source"/(aid+".json")).write_text("{}")
    summary, proposals = _inspect(ws, root)
    assert summary["invalidReceipts"] == 1
    assert summary["verifiedCompletedSources"] == 3
    assert len(proposals) == 6
    assert not summary["fullArchiveReadyForHumanPacket"]


def test_bad_candidate_interval_invalidates_its_source_receipt(tmp_path):
    ws, root, audit, state = _campaign(tmp_path, count=3)
    aid = audit["assets"][0]["sourceAssetSha256"]
    file = root/"coarse-scan"/"per-source"/(aid+".json")
    record = json.loads(file.read_text())
    record["candidateHypotheses"][0]["endMs"] = 121000
    body = json.dumps(record).encode()
    file.write_bytes(body)
    state["assets"][aid]["reportSha256"] = sha256(body).hexdigest()
    (root/"coarse-scan"/"scan-state.json").write_text(json.dumps(state))
    summary, proposals = _inspect(ws, root)
    assert summary["invalidReceipts"] == 1
    assert len(proposals) == 4


def test_symlinked_receipt_cannot_escape_workspace(tmp_path):
    ws, root, audit, _ = _campaign(tmp_path, count=3)
    aid = audit["assets"][0]["sourceAssetSha256"]
    link = root/"coarse-scan"/"per-source"/(aid+".json")
    link.unlink()
    link.symlink_to("/Volumes/DONT_READ/untrusted.mp4")
    summary, _ = _inspect(ws, root)
    assert summary["invalidReceipts"] == 1


def test_bad_checkpoint_audit_fingerprint_fails_closed(tmp_path):
    ws, root, _, state = _campaign(tmp_path, count=4)
    state["campaignAuditSha256"] = "f" * 64
    (root/"coarse-scan"/"scan-state.json").write_text(json.dumps(state))
    with pytest.raises(ValueError, match="incompatible"):
        _inspect(ws, root)


def test_role_and_budget_validation():
    with pytest.raises(ValueError, match="limit"):
        choose_balanced_review([], limit=0)
    with pytest.raises(ValueError, match="Per-source"):
        choose_balanced_review([], per_source=0)


def test_no_originals_are_ever_accessed(tmp_path, monkeypatch):
    ws, root, _, _ = _campaign(tmp_path, count=4)
    original_stat = Path.stat
    original_resolve = Path.resolve
    def guard(call):
        def wrapped(path, *args, **kwargs):
            if str(path).startswith("/Volumes/DONT_READ"):
                pytest.fail("Metadata report unexpectedly touched source video path")
            return call(path, *args, **kwargs)
        return wrapped
    monkeypatch.setattr(Path, "stat", guard(original_stat))
    monkeypatch.setattr(Path, "resolve", guard(original_resolve))
    summary, proposals = _inspect(ws, root)
    choose_balanced_review(proposals, limit=4)
    assert summary["sourceMediaRead"] is False


def test_report_path_dotdot_cannot_escape_workspace(tmp_path):
    root = tmp_path/"ws"
    (root/"reports").mkdir(parents=True)
    with pytest.raises(ValueError, match="inside the specified workspace"):
        _workspace_report(root, root/"reports"/".."/".."/"other"/"report.json")


def test_report_symlinked_parent_is_refused(tmp_path):
    root = tmp_path/"ws"
    (root/"reports").mkdir(parents=True)
    external = tmp_path/"outside"
    external.mkdir()
    (root/"reports"/"link").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlinked report"):
        _workspace_report(root, root/"reports"/"link"/"scan-state.json")

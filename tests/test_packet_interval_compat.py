"""Regression for legacy short (<1s) source candidate interval contract.

No source media is opened, changed or silently rehashed. We synthesize an
immutable SHA-backed source scan report and preserve every byte after reading.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json

import pytest

from adventure_intelligence.full_archive_campaign import (
    SCAN_VERSION, _proposals
)
from adventure_intelligence.full_archive_review_packet import _candidates


def _fixture(tmp_path: Path, *, duration: int,
             windows: list[dict], alter_receipt: bool = False):
    aid = "a" * 64
    folder = tmp_path / "coarse-scan"
    per_source = folder / "per-source"
    per_source.mkdir(parents=True)
    audit = {"assets": [{
        "sourceAssetSha256": aid,
        "durationMsFromCatalog": duration,
        "sourceBytesFromCatalog": 4096,
        "catalogActivity": "ski",
    }]}
    payload = {
        "version": SCAN_VERSION,
        "sourceAssetSha256": aid,
        "verifiedOriginalSha256": aid,
        "shaVerifiedDuringThisScan": True,
        "sourceDurationMs": duration,
        "originalSizeBytes": 4096,
        "candidateHypotheses": windows,
    }
    b = (json.dumps(payload, sort_keys=True) + "\n").encode()
    report = per_source / (aid + ".json")
    report.write_bytes(b)
    scan = {"assets": {aid: {
        "status": "coarse_completed",
        "reportSha256": ("b" * 64 if alter_receipt else sha256(b).hexdigest()),
    }}}
    return audit, scan, folder, report, b


def _window(start: int, end: int,
            role: str = "uniform_timeline_control") -> dict:
    return {"startMs": start, "endMs": end,
            "role": role, "motionChangeEvidence": None}


def test_legacy_impossible_subsecond_window_is_excluded_and_disclosed(tmp_path):
    # Old _proposals() used max(duration, 1000), producing an impossible
    # (0, 1000) proposal from a 500ms source. The source SHA can still pass.
    audit, scan, folder, report, original = _fixture(
        tmp_path, duration=500, windows=[_window(0, 1000)])
    chosen, notes = _candidates(audit, scan, folder, limit=10, per_source=4)
    assert chosen == []
    assert notes["legacySubsecondProposalsExcluded"] == 1
    assert notes["legacySubsecondSourceIds"] == ["a" * 64]
    assert report.read_bytes() == original
    assert sha256(report.read_bytes()).hexdigest() == scan["assets"]["a" * 64]["reportSha256"]


def test_future_scanner_never_invents_subsecond_window():
    assert _proposals([], 500) == []
    assert _proposals([{"t_ms":200, "change":0.5}], 999) == []
    assert _proposals([], 0) == []
    assert _proposals([], 1_000)[0]["endMs"] == 1000


def test_valid_long_source_is_preserved_unchanged(tmp_path):
    audit, scan, folder, report, original = _fixture(
        tmp_path, duration=60000,
        windows=[_window(0, 20000), _window(20000, 40000)])
    chosen, notes = _candidates(audit, scan, folder, limit=10, per_source=4)
    assert len(chosen) == 2
    assert notes["legacySubsecondProposalsExcluded"] == 0
    assert report.read_bytes() == original


@pytest.mark.parametrize("duration,start,end",[
    (500, 100, 1000),    # Not exact known legacy shape
    (60000, 0, 60001),  # General out-of-bounds
    (60000, 1000, 1000),
    (60000, -1, 20000),
    (60000, 0, 999),    # Insufficient duration
])
def test_unrecognized_bad_interval_fails_with_identifiable_source(
        tmp_path, duration, start, end):
    audit, scan, folder, _, _ = _fixture(
        tmp_path, duration=duration, windows=[_window(start, end)])
    with pytest.raises(ValueError, match=r"source=aaaaaaaaaaaa index=0 durationMs="):
        _candidates(audit, scan, folder, limit=10, per_source=4)


def test_tampered_completed_source_receipt_still_blocks(tmp_path):
    audit, scan, folder, _, _ = _fixture(
        tmp_path, duration=500, windows=[_window(0, 1000)],
        alter_receipt=True)
    with pytest.raises(ValueError, match="Completed source report changed"):
        _candidates(audit, scan, folder, limit=10, per_source=4)


def test_unsupported_role_is_not_masked_by_short_recording(tmp_path):
    audit, scan, folder, _, _ = _fixture(
        tmp_path, duration=500,
        windows=[_window(0, 1000, role="confirmed_ski_landing")])
    with pytest.raises(ValueError, match="unsupported coarse source interval"):
        _candidates(audit, scan, folder, limit=10, per_source=4)

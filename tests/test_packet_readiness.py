"""Pure metadata-only readiness gate for full-archive packet creation."""
from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "klip_packet_readiness.py"
spec = importlib.util.spec_from_file_location("klip_packet_readiness",MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def progress(completed=232,total=232,failed=0,blocked=0,integrity=0,remaining=0):
    return {
        "catalogAssetCount":total,
        "scan":{
            "available":True,
            "completed":completed,
            "failed":failed,
            "blocked":blocked,
            "reportIntegrityFailures":integrity,
            "interruptedOrUnattempted":remaining,
            "scanDurationCoveragePercentOfEntireCatalog":100 if completed==total else 11.2
        }
    }


def test_incomplete_scan_never_releases_packet():
    result=mod.evaluate(progress(completed=26,remaining=206))
    assert not result["ready"]
    assert result["originalMediaOpened"] is False
    assert result["verifiedCompletedSources"]==26
    assert any("26 of 232" in x for x in result["blockers"])


def test_all_verified_sources_pass():
    result=mod.evaluate(progress())
    assert result["ready"]
    assert result["catalogAssets"]==232
    assert result["blockers"]==[]


def test_exhausted_with_failed_sources_is_not_complete():
    result=mod.evaluate(progress(completed=231,failed=1))
    assert not result["ready"]
    assert any("failed=1" in x for x in result["blockers"])


def test_integrity_failure_blocks_even_if_all_have_completed_status():
    result=mod.evaluate(progress(integrity=1))
    assert not result["ready"]
    assert "reportIntegrityFailures=1" in result["blockers"]


def test_blocked_source_blocks_packet():
    result=mod.evaluate(progress(completed=231,blocked=1))
    assert not result["ready"]

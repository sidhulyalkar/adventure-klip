#!/usr/bin/env python3
"""Metadata-only preflight for a frozen full-archive review packet.

Verify every SHA-receipted coarse scan source and the exact packet candidate
selection before attempting an original-video SHA pass or FFmpeg preview
encoding. No raw camera source is accessed, resolved, statted, or hashed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from adventure_intelligence.full_archive_campaign import (
    AUDIT_VERSION, SCAN_VERSION, _load_json,
)
from adventure_intelligence.full_archive_campaign_status import campaign_progress
from adventure_intelligence.full_archive_review_packet import _candidates


def preflight(workspace: Path, audit_file: Path, scan_dir: Path, *,
              limit: int = 120, per_source: int = 4) -> dict:
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("Packet candidate limit must be 1..500")
    if type(per_source) is not int or not 1 <= per_source <= 12:
        raise ValueError("Packet per-source limit must be 1..12")
    root = Path(workspace).expanduser().resolve()
    audit_path = Path(audit_file).expanduser().absolute()
    scans = Path(scan_dir).expanduser().absolute()
    for path in (audit_path, scans):
        if (path.is_symlink() or not path.is_relative_to(root)
                or not path.relative_to(root).parts
                or path.relative_to(root).parts[0] not in ("reports", "exports")):
            raise ValueError("Packet reports must be under the selected private workspace")
    progress = campaign_progress(root, audit_path, scan_dir=scans)
    if not progress["goNoGo"]["coarseScannedEveryCatalogSource"]:
        scan = progress["scan"]
        raise ValueError(
            "Frozen source scan is not fully qualified: "
            f"completed={scan['completed']}/{progress['catalogAssetCount']} "
            f"failed={scan['failed']} blocked={scan['blocked']} "
            f"integrityFailures={scan['reportIntegrityFailures']} "
            f"pending={scan['interruptedOrUnattempted']}"
        )
    audit, audit_sha = _load_json(audit_path)
    state, _ = _load_json(scans / "scan-state.json")
    if (audit.get("version") != AUDIT_VERSION
            or state.get("version") != SCAN_VERSION
            or state.get("campaignAuditSha256") != audit_sha):
        raise ValueError("Packet scan checkpoint does not match the frozen audit")
    selected, diagnostics = _candidates(audit, state, scans, limit, per_source)
    if not selected:
        raise ValueError("Packet has no qualifying source-backed candidate windows")
    roles = Counter(row["selectionRole"] for row in selected)
    activities = Counter(row["asset"]["catalogActivity"] for row in selected)
    sources = {row["asset"]["sourceAssetSha256"] for row in selected}
    return {
        "status": "metadata_preflight_passed_no_video_opened",
        "catalogSourcesVerified": progress["scan"]["completed"],
        "sourceScanDurationCoveragePct": progress["scan"]["scanDurationCoveragePercentOfEntireCatalog"],
        "requestedPacketLimit": limit,
        "selectedWindows": len(selected),
        "selectedSourceCount": len(sources),
        "selectedRoleCounts": dict(sorted(roles.items())),
        "selectedActivityCounts": dict(sorted(activities.items())),
        "windowValidation": diagnostics,
        "sourceVideoOpened": False,
        "sourceVideoShaRecomputed": False,
        "previewMediaGenerated": False,
        "warning": (
            "A validated sampling window is not a verified event, "
            "playable preview, human Keep decision or final highlight."
        ),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--scan-dir", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--per-source", type=int, default=4)
    args = parser.parse_args(argv)
    try:
        result = preflight(args.workspace, args.audit, args.scan_dir,
                           limit=args.limit, per_source=args.per_source)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"PACKET PREFLIGHT BLOCKED: {exc}", file=sys.stderr)
        return 3
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Metadata-only release gate for immutable full-archive review packets.

A package caller can intentionally create exploratory packets, but the
default campaign launcher must never silently certify a partial live scan as
the finished full-archive review. Reads only workspace JSON and receipts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from adventure_intelligence.full_archive_campaign_status import campaign_progress


def evaluate(progress: dict) -> dict:
    scan=progress["scan"]
    total=progress["catalogAssetCount"]
    blockers=[]
    if total < 1 or not scan["available"]:
        blockers.append("No valid source scan checkpoint or frozen catalog")
    if scan["completed"] != total:
        blockers.append(f"Verified completed source reports {scan['completed']} of {total}")
    for key in ("failed","blocked","reportIntegrityFailures","interruptedOrUnattempted"):
        if scan[key] != 0:
            blockers.append(f"{key}={scan[key]}")
    return {
        "status": "ready_for_full_archive_packet" if not blockers
                  else "blocked_partial_or_unverified_scan",
        "ready": not blockers,
        "catalogAssets":total,
        "verifiedCompletedSources":scan["completed"],
        "durationCoveragePercent":scan["scanDurationCoveragePercentOfEntireCatalog"],
        "reportIntegrityFailures":scan["reportIntegrityFailures"],
        "blockers":blockers,
        "originalMediaOpened":False,
        "recommendation": "Finish or qualify the source scan before freezing the full-archive packet"
                          if blockers else "Safe to freeze a full-archive review packet",
    }


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",required=True,type=Path)
    parser.add_argument("--audit",required=True,type=Path)
    parser.add_argument("--scan-dir",required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        progress=campaign_progress(args.workspace,args.audit,scan_dir=args.scan_dir)
        readiness=evaluate(progress)
    except (OSError,ValueError,KeyError) as exc:
        print(f"PACKET READINESS BLOCKED: {exc}",file=sys.stderr)
        return 3
    print(json.dumps(readiness,indent=2,sort_keys=True))
    return 0 if readiness["ready"] else 3


if __name__ == "__main__":
    sys.exit(main())

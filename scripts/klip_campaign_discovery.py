#!/usr/bin/env python3
"""Read-only archive discovery coverage and source-balanced review planning.

Zero external SSD I/O: consumes frozen audit and SHA-receipted per-source
coarse scan reports from an explicitly selected Klip workspace.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from adventure_intelligence.campaign_discovery_audit import (
    inspect_discovery, discovery_selection_report, _workspace_report,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--per-source", type=int, default=2)
    parser.add_argument("--output-dir", type=Path,
                        help="Optional NEW directory under workspace reports/ or exports/")
    parser.add_argument("--json", action="store_true",
                        help="Print summary/plan metadata as JSON")
    args = parser.parse_args(argv)
    try:
        root = args.workspace.expanduser().resolve()
        directory = _workspace_report(root, args.campaign_dir)
        audit = directory / "audit" / "campaign-audit.json"
        scan = directory / "coarse-scan"
        summary, candidates = inspect_discovery(root, audit, scan)
        plan = discovery_selection_report(summary, candidates,
                                          limit=args.limit,
                                          per_source=args.per_source)
        if args.output_dir is not None:
            output = _workspace_report(root, args.output_dir)
            if output == directory or output.is_relative_to(directory):
                raise ValueError("Save the planning snapshot outside the source campaign")
            output.mkdir(mode=0o700, parents=False, exist_ok=False)
            (output/"discovery-coverage.json").write_text(
                json.dumps(summary, indent=2, sort_keys=True) + "\n")
            (output/"review-selection-plan.json").write_text(
                json.dumps(plan, indent=2, sort_keys=True) + "\n")
        public = {
            **summary,
            "selection": {key: value for key, value in plan.items() if key != "rows"},
            "outputDirectory": str(args.output_dir) if args.output_dir else None,
        }
        if args.json:
            print(json.dumps(public, indent=2, sort_keys=True))
        else:
            print(f"Sources verified: {summary['verifiedCompletedSources']}/{summary['catalogSourceCount']}")
            print(f"Pending {summary['unattemptedSources']}, failed {summary['failedSources']},"
                  f" blocked {summary['blockedSources']}, invalid receipts {summary['invalidReceipts']}")
            print(f"Candidate windows: {summary['proposalCount']}"
                  f" (motion {summary['motionChangeHypotheses']}, quiet controls {summary['uniformControls']})")
            print(f"Source timeline spanned by merged windows:"
                  f" {summary['proposedWindowCoveragePercentOfCatalog']}% of catalog")
            print(f"Balanced review proposal: {plan['selectedWindows']} windows across"
                  f" {plan['selectedDistinctSources']} distinct sources")
            print(f"Activity strata: {plan['selectedActivityStrata']}")
            print(f"Selection roles: {plan['selectedRoles']}")
            print(f"Status: {plan['status']}")
            if args.output_dir:
                print(f"Report directory: {args.output_dir}")
        return 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"DISCOVERY AUDIT BLOCKED: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

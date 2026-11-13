#!/usr/bin/env python3
"""Plan where to decode dense frames next; does not touch source footage.

Reads the saved ranked/exploration packet, current catalog metadata and
checksum-verified frozen semantic caches. JSON stdout is a private,
inspectable decode request, NOT an executable or approved edit.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from adventure_intelligence.moment_adaptive_inspection import plan_inspection


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",required=True)
    parser.add_argument("--rank-report",help="Existing ranking JSON inside workspace/reports")
    parser.add_argument("--max-clips",type=int,default=16)
    parser.add_argument("--frame-budget",type=int,default=1700)
    parser.add_argument("--explore-fraction",type=float,default=.25)
    parser.add_argument("--max-per-source",type=int,default=2)
    args = parser.parse_args()
    try:
        report = plan_inspection(
            Path(args.workspace).expanduser(),
            rank_report=Path(args.rank_report).expanduser() if args.rank_report else None,
            max_clips=args.max_clips,
            frame_budget=args.frame_budget,
            explore_fraction=args.explore_fraction,
            max_per_source=args.max_per_source,
        )
    except (ValueError,OSError,TypeError,OverflowError) as exc:
        parser.error(str(exc))
    print(json.dumps(report,indent=2,sort_keys=True,allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

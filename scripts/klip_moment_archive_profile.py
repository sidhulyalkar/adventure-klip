#!/usr/bin/env python3
"""Characterize the private Klip archive and propose a bounded pilot.

Reads SQLite catalog metadata and optionally an existing local judge report.
Prints JSON to stdout. Does NOT read or hash original video, call a model,
infer verified sport/trip identity, edit, or modify the workspace.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adventure_intelligence.moment_archive_profile import audit_archive


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", required=True)
    p.add_argument("--judge-report", help="Optional private prior proposals.json")
    p.add_argument("--limit", type=int, default=5000)
    p.add_argument("--pilot-sources", type=int, default=24)
    p.add_argument("--seed", type=int, default=17)
    p.add_argument("--coarse-seconds", type=float, default=10.0)
    p.add_argument("--dense-seconds-per-source", type=float, default=30.0)
    p.add_argument("--dense-fps", type=float, default=8.0)
    args=p.parse_args()
    try:
        report=audit_archive(
            Path(args.workspace).expanduser(),
            judge_report=(Path(args.judge_report).expanduser()
                          if args.judge_report else None),
            limit=args.limit, pilot_sources=args.pilot_sources,
            seed=args.seed, coarse_seconds=args.coarse_seconds,
            dense_seconds_per_source=args.dense_seconds_per_source,
            dense_fps=args.dense_fps,
        )
    except (OSError, ValueError, ImportError, TypeError, OverflowError) as exc:
        p.error(str(exc))
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())

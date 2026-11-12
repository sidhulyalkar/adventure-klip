#!/usr/bin/env python3
"""Rank existing cached Klip episodes for review, plus unknown-coverage probes.

Source-safe, read-only, offline. JSON stdout only. No original media reads,
no scoring of unseen footage, no edits, and no trained preference model.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adventure_intelligence.moment_clip_ranking import GOALS
from adventure_intelligence.moment_rank_archive import rank_archive


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",required=True)
    parser.add_argument("--judge-report",
                        help="Optional existing private, verified-source vision judge hypotheses")
    parser.add_argument("--goal",choices=GOALS,default="auto")
    parser.add_argument("--limit",type=int,default=24)
    parser.add_argument("--explore-fraction",type=float,default=.35)
    parser.add_argument("--max-per-source",type=int,default=2)
    parser.add_argument("--seed",type=int,default=17)
    parser.add_argument("--source-limit",type=int,default=5000)
    parser.add_argument("--episode-limit",type=int,default=6000)
    args=parser.parse_args()
    try:
        report=rank_archive(
            Path(args.workspace).expanduser(),
            judge_report=(Path(args.judge_report).expanduser()
                          if args.judge_report else None),
            goal=args.goal,limit=args.limit,
            explore_fraction=args.explore_fraction,
            max_per_source=args.max_per_source,seed=args.seed,
            source_limit=args.source_limit,
            episode_limit=args.episode_limit,
        )
    except (OSError,ValueError,TypeError,OverflowError) as exc:
        parser.error(str(exc))
    print(json.dumps(report,indent=2,sort_keys=True,allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())

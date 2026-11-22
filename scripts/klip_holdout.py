#!/usr/bin/env python3
"""Propose, freeze and show the held-out test days (see adventure_intelligence.holdout).

    klip_holdout.py propose --reports "$WS/reports"
    klip_holdout.py freeze  --reports "$WS/reports" --day 2026-03-09:ski --day 2024-12-26:snorkeling
    klip_holdout.py show    --reports "$WS/reports"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from adventure_intelligence import holdout


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("propose", "freeze", "show"):
        sub.add_parser(name).add_argument("--reports", type=Path, required=True)
    sub.choices["propose"].add_argument("--min-segments", type=int, default=8)
    f = sub.choices["freeze"]
    f.add_argument("--day", action="append", metavar="YYYY-MM-DD:SPORT")
    f.add_argument("--proposed", action="store_true", help="freeze what propose suggests")
    f.add_argument("--replace", action="store_true")
    f.add_argument("--reason")
    args = parser.parse_args(argv)
    try:
        reports = args.reports.expanduser()
        if args.command == "propose":
            picks = holdout.propose(reports, min_segments=args.min_segments)
            days = {}
            for (d, sp), u in sorted(holdout.inventory(reports).items()):
                days.setdefault(sp, []).append(f"{d} ({u['reviewSegments']} segments)")
            print(json.dumps({"proposal": picks, "allDays": days}, indent=2))
        elif args.command == "freeze":
            if args.proposed:
                units = [(p["day"], p["sport"]) for p in holdout.propose(reports)]
            else:
                units = []
                for item in args.day or []:
                    day, _, sport = item.partition(":")
                    if not sport:
                        raise ValueError(f"Use YYYY-MM-DD:SPORT, not {item!r}")
                    units.append((day, sport))
            doc = holdout.freeze(reports, units, reason=args.reason, replace=args.replace)
            print(json.dumps({k: doc[k] for k in ("frozenAt", "units")} | {
                "recordings": len(doc["recordings"])}, indent=2, default=str))
        else:
            doc = holdout.load(reports)
            print(json.dumps(doc and {**doc, "recordings": len(doc["recordings"])}, indent=2))
    except (ValueError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

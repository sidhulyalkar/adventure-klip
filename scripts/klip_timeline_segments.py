#!/usr/bin/env python3
"""Segment one source timeline from its saved coarse motion series.

Reads only JSON written by the coarse scan (per-source/<sha>.motion.json) and,
optionally, the owner evidence ledger. Never opens video or the network.

    klip_timeline_segments.py --motion per-source/<sha>.motion.json \\
        [--ledger owner-evidence.jsonl] [--budget 0.25] [--output plan.json]

With --ledger, also reports recall of owner-marked Keep spans against the
fixed-window baseline at the same footage budget.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from adventure_intelligence import evidence_ledger as ledger
from adventure_intelligence import timeline_segments as ts
from adventure_intelligence.full_archive_campaign import MOTION_VERSION


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--motion", type=Path, required=True)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--budget", type=float, default=0.25)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        doc = json.loads(args.motion.read_text(encoding="utf-8"))
        if doc.get("version") != MOTION_VERSION:
            raise ValueError("Not a saved coarse motion timeline")
        duration, series = doc["sourceDurationMs"], doc["series"]
        plan = ts.propose(ts.segment(ts.change_from_motion(series), duration),
                          budget_fraction=args.budget)
        report = {"sourceAssetSha256": doc["sourceAssetSha256"], "plan": plan,
                  "evidenceLevel": "real_media_signal_unreviewed"}
        if args.ledger:
            kept = [(r["startMs"], r["endMs"]) for r in ledger.load(args.ledger)
                    if r["sourceSha256"] == doc["sourceAssetSha256"]
                    and r["decision"] == "keep" and r["selection"] == "owner_marked_span"]
            report["ownerKeptSpans"] = len(kept)
            if kept:
                report["comparison"] = ts.compare_to_fixed_windows(series, duration, kept)
        text = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if args.output:
            if args.output.exists():
                raise ValueError("Refusing to overwrite an existing report")
            args.output.write_text(text, encoding="utf-8")
        else:
            sys.stdout.write(text)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate separate sport-specific reel plans from frozen Klip packet proxies.

Default: exploration only, plans without rendering. --render explicitly
transcodes *verified local packet proxies*, not original SSD recordings.
Human-reviewed mode accepts exactly the export downloaded from the packet
blind-review page and includes only Keep selections.

Outputs include per-clip source SHA/timecode, human rationale where supplied,
camera-motion/quiet-control evidence caveats and a conservative shot plan.
No soundtrack, model claims, source video reads or automatic publication.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from adventure_intelligence.campaign_reels import plan_sport_reels


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", required=True, type=Path)
    p.add_argument("--campaign-dir", required=True, type=Path)
    p.add_argument("--packet-dir", required=True, type=Path)
    p.add_argument("--output-dir", required=True, type=Path)
    p.add_argument("--mode", choices=("exploratory", "reviewed"),
                   default="exploratory")
    p.add_argument("--reviews", type=Path,
                   help="Frozen blind-review JSON export; required for reviewed mode")
    p.add_argument("--seconds-per-sport", type=float, default=150)
    p.add_argument("--max-clips-per-sport", type=int, default=8)
    p.add_argument("--render", action="store_true",
                   help="Explicitly render silent 540p MP4s from local verified preview derivatives")
    args = p.parse_args(argv)
    try:
        root = Path(args.campaign_dir)
        summary = plan_sport_reels(
            args.workspace,
            root / "audit" / "campaign-audit.json",
            root / "coarse-scan",
            args.packet_dir,
            args.output_dir,
            mode=args.mode,
            reviews_file=args.reviews,
            duration_budget_s=args.seconds_per_sport,
            max_per_sport=args.max_clips_per_sport,
            render=args.render,
        )
        print(json.dumps(summary, sort_keys=True, indent=2))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"CAMPAIGN REEL WORKFLOW BLOCKED: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

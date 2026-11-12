#!/usr/bin/env python3
"""Propose source-to-source temporal neighbors from local capture metadata.

Safe read-only discovery, NO original-video reads or source grouping changes.
Capture time links cannot verify the same outing, ski trail, reef or animal.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adventure_intelligence.moment_archive_context import capture_neighborhoods


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--max-gap-hours", type=float, default=12.0)
    parser.add_argument("--max-neighbors", type=int, default=4)
    parser.add_argument("--limit", type=int, default=3000)
    args = parser.parse_args()
    try:
        result = capture_neighborhoods(
            Path(args.workspace).expanduser(),
            max_gap_hours=args.max_gap_hours,
            max_neighbors=args.max_neighbors,
            limit=args.limit,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

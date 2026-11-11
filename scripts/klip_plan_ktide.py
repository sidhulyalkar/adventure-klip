#!/usr/bin/env python3
"""Plan balanced K-TIDE windows from existing completed feature caches only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adventure_intelligence.ktide_planner import plan_cached_windows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--assignments", required=True,
                        help="Owner verified source -> trip and editorial profile mapping")
    parser.add_argument("--provider", default="siglip2",
                        choices=("siglip2", "histogram"))
    parser.add_argument("--window-seconds", type=int, default=30)
    parser.add_argument("--stride-seconds", type=int, default=30)
    parser.add_argument("--min-cached-frames", type=int, default=4)
    parser.add_argument("--max-per-source", type=int, default=20)
    args = parser.parse_args()
    path = Path(args.assignments).expanduser()
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        parser.error("Assignments must be a local regular JSON <= 2 MB")
    try:
        source_map = json.loads(path.read_bytes())
        result = plan_cached_windows(
            Path(args.workspace).expanduser(), source_map,
            provider=args.provider,
            window_seconds=args.window_seconds,
            stride_seconds=args.stride_seconds,
            min_cached_frames=args.min_cached_frames,
            max_per_source=args.max_per_source)
    except (ValueError, OSError, UnicodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

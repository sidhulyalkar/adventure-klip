#!/usr/bin/env python3
"""Read-only temporal coverage/PTS audit of a K-TIDE training manifest.

Prints machine-readable private JSON to stdout. No original media, models,
editorial claims, training, reports or source archive are modified.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adventure_intelligence.ktide_observability import temporal_observability


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--max-gap-ms", type=int, default=7000)
    args = parser.parse_args()
    try:
        report = temporal_observability(
            Path(args.manifest).expanduser(),
            workspace=Path(args.workspace).expanduser(),
            max_gap_ms=args.max_gap_ms,
        )
    except (OSError, ValueError, ImportError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

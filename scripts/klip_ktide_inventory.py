#!/usr/bin/env python3
"""Inventory completed local feature caches without reading original video."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adventure_intelligence.ktide_inventory import inventory_cached_features


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--provider", choices=("siglip2", "histogram"), default="siglip2")
    parser.add_argument("--limit", type=int, default=3000)
    args = parser.parse_args()
    try:
        report = inventory_cached_features(
            Path(args.workspace).expanduser(), provider=args.provider, limit=args.limit)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

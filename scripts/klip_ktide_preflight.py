#!/usr/bin/env python3
"""Audit cached semantic coverage without opening video or modifying SQLite."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from adventure_intelligence.ktide_preflight import preflight


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace", required=True)
    p.add_argument("--provider", choices=["siglip2", "histogram"], default="histogram")
    p.add_argument("--limit", type=int, default=3000)
    a = p.parse_args()
    try:
        report = preflight(Path(a.workspace).expanduser(), provider=a.provider,
                           limit=a.limit)
    except (ValueError, OSError) as exc:
        p.error(str(exc))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

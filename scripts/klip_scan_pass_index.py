#!/usr/bin/env python3
"""Find the next untouched campaign scan-pass log number.

This enumerates only a selected Klip report directory. It never opens videos,
the catalog, or a stale removable source path. The source scan owns its own
concurrency lock; this helper only prevents normal sequential restarts from
overwriting existing pass receipts.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re

_PATTERN = re.compile(r"^scan-pass-(\d+)\.(?:json|stderr\.log)$")


def next_pass_index(campaign_dir: Path) -> int:
    campaign_dir = Path(campaign_dir)
    if not campaign_dir.is_dir() or campaign_dir.is_symlink():
        raise ValueError("Campaign directory missing or symlinked")
    largest = 0
    for entry in campaign_dir.iterdir():
        match = _PATTERN.fullmatch(entry.name)
        if not match:
            continue
        if entry.is_symlink():
            raise ValueError("Refuse symlinked prior scan-pass receipt")
        largest = max(largest, int(match.group(1)))
    return largest + 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        print(next_pass_index(args.campaign_dir))
        return 0
    except (OSError, ValueError) as exc:
        parser.exit(3, f"SCAN PASS RECEIPT BLOCKED: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())

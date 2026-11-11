#!/usr/bin/env python3
"""Summarize an exported private Klip review packet without touching video.

Use --manifest when the frozen review manifest is available. The output
reports text-derived editorial intent, not verified media or model training.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adventure_intelligence.review_intent import analyze_export


def _read_local(path: str, max_size: int) -> bytes:
    p = Path(path).expanduser()
    if p.is_symlink() or not p.is_file() or not 2 <= p.stat().st_size <= max_size:
        raise ValueError("Local input missing, symlinked or too large")
    return p.read_bytes()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviews", required=True, help="Local exported reviews JSON")
    parser.add_argument("--manifest", help="Optional exact frozen curation-manifest.json")
    args = parser.parse_args()
    try:
        export = json.loads(_read_local(args.reviews, 4_000_000))
        frozen = _read_local(args.manifest, 12_000_000) if args.manifest else None
        result = analyze_export(export, frozen)
    except (ValueError, UnicodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

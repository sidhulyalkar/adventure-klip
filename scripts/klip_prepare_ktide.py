#!/usr/bin/env python3
"""K-TIDE dataset preparation: local completed feature caches, never source video."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adventure_intelligence.encoder_export import export_cached_training_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, help="Existing Klip workspace")
    parser.add_argument("--selection", required=True,
                        help="Explicit private source windows with trip group IDs")
    parser.add_argument("--name", required=True,
                        help="New dataset folder name, ktide-<label>")
    parser.add_argument("--provider", choices=("siglip2", "histogram"), default="siglip2")
    parser.add_argument("--max-frames", type=int, default=96)
    args = parser.parse_args()
    path = Path(args.selection).expanduser()
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8_000_000:
        parser.error("Selection must be regular, local and <= 8 MB")
    try:
        doc = json.loads(path.read_bytes())
        result = export_cached_training_dataset(
            Path(args.workspace).expanduser(), doc,
            name=args.name, provider=args.provider, max_frames=args.max_frames)
    except (OSError, ValueError, UnicodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

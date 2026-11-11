#!/usr/bin/env python3
"""Export K-TIDE and frozen-mean clip embeddings, offline JSON only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adventure_intelligence.ktide_embed import encode_dataset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--device", default="cpu", choices=("cpu", "cuda", "mps"))
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()
    try:
        result = encode_dataset(
            Path(args.manifest).expanduser(), Path(args.checkpoint).expanduser(),
            batch_size=args.batch_size, device=args.device)
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

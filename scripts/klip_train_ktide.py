#!/usr/bin/env python3
"""Train a local K-TIDE temporal adapter from frozen, source-grouped features.

The saved model learns temporal structure over completed SigLIP2 (or explicit
histogram baseline) embedding caches; it does NOT train a video pixel backbone,
fit human labels, or classify verified actions. No footage is decoded or sent.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adventure_intelligence.ktide_train import train_local


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--manifest", required=True, help="Prepared K-TIDE manifest.json")
    parser.add_argument("--name", required=True, help="New ktide-run-* folder")
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--lr", type=float, default=0.0003)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--mask-ratio", type=float, default=.3)
    parser.add_argument("--hidden", type=int, default=128)
    parser.add_argument("--layers", type=int, default=2)
    parser.add_argument("--max-gap-ms", type=int, default=7000)
    parser.add_argument("--device", choices=("auto", "mps", "cuda", "cpu"), default="auto")
    args = parser.parse_args()
    try:
        result = train_local(
            Path(args.manifest).expanduser(),
            workspace=Path(args.workspace).expanduser(),
            run_name=args.name,
            epochs=args.epochs, batch_size=args.batch_size,
            learning_rate=args.lr, seed=args.seed, mask_ratio=args.mask_ratio,
            hidden=args.hidden, layers=args.layers,
            max_gap_ms=args.max_gap_ms, device=args.device,
        )
    except (OSError, ValueError, ImportError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

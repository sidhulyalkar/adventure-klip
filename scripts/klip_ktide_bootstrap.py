#!/usr/bin/env python3
"""Diagnose missing K-TIDE caches; optionally bootstrap only approved sources.

Default inspect and preflight modes use read-only SQLite and filesystem stat:
no original bytes, model inference, source hashes or database writes.
Explicit --run is a different mode and requires acknowledgement of reading
and SHA-checking original video. External model downloads need opt-in.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from adventure_intelligence.ktide_bootstrap import (
    inspect_workspace, preflight_bootstrap, run_bootstrap,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inspect", action="store_true",
                      help="Read-only inventory of cached jobs and pilot candidates")
    mode.add_argument("--preflight", action="store_true",
                      help="Read-only plan for explicitly selected source identities")
    mode.add_argument("--run", action="store_true",
                      help="Explicitly read/hash originals and generate bounded SigLIP2 caches")
    parser.add_argument("--assignments",
                        help="Owner-confirmed klip-ktide-source-assignments-v1 JSON")
    parser.add_argument("--candidate-limit", type=int, default=16)
    parser.add_argument("--sample-seconds", type=float, default=4.0)
    parser.add_argument("--max-frames", type=int, default=160)
    parser.add_argument("--max-sources", type=int, default=6)
    parser.add_argument("--max-duration-minutes", type=float, default=8.0)
    parser.add_argument("--max-total-gb", type=float, default=30.0)
    parser.add_argument("--ack-original-reads", action="store_true",
                        help="Required for --run: original files are opened and SHA-verified")
    parser.add_argument("--allow-model-download", action="store_true",
                        help="Required for remote model weights; otherwise offline-only")
    args = parser.parse_args()
    workspace = Path(args.workspace).expanduser()
    if not args.inspect and not args.assignments:
        parser.error("--assignments is required for preflight/run")
    if args.run and not args.ack_original_reads:
        parser.error("Refusing --run without --ack-original-reads")
    if args.inspect and args.assignments:
        parser.error("--inspect never accepts source assignments")
    try:
        if args.inspect:
            result = inspect_workspace(workspace, candidates=args.candidate_limit)
        else:
            path = Path(args.assignments).expanduser()
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
                raise ValueError("Assignments must be regular local JSON <= 2 MB")
            assignments = json.loads(path.read_bytes())
            kwargs = {
                "sample_seconds": args.sample_seconds,
                "max_frames": args.max_frames,
                "max_sources": args.max_sources,
                "max_duration_minutes": args.max_duration_minutes,
                "max_total_gb": args.max_total_gb,
            }
            if args.run:
                if not args.allow_model_download:
                    # No pretrained model download unless an explicit opt-in
                    # is provided. Cached Hugging Face weights are okay.
                    os.environ["HF_HUB_OFFLINE"] = "1"
                    os.environ["TRANSFORMERS_OFFLINE"] = "1"
                result = run_bootstrap(workspace, assignments, **kwargs)
            else:
                result = preflight_bootstrap(workspace, assignments, **kwargs)
    except (ValueError, OSError, UnicodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.run and result["semanticResult"]["errors"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

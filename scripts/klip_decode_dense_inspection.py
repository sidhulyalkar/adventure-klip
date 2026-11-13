#!/usr/bin/env python3
"""Bounded, optionally executable K-MOMENT dense-frame pilot.

Defaults to read-only preflight. Explicit --run --ack-original-reads opens
user original sources READ ONLY, re-hashes their full content and runs
batched FFmpeg decoding for approved inspection intervals. Derived JPEGs
are private to workspace/reports and never change originals or catalog.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from adventure_intelligence.moment_dense_decode import decode_inspection


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",required=True)
    parser.add_argument("--plan",required=True)
    parser.add_argument("--name",default="kmoment-dense-pilot-001")
    parser.add_argument("--max-clips",type=int,default=8)
    parser.add_argument("--max-frames",type=int,default=800)
    parser.add_argument("--width",type=int,default=384)
    parser.add_argument("--timeout-seconds",type=int,default=360)
    parser.add_argument("--run",action="store_true",
                        help="Opt in to local FFmpeg reads of originals")
    parser.add_argument("--ack-original-reads",action="store_true",
                        help="Acknowledge full SHA-256 of selected source videos")
    args=parser.parse_args()
    try:
        result=decode_inspection(
            Path(args.workspace).expanduser(),Path(args.plan).expanduser(),
            run_name=args.name,max_clips=args.max_clips,max_frames=args.max_frames,
            width=args.width,timeout_seconds=args.timeout_seconds,
            run=args.run,ack_original_reads=args.ack_original_reads,
        )
    except (OSError,ValueError,RuntimeError,TypeError) as exc:
        # Avoid printing absolute source paths from failures.
        parser.error(type(exc).__name__+": "+(
            str(exc) if type(exc) is ValueError else "source/cache preflight failed"))
    print(json.dumps(result,indent=2,sort_keys=True,allow_nan=False))
    return int(bool(result.get("failures")))


if __name__=="__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Audit derived dense frames, produce contact sheets and optional SigLIP2 timelines.

Preflight by default; --run only reads already decoded, SHA-backed JPEGs
and writes private derived reports. Never opens the original video files.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

from adventure_intelligence.moment_dense_qa import analyze_dense


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",required=True)
    parser.add_argument("--plan",required=True)
    parser.add_argument("--decode-name",default="kmoment-dense-pilot-001")
    parser.add_argument("--name",default="kmoment-qa-pilot-001")
    parser.add_argument("--backend",choices=("storyboards","siglip2"),
                        default="storyboards")
    parser.add_argument("--batch-size",type=int,default=6)
    parser.add_argument("--run",action="store_true")
    args=parser.parse_args()
    try:
        result=analyze_dense(
            Path(args.workspace).expanduser(),
            Path(args.plan).expanduser(),
            decode_name=args.decode_name,
            analysis_name=args.name,
            backend=args.backend,
            batch_size=args.batch_size,
            run=args.run,
        )
    except (OSError,ValueError,TypeError,RuntimeError) as exc:
        parser.error(str(exc) if isinstance(exc,ValueError)
                     else type(exc).__name__+": invalid private input")
    print(json.dumps(result,indent=2,sort_keys=True,allow_nan=False))
    return int(bool(result.get("errors")))


if __name__=="__main__":
    raise SystemExit(main())

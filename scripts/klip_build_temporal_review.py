#!/usr/bin/env python3
"""Build an offline visual review page from previously verified dense JPEG evidence.

No source-video access, model inference, network, database writes or automatic
label imports. The generated page plays cached stills and exports opt-in
human annotations as private JSON.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from adventure_intelligence.moment_temporal_review import build_temporal_review


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace",required=True)
    parser.add_argument("--plan",required=True)
    parser.add_argument("--decode-name",default="kmoment-dense-pilot-001")
    parser.add_argument("--qa-name",default="kmoment-qa-siglip2-001")
    parser.add_argument("--name",default="kmoment-review-pilot-001")
    args=parser.parse_args()
    try:
        result=build_temporal_review(
            Path(args.workspace).expanduser(),
            Path(args.plan).expanduser(),
            decode_name=args.decode_name,
            qa_name=args.qa_name,
            review_name=args.name)
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc) if isinstance(exc,ValueError)
                     else type(exc).__name__+": unsafe private review input")
    print(json.dumps(result,indent=2,sort_keys=True,allow_nan=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())

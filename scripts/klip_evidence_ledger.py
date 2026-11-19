#!/usr/bin/env python3
"""Import frozen owner review exports into the append-only evidence ledger.

Reads only the JSON files named on the command line. It never opens video,
the catalog or the network, and never writes to its inputs.

    klip_evidence_ledger.py import --kind marine --context manifest.json \\
        --review reviewed.json --ledger owner-evidence.jsonl
    klip_evidence_ledger.py report --ledger owner-evidence.jsonl [--outings outings.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from adventure_intelligence import evidence_ledger as ledger


def _json(path: Path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing or symlinked input: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("--kind", choices=sorted(ledger.ADAPTERS), required=True)
    imp.add_argument("--context", type=Path,
                     help="frozen sample manifest, or the ski run context plan; a ski-run "
                          "annotation can be imported without it")
    imp.add_argument("--review", type=Path, required=True)
    imp.add_argument("--ledger", type=Path, required=True)
    rep = sub.add_parser("report")
    rep.add_argument("--ledger", type=Path, required=True)
    rep.add_argument("--outings", type=Path,
                     help="JSON object mapping source SHA-256 to an outing ID")
    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            before = ledger.file_digest(args.review)
            review = _json(args.review)
            if args.context is not None:
                context = _json(args.context)
            elif args.kind == "ski-run":
                context = ledger.ski_run_plan_from_annotation(review)
            else:
                raise ValueError(f"--context is required for {args.kind} reviews")
            rows = ledger.ADAPTERS[args.kind](context, review, before)
            if args.context is None:
                rows = [{**r, "contextPlanReconstructed": True} for r in rows]
            result = ledger.append(args.ledger, rows)
            if ledger.file_digest(args.review) != before:
                raise ValueError("Review export changed during import")
            print(json.dumps({**result, "reviewSha256": before}, indent=2))
        else:
            rows = ledger.load(args.ledger)
            if args.outings:
                rows = ledger.with_outings(rows, _json(args.outings))
            print(json.dumps({**ledger.summary(rows),
                              "conflictRecords": ledger.conflicts(rows)}, indent=2))
    except (ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the discovery experiment end to end on a short, explicit list of recordings.

    verify SHA -> motion timeline -> segments -> proxy clips -> judge panel -> report

Resumable: rerun the same command after an interruption and finished work is
skipped. Sources are read-only; output must be outside the footage directory.
Judges run through Ollama on this machine only.

Write the source list first (at most 12 recordings):

    klip_discovery_experiment.py list --sport ski /path/run1.MP4 /path/run2.MP4 > sources.json

Then run, and rerun to resume:

    klip_discovery_experiment.py run --sources sources.json --workspace "$WS" \\
        --trusted-source-root /Volumes/SSD/DJI --allow-mounted-ssd \\
        --output "$WS/reports/discovery-001" [--ledger owner-evidence.jsonl]

Use --stages verify motion segments to stop before any model call.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from adventure_intelligence import discovery_experiment as experiment
from adventure_intelligence import judge_panel as panel


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="hash files and print a source list")
    listing.add_argument("--sport", choices=sorted(panel.RUBRICS), required=True)
    listing.add_argument("files", nargs="+", type=Path)
    run = sub.add_parser("run")
    run.add_argument("--sources", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--workspace", type=Path, required=True)
    run.add_argument("--trusted-source-root", type=Path, required=True)
    run.add_argument("--allow-mounted-ssd", action="store_true")
    run.add_argument("--ledger", type=Path)
    run.add_argument("--stages", nargs="+", choices=experiment.STAGES,
                     default=list(experiment.STAGES))
    run.add_argument("--models", nargs="+")
    run.add_argument("--budget", type=float)
    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            if len(args.files) > experiment.MAX_SOURCES:
                raise ValueError(f"At most {experiment.MAX_SOURCES} recordings per run")
            rows = [{"sourceSha256": experiment.source_digest(f), "path": str(f.absolute()),
                     "sport": args.sport} for f in args.files]
            print(json.dumps(rows, indent=2))
            return 0
        overrides = {}
        if args.models:
            overrides["models"] = args.models
        if args.budget is not None:
            overrides["budgetFraction"] = args.budget
        if "judge" in args.stages:
            models = overrides.get("models", experiment.DEFAULTS["models"])
            missing = experiment.missing_models(models)
            if missing:
                raise ValueError("Pull the judge models first: " +
                                 " && ".join(f"ollama pull {m}" for m in missing))
        report = experiment.run(
            args.sources, args.output, workspace=args.workspace,
            trusted_root=args.trusted_source_root, allow_mounted=args.allow_mounted_ssd,
            overrides=overrides, stages=tuple(args.stages), ledger_file=args.ledger,
            log=lambda message: print("[discovery]", message, file=sys.stderr, flush=True))
        print(json.dumps(report["totals"], indent=2))
        print(f"report: {args.output / 'report.json'}", file=sys.stderr)
    except (ValueError, KeyError, OSError, RuntimeError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

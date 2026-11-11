#!/usr/bin/env python3
"""Propose local video scene labels and provisional K-TIDE smoke assignments.

Default: no source-video reads, no Ollama calls. --run explicitly hashes and
decodes only selected original source files and sends sparse stills exclusively
to the local loopback Ollama API. Never modifies catalog or original media.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from adventure_intelligence.ktide_local_judge import (
    MODEL, merge_smoke_assignments, run_judge_pilot, select_judge_pilot,
)
from adventure_intelligence.ktide_study_contract import load_json
from adventure_intelligence.encoder_dataset import safe_training_workspace


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--inventory", required=True,
                        help="Private ktide-histogram-inventory.json, existing cache only")
    parser.add_argument("--run", action="store_true",
                        help="Opt in to source SHA reads, sparse decoding and local vision judge")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--frames", type=int, default=4)
    parser.add_argument("--group-gap-days", type=int, default=14)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--name", default="ktide-judge-pilot-001",
                        help="New private directory named ktide-judge-* under reports/")
    parser.add_argument("--existing-assignments",
                        help="Optional existing private JSON. Preserve verified owner labels")
    args = parser.parse_args()
    if not re.fullmatch(r"ktide-judge-[A-Za-z0-9_-]{4,50}", args.name):
        parser.error("--name must be a unique ktide-judge-* label")
    try:
        ws = safe_training_workspace(Path(args.workspace).expanduser())
        data, inventory_hash = load_json(Path(args.inventory).expanduser(),
                                         max_bytes=16_000_000)
        plan = select_judge_pilot(data, limit=args.limit,
                                  grouping_days=args.group_gap_days)
        existing = None
        if args.existing_assignments:
            existing, _ = load_json(Path(args.existing_assignments).expanduser(),
                                    max_bytes=2_000_000)
        if not args.run:
            result = {
                **plan,
                "inventoryFileSha256": inventory_hash,
                "selectedSourceIds": [r["sourceSha256"] for r in plan["selected"]],
                "humanTripIdentityConfirmed": False,
                "sourceVideoReadsPerformed": False,
                "modelCallsPerformed": 0,
                "nextStep": "Explicitly rerun with --run for sparse local visual proposals",
            }
        else:
            if not plan["selected"]:
                parser.error("No usable frozen feature caches in this inventory")
            output = ws / "reports" / args.name
            if (output.exists() or output.is_symlink()
                    or (ws / "reports").is_symlink()):
                parser.error("Refusing to overwrite an existing output or symlink")
            report = run_judge_pilot(ws, plan, model=args.model,
                                     frame_count=args.frames, existing=existing)
            output.mkdir(parents=True,mode=0o700)
            (output / "proposals.json").write_text(
                json.dumps({
                    k:v for k,v in report.items() if k!="smokeAssignments"
                },indent=2,sort_keys=True)+"\n")
            (output / "assignments-smoke.json").write_text(
                json.dumps(report["smokeAssignments"],indent=2,sort_keys=True)+"\n")
            result={
                "version":report["version"],
                "outputDirectory":str(output),
                "sourceCount":report["sourceCount"],
                "successes":report["successes"],
                "failures":report["sourceCount"]-report["successes"],
                "provisionalCaptureGroups":plan["provisionalCaptureGroups"],
                "groupingStatus":report["groupingStatus"],
                "scientificHeldoutEligible":False,
                "originalSourceVideoModified":False,
                "suggestedNextAction":(
                    "Use --allow-provisional-smoke for engineering training ONLY; "
                    "independent trips need verification for retrieval benchmarks."),
            }
    except (OSError,ValueError,UnicodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result,indent=2,sort_keys=True))
    return 0


if __name__=="__main__":
    raise SystemExit(main())

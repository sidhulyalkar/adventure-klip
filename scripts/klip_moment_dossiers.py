#!/usr/bin/env python3
"""Seed source-level scene dossiers from a previous private vision-judge report.

Reads only private JSON in the Klip workspace, never camera recordings.
Does not make new LLM calls, infer location/biomechanics, score personal
momentworthiness, restore footage, create edits or modify originals.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from adventure_intelligence.encoder_dataset import safe_training_workspace
from adventure_intelligence.ktide_study_contract import load_json
from adventure_intelligence.moment_dossier import make_dossier_seed_report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--judge-report", required=True,
                    help="Existing private ktide-judge-*/proposals.json")
    ap.add_argument("--name", default="moment-dossier-seed-001",
                    help="New directory under <workspace>/reports, never overwritten")
    args = ap.parse_args()
    if not re.fullmatch(r"moment-dossier-[A-Za-z0-9_-]{4,60}", args.name):
        ap.error("Use a unique moment-dossier-* name")
    try:
        root = safe_training_workspace(Path(args.workspace).expanduser())
        reports = root / "reports"
        if reports.is_symlink() or not reports.is_dir():
            raise ValueError("Workspace reports directory unavailable")
        input_path = Path(args.judge_report).expanduser()
        if input_path.is_symlink() or input_path.name != "proposals.json":
            raise ValueError("Require a normal private proposals.json file")
        resolved = input_path.resolve(strict=True)
        if not resolved.is_relative_to(reports):
            raise ValueError("Judge input must be inside workspace reports directory")
        output = reports / args.name
        if output.exists() or output.is_symlink():
            raise ValueError("Refusing to overwrite an existing private dossier directory")
        doc, _ = load_json(resolved, max_bytes=16_000_000)
        report = make_dossier_seed_report(doc)
        output.mkdir(mode=0o700)
        with (output / "dossiers.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except (OSError, ValueError, UnicodeError) as exc:
        ap.error(str(exc))
    print(json.dumps({
        "version": report["version"],
        "outputPath": str(output / "dossiers.json"),
        "sourceCount": report["sourceCount"],
        "hypothesizedSourceOverviews": report["sourceOverviewHypotheses"],
        "sourcesStillUnjudged": report["unknownOrFailedSources"],
        "eventLevelDescriptionsQualified": False,
        "sourceVideosRead": False,
        "modelCalled": False,
        "editorialRankingPerformed": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the judge panel over discovery folders that were scanned without it.

`klip_value_experiment.py index` deliberately skips the judge stage, so its batch
reports show judgedSegments 0. This command judges those folders afterwards from the
proxy clips already cut. It never opens camera originals: a recording that was not
scanned is reported as a failure, not hashed. Settings (models, frames) are the ones
frozen in each folder's experiment.json. Resumable; only missing answers are asked.

    # 1. See the backlog (no model calls, no Ollama needed)
    klip_judge_batches.py --reports "$WS/reports" --trusted-source-root /Volumes/Sid_DJI_SSD --dry-run

    # 2. Timing pilot: stop after 20 judge calls, then read the seconds-per-call it prints
    klip_judge_batches.py --reports "$WS/reports" --trusted-source-root /Volumes/Sid_DJI_SSD \\
        --folders discovery-011 --max-calls 20

    # 3. Full pass (rerun to resume)
    klip_judge_batches.py --reports "$WS/reports" --trusted-source-root /Volumes/Sid_DJI_SSD
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys

from adventure_intelligence import discovery_experiment as experiment


def _folders(reports: Path, names: list[str] | None) -> list[Path]:
    found = sorted(p for p in reports.glob("discovery-*")
                   if (p / "experiment.json").is_file() and (p / "sources.json").is_file())
    if names:
        wanted = set(names)
        found = [p for p in found if p.name in wanted]
        missing = wanted - {p.name for p in found}
        if missing:
            raise ValueError("Not a discovery folder with sources.json: " + ", ".join(sorted(missing)))
    return found


def _seconds_per_call(folders: list[Path]) -> dict[str, dict]:
    times: dict[str, list[float]] = {}
    for folder in folders:
        for path in folder.glob("*/judgments/*.json"):
            for j in json.loads(path.read_text(encoding="utf-8"))["judgments"]:
                if "elapsedSeconds" in j:
                    times.setdefault(j["model"], []).append(j["elapsedSeconds"])
    return {m: {"calls": len(t), "medianSeconds": round(median(t), 1)}
            for m, t in times.items()}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reports", type=Path, required=True,
                        help="folder holding discovery-NNN batches")
    parser.add_argument("--trusted-source-root", type=Path, required=True)
    parser.add_argument("--folders", nargs="+", help="only these batch folders")
    parser.add_argument("--dry-run", action="store_true", help="count the backlog only")
    parser.add_argument("--max-calls", type=int, help="stop after this many judge calls")
    args = parser.parse_args(argv)
    try:
        folders = _folders(args.reports.expanduser(), args.folders)
        if not folders:
            raise ValueError(f"No discovery folders under {args.reports}")
        backlog, models = {}, set()
        for folder in folders:
            frozen = json.loads((folder / "experiment.json").read_text(encoding="utf-8"))
            rows = experiment.load_sources(folder / "sources.json")
            models |= set(frozen["config"]["models"])
            backlog[folder.name] = experiment.judge_workload(folder, rows,
                                                             frozen["config"]["models"])
        total = {k: sum(b[k] for b in backlog.values()) for k in
                 ("segmentsForReview", "segmentsSkippedUnknownSport", "segmentsMissingProxy",
                  "judgeCallsExpected", "judgeCallsAnswered", "judgeCallsLeft")}
        by_sport: dict[str, dict] = {}
        for b in backlog.values():
            for sport, n in b["bySport"].items():
                agg = by_sport.setdefault(sport, {"segments": 0, "callsLeft": 0})
                agg["segments"] += n["segments"]
                agg["callsLeft"] += n["callsLeft"]
        print(json.dumps({"folders": len(folders), "models": sorted(models), **total,
                          "bySport": by_sport}, indent=2))
        if args.dry_run:
            return 0
        missing = experiment.missing_models(sorted(models))
        if missing:
            raise ValueError("Pull the judge models first: " +
                             " && ".join(f"ollama pull {m}" for m in missing))
        budget = [args.max_calls] if args.max_calls is not None else None
        workspace = args.reports.expanduser().absolute().parent
        for folder in folders:
            if backlog[folder.name]["judgeCallsLeft"] == 0:
                continue
            if budget is not None and budget[0] <= 0:
                break
            print(f"[judge] {folder.name}: {backlog[folder.name]['judgeCallsLeft']} calls left",
                  file=sys.stderr, flush=True)
            report = experiment.run(
                folder / "sources.json", folder, workspace=workspace,
                trusted_root=args.trusted_source_root, allow_mounted=True,
                stages=("judge",), judge_budget=budget,
                log=lambda m: print("[judge]", m, file=sys.stderr, flush=True))
            t = report["totals"]
            print(json.dumps({"folder": folder.name, **{k: t[k] for k in (
                "judgeCallsAnswered", "judgeCallsLeft", "judgeAbstentions",
                "judgeControlFailures", "segmentsWithDisputedFacts", "failedSources")}}))
        print(json.dumps({"secondsPerCall": _seconds_per_call(folders)}, indent=2))
    except (ValueError, KeyError, OSError, RuntimeError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

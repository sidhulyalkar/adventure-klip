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

Recordings indexed before their activity was tagged are frozen as "unknown" in each
batch's sources.json and get no judge questions. Pass --roster to take the sport from
the roster's current tags instead: the batch's sources.json is never edited; the
relabelled list is written beside it as judge-sources.json, with judge-sources.provenance.json
saying where each sport came from. Recordings still unknown are listed by --dry-run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
import sys

from adventure_intelligence import discovery_experiment as experiment
from adventure_intelligence import outing_roster as roster


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


def _relabel(folder: Path, rows: list[dict], tagged: dict[str, str], roster_file: Path | None,
             write: bool) -> tuple[Path, list[dict]]:
    """Rows with sport from the roster where the batch froze "unknown"; never edits sources.json."""
    if roster_file is None:
        # A relabelled list from an earlier --roster run keeps being used, so a resumed
        # pass does not silently drop back to "unknown".
        earlier = folder / "judge-sources.json"
        if earlier.is_file():
            return earlier, experiment.load_sources(earlier)
        return folder / "sources.json", rows
    out, provenance = [], {}
    for row in rows:
        sport, origin = row["sport"], "sources.json"
        if sport == experiment.UNKNOWN_SPORT and row["sourceSha256"] in tagged:
            sport, origin = tagged[row["sourceSha256"]], "roster"
            # A recording already judged under another rubric must not be mixed.
            for path in (folder / row["sourceSha256"] / "judgments").glob("*.json"):
                judged = json.loads(path.read_text(encoding="utf-8"))["judgments"]
                if any(j["sport"] != sport for j in judged):
                    raise ValueError(f"{folder.name}/{row['sourceSha256'][:12]} was judged as "
                                     f"another sport; not relabelling it to {sport}")
        out.append({**row, "sport": sport})
        provenance[row["sourceSha256"]] = origin
    target = folder / "judge-sources.json"
    if write:
        experiment._atomic_json(target, out)
        experiment._atomic_json(folder / "judge-sources.provenance.json", {
            "roster": str(roster_file),
            "rosterSha256": experiment.source_digest(roster_file),
            "sportFrom": provenance})
    return target, out


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
    parser.add_argument("--roster", type=Path,
                        help="take sports from this roster for recordings frozen as unknown")
    args = parser.parse_args(argv)
    try:
        tagged, activity = {}, {}
        if args.roster:
            doc = roster.validate(json.loads(args.roster.read_text(encoding="utf-8")))
            tagged, activity = roster.sport_by_source(doc), roster.activity_by_source(doc)
        folders = _folders(args.reports.expanduser(), args.folders)
        if not folders:
            raise ValueError(f"No discovery folders under {args.reports}")
        backlog, models, inputs, still_unknown, relabelled = {}, set(), {}, [], 0
        no_rubric: dict[str, int] = {}  # tagged kayak/hike/...: no judge questions, by design
        for folder in folders:
            frozen = json.loads((folder / "experiment.json").read_text(encoding="utf-8"))
            original = experiment.load_sources(folder / "sources.json")
            inputs[folder.name], rows = _relabel(folder, original, tagged, args.roster,
                                                 write=not args.dry_run)
            relabelled += sum(a["sport"] != b["sport"] for a, b in zip(original, rows))
            models |= set(frozen["config"]["models"])
            backlog[folder.name] = experiment.judge_workload(folder, rows,
                                                             frozen["config"]["models"])
            for row in rows:
                plan = experiment._read(folder / row["sourceSha256"] / "segments.json")
                chosen = sum(s["role"] != "not_reviewed" for s in (plan or {}).get("segments", []))
                own = activity.get(row["sourceSha256"], roster.UNLABELLED)
                if row["sport"] == experiment.UNKNOWN_SPORT and chosen and own != roster.UNLABELLED:
                    no_rubric[own] = no_rubric.get(own, 0) + chosen
                elif row["sport"] == experiment.UNKNOWN_SPORT and chosen:
                    still_unknown.append({"folder": folder.name, "sourceSha256": row["sourceSha256"],
                                          "file": Path(row["path"]).name,
                                          "reviewSegments": chosen})
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
                          "bySport": by_sport,
                          "recordingsRelabelledFromRoster": relabelled,
                          "segmentsTaggedWithoutJudgeRubric": no_rubric,
                          "recordingsStillUnknown": len(still_unknown)}, indent=2))
        if args.dry_run:
            if still_unknown:
                print("untagged in the roster (tag these; only ski, snorkeling and bike get "
                      "judge questions):", file=sys.stderr)
                for u in still_unknown:
                    print(f"  {u['folder']}  {u['file']}  {u['sourceSha256'][:12]}  "
                          f"{u['reviewSegments']} segments", file=sys.stderr)
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
                inputs[folder.name], folder, workspace=workspace,
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

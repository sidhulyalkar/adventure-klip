#!/usr/bin/env python3
"""Tag micro-recordings (accidental button presses) as accidental in the roster.

Rule, chosen by the owner: a camera original whose whole duration (from the index's
source.json) is under --max-seconds is an accidental recording. Each one gets activity
"accidental" (which removes it from every later run) and the tag "micro-recording", so the
reason stays visible and the rule can be undone. Recordings the owner has tagged with
another activity are left alone and listed, never overwritten.

Every roster given is updated in place after a timestamped backup beside it, and a log of
what changed (file, duration, previous activity) is written next to the first roster.

    klip_tag_micro_recordings.py --reports "$WS/reports" \\
        --roster "$WS/reports/owner-ledgers/roster.json" \\
        --roster "$WS/reports/owner-ledgers/roster.guessed-2026-11-20.json" --dry-run
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

from adventure_intelligence import outing_roster as roster

TAG = "micro-recording"


def micro_recordings(reports: Path, max_ms: int) -> dict[str, dict]:
    """sha -> {file, durationMs, folder} for every indexed recording shorter than max_ms."""
    out = {}
    for folder in sorted(reports.glob("discovery-*")):
        try:
            rows = json.loads((folder / "sources.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for row in rows:
            try:
                source = json.loads((folder / row["sourceSha256"] / "source.json")
                                    .read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if source.get("durationMs") is not None and source["durationMs"] < max_ms:
                out[row["sourceSha256"]] = {"file": Path(row["path"]).name,
                                            "durationMs": source["durationMs"],
                                            "folder": folder.name}
    return out


def tag(doc: dict, micro: dict[str, dict]) -> tuple[dict, list[dict], list[dict]]:
    """(updated copy, changes, left alone). The input document is not modified."""
    doc = json.loads(json.dumps(roster.validate(doc)))
    changes, kept = [], []
    for outing in doc["outings"]:
        for row in outing["sources"]:
            info = micro.get(row["sourceSha256"])
            if info is None:
                continue
            before = row.get("activity", roster.UNLABELLED)
            owner_tags = set(row.get("tags") or []) - {roster.GUESS_TAG,
                                                       roster.MAYBE_ACCIDENTAL_TAG, TAG}
            guessed = roster.GUESS_TAG in (row.get("tags") or [])
            if before not in (roster.UNLABELLED, roster.EXCLUDED) and not guessed:
                kept.append({**info, "sourceSha256": row["sourceSha256"],
                             "ownerActivity": before})   # the owner's own label wins
                continue
            if before == roster.EXCLUDED and TAG in (row.get("tags") or []):
                continue
            tags = [t for t in (row.get("tags") or []) if t not in (roster.GUESS_TAG,
                                                                    roster.MAYBE_ACCIDENTAL_TAG)]
            row["activity"] = roster.EXCLUDED
            row["tags"] = sorted(set(tags) | {TAG})
            changes.append({**info, "sourceSha256": row["sourceSha256"],
                            "previousActivity": before, "wasModelGuess": guessed,
                            "ownerTags": sorted(owner_tags)})
    return roster.validate(doc), changes, kept


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--roster", type=Path, action="append", required=True)
    parser.add_argument("--max-seconds", type=float, default=2.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        micro = micro_recordings(args.reports.expanduser(), int(args.max_seconds * 1000))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        summary = {"microRecordings": len(micro), "rule": f"duration < {args.max_seconds} s",
                   "rosters": []}
        log_entries = []
        for path in args.roster:
            path = path.expanduser()
            updated, changes, kept = tag(json.loads(path.read_text(encoding="utf-8")), micro)
            in_roster = {r["sourceSha256"] for o in updated["outings"] for r in o["sources"]}
            summary["rosters"].append({
                "roster": str(path), "tagged": len(changes), "ownerLabelKept": kept,
                "notInRoster": sorted(v["file"] for s, v in micro.items() if s not in in_roster)})
            if not args.dry_run and changes:
                shutil.copy2(path, path.with_name(f"{path.name}.bak-{stamp}"))
                tmp = path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(updated, indent=2), encoding="utf-8")
                tmp.replace(path)
            log_entries.append({"roster": str(path), "changes": changes, "ownerLabelKept": kept})
        for m in sorted(micro.values(), key=lambda v: v["file"]):
            print(f"  {m['folder']}  {m['file']}  {m['durationMs'] / 1000:.2f} s", file=sys.stderr)
        if not args.dry_run:
            log = args.roster[0].expanduser().with_name(f"micro-recordings-{stamp}.json")
            log.write_text(json.dumps({"rule": summary["rule"], "decidedBy": "owner",
                                       "at": stamp, "rosters": log_entries}, indent=2),
                           encoding="utf-8")
            summary["log"] = str(log)
        print(json.dumps(summary, indent=2))
    except (ValueError, OSError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

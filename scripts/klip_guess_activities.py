#!/usr/bin/env python3
"""Guess the activity of untagged recordings, as a separate roster for the judge panel only.

Evidence per recording, all already on disk (no video is opened, no model is run):
  - frames: the description store's whole-recording activity vote (SigLIP2 frames vs
    activity phrases; share = fraction of frames that voted for the winner);
  - day:    the owner's own tags on other recordings from the same capture day (DJI name).

Rules (recorded in the evidence file):
  frames and day agree                          -> high
  frames only, share >= --min-share             -> medium
  frames vs day disagree, or a weak frames vote -> low (frames win if strong, else day)
  day only                                      -> low
  frames say "accidental"                       -> never applied (that would hide the
                                                   recording from everything); listed instead
  no evidence                                   -> left unknown

Every guessed recording is tagged activity-guessed. The roster passed in is never edited;
the guessed copy is refused by every evaluation command (outing_roster.refuse_guesses), so
a guess can steer which judge questions are asked but never counts as an owner label.
To confirm a guess, keep the activity and remove the tag (e.g. in the Organizer).

    klip_guess_activities.py --workspace "$WS" --roster "$WS/reports/owner-ledgers/roster.json"
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import sys

from adventure_intelligence import outing_roster as roster

VERSION = "klip-activity-guess-v1"
_DAY = re.compile(r"DJI_(\d{8})")


def _names(workspace: Path) -> dict[str, str]:
    """sha -> file name, from the catalog (read-only) and discovery source lists."""
    out = {}
    for f in (workspace / "reports").glob("discovery-*/sources.json"):
        for row in json.loads(f.read_text(encoding="utf-8")):
            out[row["sourceSha256"]] = Path(row["path"]).name
    database = workspace / "catalog.sqlite3"
    if database.is_file() and not database.is_symlink():
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
            db.execute("PRAGMA query_only=ON")
            for sha, path in db.execute("SELECT sha256, stored_path FROM assets"):
                out.setdefault(sha, Path(path).name)
    return out


def guess(doc: dict, names: dict[str, str], store, *, min_share: float = 0.6) -> dict:
    """{sha: record} for every untagged recording; pure, so it is testable without media."""
    doc = roster.validate(doc)
    guessed = roster.guessed_sources(doc)

    def day(sha):
        m = _DAY.search(names.get(sha, ""))
        return m.group(1) if m else None

    owner_days: dict[str, Counter] = {}
    for outing in doc["outings"]:
        for row in outing["sources"]:
            act = row.get("activity", roster.UNLABELLED)
            if (act not in (roster.UNLABELLED, roster.EXCLUDED)
                    and row["sourceSha256"] not in guessed and day(row["sourceSha256"])):
                owner_days.setdefault(day(row["sourceSha256"]), Counter())[act] += 1

    out = {}
    for outing in doc["outings"]:
        for row in outing["sources"]:
            sha = row["sourceSha256"]
            if row.get("activity", roster.UNLABELLED) != roster.UNLABELLED or sha in guessed:
                continue
            frames = store.activity(sha) if store is not None else None
            tags = owner_days.get(day(sha))
            day_top = tags.most_common(1)[0][0] if tags else None
            rec = {"file": names.get(sha), "day": day(sha), "outing": outing["outing"],
                   "frames": frames and {k: frames[k] for k in ("proposal", "share", "votes")},
                   "ownerTagsSameDay": dict(tags) if tags else {}}
            proposal = frames["proposal"] if frames else None
            strong = bool(frames) and frames["share"] >= min_share
            if proposal == roster.EXCLUDED:
                rec.update(guess=day_top, confidence="low" if day_top else None,
                           possibleAccidental=True,
                           reason="frames look accidental; not applied" +
                                  ("; day tag used" if day_top else ""))
            elif proposal and day_top == proposal:
                rec.update(guess=proposal, confidence="high", reason="frames and day agree")
            elif proposal and not day_top:
                rec.update(guess=proposal, confidence="medium" if strong else "low",
                           reason="frames only" + ("" if strong else " (weak vote)"))
            elif proposal:
                pick = proposal if strong else day_top
                rec.update(guess=pick, confidence="low",
                           reason=f"frames say {proposal}, same-day tags say {day_top}; "
                                  f"{'frames' if strong else 'day'} used")
            elif day_top:
                rec.update(guess=day_top, confidence="low", reason="day only (not described)")
            else:
                rec.update(guess=None, confidence=None, reason="no evidence")
            out[sha] = rec
    return out


def apply(doc: dict, records: dict) -> dict:
    """A copy of the roster with guesses filled in and tagged; the input is not changed."""
    doc = json.loads(json.dumps(doc))
    for outing in doc["outings"]:
        for row in outing["sources"]:
            rec = records.get(row["sourceSha256"])
            if rec and rec["guess"]:
                row["activity"] = rec["guess"]
                tags = list(row.get("tags") or [])
                if roster.GUESS_TAG not in tags:
                    if len(tags) >= 30:
                        raise ValueError(f"{row['sourceSha256'][:12]} already has 30 tags")
                    row["tags"] = tags + [roster.GUESS_TAG]
    return roster.validate(doc)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--roster", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, help="description store "
                        "(default WORKSPACE/reports/evidence)")
    parser.add_argument("--output", type=Path, help="guessed roster "
                        "(default roster.guessed-<today>.json beside the input)")
    parser.add_argument("--min-share", type=float, default=0.6)
    args = parser.parse_args(argv)
    try:
        workspace = args.workspace.expanduser()
        text = args.roster.read_text(encoding="utf-8")
        doc = json.loads(text)
        store = None
        folder = (args.evidence or workspace / "reports" / "evidence").expanduser()
        if (folder / "vocabulary.json").is_file():
            from adventure_intelligence.moment_evidence import Store
            store = Store(folder)
        else:
            print(f"note: no description store at {folder}; day tags only", file=sys.stderr)
        records = guess(doc, _names(workspace), store, min_share=args.min_share)
        out = args.output or args.roster.with_name(f"roster.guessed-{date.today()}.json")
        if out.resolve() == args.roster.resolve():
            raise ValueError("Write the guesses to a new file, not over the roster")
        guessed_doc = apply(doc, records)
        out.write_text(json.dumps(guessed_doc, indent=2), encoding="utf-8")
        evidence = out.with_suffix(".evidence.json")
        evidence.write_text(json.dumps({
            "version": VERSION, "isModelProposalNotOwnerJudgment": True,
            "inputRoster": str(args.roster), "inputRosterSha256": sha256(text.encode()).hexdigest(),
            "evidenceStore": str(folder) if store else None,
            "vocabulary": store.doc["digest"] if store else None,
            "imageModel": store.doc["imageModel"] if store else None,
            "minShare": args.min_share, "recordings": records}, indent=2), encoding="utf-8")
        by_day: dict[str, Counter] = {}
        for rec in records.values():
            c = by_day.setdefault(rec["day"] or "no date", Counter())
            c[f'{rec["guess"] or "unknown"}/{rec["confidence"] or "-"}'] += 1
        for d in sorted(by_day):
            print(f"{d}  " + ", ".join(f"{k} {v}" for k, v in sorted(by_day[d].items())))
        conf = Counter(r["confidence"] or "none" for r in records.values())
        print(json.dumps({"untagged": len(records),
                          "guessed": sum(bool(r["guess"]) for r in records.values()),
                          "byConfidence": dict(conf),
                          "possibleAccidental": sum(bool(r.get("possibleAccidental"))
                                                    for r in records.values()),
                          "roster": str(out), "evidence": str(evidence)}, indent=2))
    except (ValueError, OSError, KeyError, json.JSONDecodeError, sqlite3.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""The highlights library: every moment you have judged worth keeping, in one place.

    build           gather grid keeps and stars, tap preferences, gallery trims, highlights-
                    page answers and (optionally) verified search proposals into moments
    top             the best moments so far, per sport
    page            one review page of the library (rate 1-5, trim, tick what it shows, note)
    import-answers  store that page's answers; the next build picks them up

Reads the ledgers and the review clips; never writes a ledger except through
import-answers (append-only owner labels), and never opens camera originals.

    R="$WS/reports"; L="$R/owner-ledgers"
    $PY scripts/klip_highlight_library.py build --reports "$R" --ledgers "$L"
    $PY scripts/klip_highlight_library.py top --reports "$R"
    $PY scripts/klip_highlight_library.py page --reports "$R"
    $PY scripts/klip_highlight_library.py import-answers --reports "$R" --answers ~/Downloads/highlight-answers-<deck>.json
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from adventure_intelligence import highlight_library as lib
from adventure_intelligence import highlight_review as review
from adventure_intelligence import holdout
from adventure_intelligence import outing_roster as roster
from adventure_intelligence import owner_labels
from adventure_intelligence import run_records
from adventure_intelligence import scanned_recordings
from adventure_intelligence import value_pairs

FOLDER = "highlight-library-v1"
SEARCH = "highlight-search-v1"


def _read(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(doc if isinstance(doc, str) else json.dumps(doc, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def discovery(reports: Path) -> dict[str, dict]:
    """sha -> {file, sport, proxies: [(start, end, path)]}, as the review app finds them."""
    return scanned_recordings.scanned(reports)


def search_media(reports: Path) -> tuple[list[dict], dict[str, dict]]:
    """The latest search run's candidates and every stage B result (any model)."""
    home = Path(reports) / SEARCH
    pointer = _read(home / "latest.json")
    doc = _read(home / "runs" / pointer["run"] / "candidates.json") if pointer else None
    verified = {}
    for f in sorted((home / "verify").glob("*/*.json")) if (home / "verify").is_dir() else []:
        r = _read(f)
        if r and r.get("candidateId"):
            verified[r["candidateId"]] = r
    return (doc or {}).get("candidates", []), verified


def context(reports: Path, roster_doc: dict | None, disc: dict) -> dict[str, dict]:
    held = holdout.held_out(reports)
    activity = roster.activity_by_source(roster_doc) if roster_doc else {}
    outing = roster.outing_by_source(roster_doc) if roster_doc else {}
    tags = {}
    for o in (roster_doc or {}).get("outings", []):
        for row in o["sources"]:
            tags[row["sourceSha256"]] = row.get("tags") or []
    cat = scanned_recordings.catalog(Path(reports).parent)
    out = {}
    for sha in set(disc) | set(activity) | set(cat):
        file = (disc.get(sha) or {}).get("file") or (
            Path(cat[sha]["path"]).name if cat.get(sha, {}).get("path") else None)
        sport = activity.get(sha)
        if sport in (None, roster.UNLABELLED):
            sport = (disc.get(sha) or {}).get("sport")
        out[sha] = {"sport": sport, "outing": outing.get(sha), "file": file,
                    "day": holdout.day_of(file or ""), "heldOut": sha in held,
                    "tags": tags.get(sha, [])}
    return out


def cmd_build(args) -> dict:
    ledgers = Path(args.ledgers or Path(args.reports) / "owner-ledgers").expanduser()
    pairs = value_pairs.load(ledgers / "owner-pairs.jsonl")
    roster_file = ledgers / "roster.json"
    roster_doc = roster.validate(_read(roster_file)) if roster_file.is_file() else None
    evidence = (lib.grid_evidence(pairs) + lib.tap_evidence(pairs)
                + lib.boundary_evidence(owner_labels.load(args.reports, "boundary"))
                + lib.review_evidence(owner_labels.load(args.reports, "highlight")))
    if not args.owner_only:
        candidates, verified = search_media(args.reports)
        evidence += lib.proposal_evidence(candidates, verified)
    disc = discovery(args.reports)
    library = lib.build(evidence, context(args.reports, roster_doc, disc))
    library["inputs"] = {"pairsLedger": str(ledgers / "owner-pairs.jsonl"),
                         "pairsRows": len(pairs), "roster": roster_doc is not None}
    home = Path(args.reports) / FOLDER
    _write(home / "snapshots" / f'{library["evidenceSha256"][:12]}.json', library)
    _write(home / "library.json", library)
    top = lib.ranked(library, limit=10)
    return {"counts": library["counts"], "perOuting": library["perOuting"],
            "top": [_line(m) for m in top], "file": str(home / "library.json")}


def _line(m: dict) -> str:
    stars = f' ★{m["stars"]}' if m.get("stars") else ""
    what = f' [{", ".join(m["what"])}]' if m.get("what") else ""
    flag = " (conflict)" if m.get("conflict") else ""
    return (f'{m["tier"]}{stars} {m.get("sport")} {m.get("outing") or m.get("day")} '
            f'{m.get("file")} {m["startMs"] / 1000:.0f}-{m["endMs"] / 1000:.0f}s{what}{flag}')


def _library(reports: Path) -> dict:
    doc = _read(Path(reports) / FOLDER / "library.json")
    if not doc:
        raise ValueError("No library yet: run build first")
    return doc


def cmd_top(args) -> dict:
    library = _library(args.reports)
    sports = [args.sport] if args.sport else sorted(library["counts"]["bySport"])
    return {s: [_line(m) for m in lib.ranked(library, sport=s, limit=args.limit)] for s in sports}


def media_finder(reports: Path):
    disc = discovery(reports)
    candidates, _ = search_media(reports)
    clips = Path(reports) / SEARCH / "clips"
    cut = [(c["sourceSha256"], c["startMs"], c["endMs"], clips / f'{c["id"]}.mp4')
           for c in candidates if (clips / f'{c["id"]}.mp4').is_file()]

    def media_of(m: dict):
        sha, s, e = m["sourceSha256"], m["startMs"], m["endMs"]
        options = [(a, b, p) for a, b, p in (disc.get(sha) or {}).get("proxies", [])]
        options += [(a, b, p) for x, a, b, p in cut if x == sha]
        covering = [o for o in options if o[0] <= s and e <= o[1]]
        if covering:
            a, b, p = min(covering, key=lambda o: o[1] - o[0])
            return p, a, b
        partial = [o for o in options if o[0] < e and s < o[1]]
        if partial:            # shows what exists of the moment; the page plays that part
            a, b, p = max(partial, key=lambda o: min(o[1], e) - max(o[0], s))
            return p, a, b
        return None
    return media_of


def cmd_page(args) -> dict:
    library = _library(args.reports)
    tiers = tuple(args.tiers)
    deck = lib.deck(library, media_finder(args.reports), tiers=tiers)
    # Keep the moment's window inside what its clip actually covers.
    for item in deck["items"].values():
        item["startMs"] = max(item["startMs"], item["mediaStartMs"])
        item["endMs"] = min(item["endMs"], item["mediaEndMs"])
    out = Path(args.reports) / FOLDER / "review" / deck["deck"]
    _write(out / "deck.json", deck)
    media = deck["media"]
    page = review.page(deck, out, lambda i: Path(media[i]) if i in media else None)
    _write(out / "index.html", page)
    return {"deck": deck["deck"], "moments": len(deck["items"]), "byTier": deck["counts"],
            "withoutClip": sum(1 for i in deck["items"] if i not in media),
            "page": str(out / "index.html")}


def cmd_import(args) -> dict:
    raw = Path(args.answers).expanduser().read_bytes()
    answers = json.loads(raw)
    deck = _read(Path(args.reports) / FOLDER / "review" / str(answers.get("deck")) / "deck.json")
    if not deck:
        raise ValueError(f"No library page {answers.get('deck')}: were these answers from the "
                         "highlights review page instead? Use klip_highlight_search.py import-answers")
    return review.import_answers(args.reports, deck, answers, raw)


COMMANDS = {"build": cmd_build, "top": cmd_top, "page": cmd_page, "import-answers": cmd_import}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        s = sub.add_parser(name)
        s.add_argument("--reports", type=Path, required=True)
        if name == "build":
            s.add_argument("--ledgers", type=Path)
            s.add_argument("--owner-only", action="store_true",
                           help="leave out verified search proposals")
        if name == "top":
            s.add_argument("--sport")
            s.add_argument("--limit", type=int, default=20)
        if name == "page":
            s.add_argument("--tiers", nargs="+", default=["owner_best", "owner_keep", "owner_maybe"],
                           choices=lib.TIERS)
        if name == "import-answers":
            s.add_argument("--answers", type=Path, required=True)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        result = COMMANDS[args.command](args)
    except (ValueError, OSError, KeyError) as error:
        print(f"[library] stopped: {error}", file=sys.stderr)
        return 3
    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_records.main_recorded(main, "klip_highlight_library"))

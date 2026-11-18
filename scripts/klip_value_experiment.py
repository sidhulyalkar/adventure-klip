#!/usr/bin/env python3
"""Rank an outing's footage by what the owner would keep, and check it against the owner.

Never opens video and never calls a model. Reads the catalog and reports
read-only; writes only the files each command names. Typical order:

    klip_value_experiment.py roster  --workspace "$WS" > roster.json   # check, then confirm it
    klip_value_experiment.py harvest --workspace "$WS" --ledger owner-evidence.jsonl --roster roster.json
    klip_value_experiment.py plan    --workspace "$WS" --ledger owner-evidence.jsonl --roster roster.json \\
        > sources.json                                                  # input for the discovery run
    klip_value_experiment.py deck    --experiment "$WS/reports/discovery-001" --workspace "$WS" --roster roster.json
    klip_value_experiment.py import-answers --deck <deck.json> --answers <export.json> \\
        --pairs-ledger owner-pairs.jsonl
    klip_value_experiment.py evaluate --experiment "$WS/reports/discovery-001" --workspace "$WS" \\
        --ledger owner-evidence.jsonl --pairs-ledger owner-pairs.jsonl --roster roster.json \\
        --output "$WS/reports/value-001"
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

from adventure_intelligence import evidence_ledger as ledger
from adventure_intelligence import outing_roster as roster
from adventure_intelligence import owner_evidence
from adventure_intelligence import value_pairs as pairs_mod

SPORTS = ("ski", "snorkeling", "bike")
DEFAULT_WORKSPACE = Path(os.environ.get("ADVENTURE_HOME") or Path.home() / "AdventureAI")


def _json(path: Path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing or symlinked input: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise ValueError(f"{path} is empty or not JSON; rerun the step that writes it") from None


def _workspace(path: Path) -> Path:
    """The Klip data folder (holds catalog.sqlite3), not the code checkout."""
    path = path.expanduser()
    if not (path / "catalog.sqlite3").is_file():
        hint = (" This looks like the code checkout." if (path / "pyproject.toml").is_file() else "")
        raise ValueError(
            f"No catalog.sqlite3 in {path}.{hint} Use the folder you pass to "
            f"`klip --workspace` (default {DEFAULT_WORKSPACE}). To find it: "
            "find ~ /Volumes -maxdepth 5 -name catalog.sqlite3 2>/dev/null")
    return path


def _emit(doc, output: Path | None) -> None:
    """Print, or write the file only once the result exists, so a failure leaves nothing."""
    text = json.dumps(doc, indent=2) + "\n"
    if output is None:
        sys.stdout.write(text)
        return
    output = output.expanduser()
    if output.exists() and not getattr(_emit, "overwrite", False):
        raise ValueError(f"{output} exists; pass --force to replace it")
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".partial")
    temp.write_text(text, encoding="utf-8")
    temp.replace(output)
    print(f"wrote {output}", file=sys.stderr)


def _mapping(args):
    if not getattr(args, "roster", None):
        return {}, False
    doc = roster.validate(_json(args.roster))
    return roster.outing_by_source(doc), doc["status"] == "owner_confirmed"


def _owner(args, group_of):
    """All owner judgments and every pair derived from them."""
    rows = ledger.load(args.ledger) if getattr(args, "ledger", None) else []
    answers = (pairs_mod.load(args.pairs_ledger)
               if getattr(args, "pairs_ledger", None) else [])
    collected = owner_evidence.collect(getattr(args, "workspace", None), rows,
                                       [r.expanduser() for r in getattr(args, "reports", None) or []])
    grid = pairs_mod.grid_judgments(answers)
    judgments = collected["judgments"] + grid
    pairs = (pairs_mod.from_ledger(collected["judgments"], group_of, seed=args.seed)
             + pairs_mod.from_ratings(collected["judgments"], group_of, seed=args.seed)
             + pairs_mod.from_direct(collected["pairs"], group_of)
             + pairs_mod.from_taps(answers, group_of)
             + pairs_mod.from_grid(answers, group_of))
    return {"collected": collected, "answers": answers, "judgments": judgments,
            "pairs": pairs, "rows": rows}


def _load(args):
    from adventure_intelligence import value_eval, value_features as features
    sources, duplicates = {}, 0
    for folder in args.experiment:
        for sha, source in features.load_experiment(folder).items():
            if sha in sources:
                duplicates += 1  # first experiment listed wins; never mix two runs of one source
            else:
                sources[sha] = source
    mapping, confirmed = _mapping(args)

    def group_of(sha):
        return roster.group_of(mapping, sha)

    owner = _owner(args, group_of)
    embeddings = (features.attach_embeddings(sources, args.workspace)
                  if args.workspace and (args.workspace / "catalog.sqlite3").is_file() else None)
    pool = features.segment_intervals(sources)
    taps = [r for r in owner["answers"] if "left" in r]
    items = features.build_items(
        sources, pool + value_eval.judgment_intervals(owner["judgments"])
        + value_eval.pair_intervals(owner["collected"]["pairs"] + taps)
        + value_eval.grid_intervals(owner["answers"]), group_of, seed=args.seed)
    pool_ids = {features.item_id(*interval) for interval in pool} & set(items)
    labelled = {j["sourceSha256"] for j in owner["judgments"]}
    return {**owner, "sources": sources, "items": items, "pool_ids": pool_ids,
            "confirmed": confirmed, "embeddings": embeddings, "duplicates": duplicates,
            "group_of": group_of,
            "labelledSourcesWithoutEvidence": len(labelled - set(sources))}


def _catalog_assets(workspace: Path) -> dict[str, dict]:
    database = workspace / "catalog.sqlite3"
    if database.is_symlink() or not database.is_file():
        raise ValueError("No catalog in this workspace")
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        return {r[0]: {"path": r[1], "activity": r[2], "duration": r[3]} for r in db.execute(
            "SELECT sha256,stored_path,activity,duration FROM assets WHERE duration>0")}


def _done(folders: list[Path]) -> tuple[set[str], set[str]]:
    """(scanned, attempted). Scanned means the motion timeline exists; a recording
    that failed was attempted but gives no evidence, so its pairs stay unusable."""
    scanned, attempted = set(), set()
    for folder in folders or []:
        for sha in _json(folder / "experiment.json")["sources"]:
            attempted.add(sha)
            if (folder / sha / "motion.json").is_file():
                scanned.add(sha)
    return scanned, attempted


def harvest(args) -> dict:
    mapping, confirmed = _mapping(args)

    def group_of(sha):
        return roster.group_of(mapping, sha)

    owner = _owner(args, group_of)
    by_origin: dict[str, dict] = {}
    for j in owner["judgments"]:
        row = by_origin.setdefault(j["origin"], {"keep": 0, "skip": 0, "unsure": 0,
                                                 "ownerMarkedSpans": 0, "withRatings": 0})
        row[j["decision"]] += 1
        row["ownerMarkedSpans"] += j["selection"] == "owner_marked_span"
        row["withRatings"] += bool(j["ratings"])
    by_outing: dict[str, dict] = {}
    for p in owner["pairs"]:
        for g in p["groups"]:
            by_outing.setdefault(g, {"pairs": 0, "judgments": 0})["pairs"] += 1
    for j in owner["judgments"]:
        by_outing.setdefault(group_of(j["sourceSha256"]), {"pairs": 0, "judgments": 0})[
            "judgments"] += 1
    tiers: dict[str, dict] = {}
    for p in owner["pairs"]:
        t = tiers.setdefault(p["tier"], {"pairs": 0, "scoreable": 0})
        t["pairs"] += 1
        t["scoreable"] += p["evalEligible"]
    unused = [f for f in owner["collected"]["files"] if not f["used"]]
    return {
        "rosterConfirmed": confirmed,
        "counts": owner["collected"]["counts"],
        "judgmentsByOrigin": by_origin, "pairsByTier": tiers,
        "outings": {"withAnyLabel": sum(not g.startswith("source:") for g in by_outing),
                    "recordingsWithoutOuting": sum(g.startswith("source:") for g in by_outing),
                    "byOuting": dict(sorted(by_outing.items(),
                                            key=lambda kv: -kv[1]["judgments"] - kv[1]["pairs"]))},
        "reviewFiles": {"consumed": sum(f["used"] for f in owner["collected"]["files"]),
                        "notConsumed": unused},
        "contradictions": owner["collected"]["contradictions"],
        "gridConsistency": pairs_mod.grid_consistency(owner["answers"]),
        "tapConsistency": pairs_mod.self_consistency(owner["answers"]),
    }


def plan(args) -> list[dict]:
    """Next recordings to scan: the most-labelled recording of each outing first, then the next."""
    mapping, _ = _mapping(args)

    def group_of(sha):
        return roster.group_of(mapping, sha)

    owner = _owner(args, group_of)
    everything = _catalog_assets(args.workspace)
    tagged = roster.sport_by_source(roster.validate(_json(args.roster))) if args.roster else {}
    for sha, a in everything.items():
        # Catalog tag, else the owner's outing tag, else unknown (no judge questions).
        a["sport"] = (a["activity"] if a["activity"] in SPORTS
                      else tagged.get(sha, "unknown"))
    root = args.source_root.expanduser().absolute() if args.source_root else None
    where: dict[str, int] = {}
    activity: dict[str, int] = {}
    for a in everything.values():
        parts = Path(a["path"]).parts
        top = str(Path(*parts[:4])) if len(parts) >= 4 else a["path"]
        where[top] = where.get(top, 0) + 1
        activity[a["activity"]] = activity.get(a["activity"], 0) + 1
    # Only recordings under the footage root are considered, and only those are
    # checked on disk: paths elsewhere (another drive, a stalled card) are never touched.
    assets = {s: a for s, a in everything.items()
              if root is None or Path(a["path"]).is_relative_to(root)}
    missing = [s for s, a in assets.items() if root is not None and not Path(a["path"]).is_file()]
    for s in missing:
        del assets[s]
    plan.diagnostics = {"catalogRecordings": len(everything),
                        "underSourceRoot": len(assets) + len(missing),
                        "missingOnDisk": len(missing),
                        "catalogPathsByFolder": dict(sorted(where.items(), key=lambda kv: -kv[1])[:8]),
                        "catalogActivity": dict(sorted(activity.items(), key=lambda kv: -kv[1])),
                        "sportFromRosterTags": sum(a["activity"] not in SPORTS
                                                   and a["sport"] != "unknown"
                                                   for a in everything.values())}
    done, attempted = _done(args.exclude_experiment)
    plan.diagnostics["attemptedButFailed"] = len(attempted - done)
    weight: dict[str, float] = {}
    for j in owner["judgments"]:
        weight[j["sourceSha256"]] = weight.get(j["sourceSha256"], 0) + 1
    # A pair can only be used once both of its recordings are scanned, so
    # recordings are chosen greedily by the pairs they complete, counting what
    # earlier experiments already scanned. Scoreable pairs count most.
    pairs = []
    for p in owner["pairs"]:
        sides = frozenset((p["left"]["sourceSha256"], p["right"]["sourceSha256"]))
        pairs.append((sides, 1.0 if p["evalEligible"] else 0.3, p["evalEligible"]))
        for sha in sides:
            weight[sha] = weight.get(sha, 0) + 0.5
    reachable = [(sides, w, ok) for sides, w, ok in pairs
                 if all(sha in done or sha in assets for sha in sides)]
    candidates = {sha for sha in weight if sha in assets and sha not in attempted
                  and not (args.sport and assets[sha]["sport"] != args.sport)}
    covered, picked = set(done), []

    def usable(scope, scoreable_only=False):
        return sum(1 for sides, _, ok in pairs
                   if sides <= scope and (ok or not scoreable_only))

    before = {"all": usable(covered), "scoreable": usable(covered, True)}
    while len(picked) < args.max and candidates:
        def gain(sha):
            scope = covered | {sha}
            complete = sum(w for sides, w, _ in reachable
                           if sha in sides and sides <= scope)
            touch = sum(w for sides, w, _ in reachable
                        if sha in sides and not sides <= scope)
            return (complete, touch, weight[sha], sha)
        best = max(candidates, key=gain)
        candidates.discard(best)
        picked.append(best)
        covered.add(best)
    plan.diagnostics.update({
        "pairsTotal": len(pairs),
        "pairsWithARecordingOutsideSourceRoot": len(pairs) - len(reachable),
        "pairsUsableBeforeThisBatch": before,
        "pairsUsableAfterThisBatch": {"all": usable(covered), "scoreable": usable(covered, True)},
        "labelledRecordingsLeftAfterThisBatch": len(candidates),
    })
    if args.fill and len(picked) < args.max:
        # Spare slots: unlabelled recordings, one per outing per round, outings
        # not yet covered first, a seeded shuffle within each outing.
        import random
        rng = random.Random(f"plan:{args.seed}")
        spare: dict[str, list[str]] = {}
        for sha in sorted(assets):
            if sha in attempted or sha in picked or (args.sport and assets[sha]["sport"] != args.sport):
                continue
            spare.setdefault(group_of(sha), []).append(sha)
        for shas in spare.values():
            rng.shuffle(shas)
        covered = {group_of(s) for s in picked}
        outings = sorted(spare, key=lambda g: (g in covered, g))
        while len(picked) < args.max and any(spare[g] for g in outings):
            for g in outings:
                if spare[g] and len(picked) < args.max:
                    picked.append(spare[g].pop(0))
    print(json.dumps(plan.diagnostics, indent=2), file=sys.stderr)
    print(json.dumps({"labelled": sum(s in weight for s in picked),
                      "unlabelled": sum(s not in weight for s in picked),
                      "outings": len({group_of(s) for s in picked}),
                      "sports": sorted({assets[s]["sport"] for s in picked})}),
          file=sys.stderr)
    return [{"sourceSha256": s, "path": assets[s]["path"], "sport": assets[s]["sport"]}
            for s in picked]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    ros = sub.add_parser("roster", help="propose outings from catalog capture times")
    ros.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    ros.add_argument("--gap-hours", type=float, default=6.0)
    ros.add_argument("--output", type=Path)
    ros.add_argument("--force", action="store_true", help="replace an existing --output")

    def owner_inputs(p, *, workspace_required=False):
        p.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE,
                       help="Klip data folder with catalog.sqlite3 "
                            f"(default: $ADVENTURE_HOME or {DEFAULT_WORKSPACE})")
        p.add_argument("--reports", type=Path, nargs="*", default=[],
                       help="extra folders holding review exports (workspace reports/ and exports/ are always read)")
        p.add_argument("--ledger", type=Path)
        p.add_argument("--pairs-ledger", type=Path)
        p.add_argument("--roster", type=Path)
        p.add_argument("--seed", type=int, default=0)

    rev = sub.add_parser("review", help="open the local review app (outings and clip grid)")
    rev.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    rev.add_argument("--source-root", type=Path, help="footage folder, for outing thumbnails")
    rev.add_argument("--ledgers", type=Path,
                     help="owner files folder (default: WORKSPACE/reports/owner-ledgers)")
    rev.add_argument("--experiment", type=Path, nargs="*",
                     help="discovery folders (default: every WORKSPACE/reports/discovery-*)")
    rev.add_argument("--port", type=int, default=0)
    rev.add_argument("--no-browser", action="store_true")
    loc = sub.add_parser("locate", help="find every Klip catalog and review export under folders")
    loc.add_argument("roots", type=Path, nargs="+")
    har = sub.add_parser("harvest", help="report every owner label and whether it is used")
    owner_inputs(har, workspace_required=True)
    pl = sub.add_parser("plan", help="print a discovery source list that covers your labels")
    owner_inputs(pl, workspace_required=True)
    pl.add_argument("--max", type=int, default=12)
    pl.add_argument("--sport", choices=SPORTS)
    pl.add_argument("--exclude-experiment", type=Path, nargs="*", default=[])
    pl.add_argument("--output", type=Path)
    pl.add_argument("--force", action="store_true", help="replace an existing --output")
    pl.add_argument("--source-root", type=Path,
                    help="footage folder (same as discovery --trusted-source-root); only "
                         "recordings under it that exist on disk are picked")
    pl.add_argument("--no-fill", dest="fill", action="store_false",
                    help="only labelled recordings; by default spare slots take unlabelled "
                         "recordings from outings not yet covered")
    deck = sub.add_parser("deck", help="write a blind review page")
    ev = sub.add_parser("evaluate", help="held-out ladder and ranked review queue")
    for p in (deck, ev):
        owner_inputs(p)
        p.add_argument("--experiment", type=Path, nargs="+", required=True)
    deck.add_argument("--format", choices=("grid", "pairs"), default="grid")
    deck.add_argument("--screens", type=int, default=30, help="grid screens")
    deck.add_argument("--per-screen", type=int, default=4)
    deck.add_argument("--pairs", type=int, default=130, help="pair format only")
    deck.add_argument("--repeats", type=int)
    deck.add_argument("--max-seconds", type=float, default=60.0)
    deck.add_argument("--active", action="store_true",
                      help="pairs format: half the deck from pairs the current head separates least")
    ev.add_argument("--output", type=Path, required=True)
    imp = sub.add_parser("import-answers", aliases=["import-taps"])
    imp.add_argument("--deck", type=Path, required=True)
    imp.add_argument("--answers", type=Path, required=True)
    imp.add_argument("--pairs-ledger", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        _emit.overwrite = getattr(args, "force", False)
        if getattr(args, "workspace", None) is not None:
            explicit = "--workspace" in (argv if argv is not None else sys.argv)
            if (args.command in ("deck", "evaluate") and not explicit
                    and not (args.workspace / "catalog.sqlite3").is_file()):
                print(f"warning: no catalog at {args.workspace}; Studio and benchmark labels, "
                      "review files and embeddings are not used. Pass --workspace.",
                      file=sys.stderr)
                args.workspace = None
            else:
                args.workspace = _workspace(args.workspace)
        if args.command == "review":
            from adventure_intelligence import value_review_server as app
            folder = (args.ledgers or args.workspace / "reports" / "owner-ledgers").expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            roster_file = folder / "roster.json"
            if not roster_file.exists():
                _emit(roster.propose(roster.catalog_rows(args.workspace)), roster_file)
            experiments = args.experiment or sorted(
                (args.workspace / "reports").glob("discovery-*"))
            state = app.ReviewState(args.workspace, roster_file, folder / "owner-pairs.jsonl",
                                    experiments, args.source_root)
            print(f"[klip] {len(state.sources())} scanned recordings in "
                  f"{len(experiments)} discovery folders; answers go to "
                  f"{folder / 'owner-pairs.jsonl'}", file=sys.stderr)
            app.serve(state, port=args.port, open_browser=not args.no_browser)
        elif args.command == "locate":
            print(json.dumps(owner_evidence.locate(args.roots), indent=2))
        elif args.command == "roster":
            _emit(roster.propose(roster.catalog_rows(args.workspace),
                                 gap_hours=args.gap_hours), args.output)
        elif args.command == "harvest":
            print(json.dumps(harvest(args), indent=2))
        elif args.command == "plan":
            if not 1 <= args.max <= 12:
                raise ValueError("A discovery run takes 1..12 recordings")
            plan.diagnostics = {}
            rows = plan(args)
            if not rows:
                print(json.dumps(plan.diagnostics, indent=2), file=sys.stderr)
                raise ValueError("No recordings left to scan; see catalogPathsByFolder and "
                                 "catalogActivity above")
            _emit(rows, args.output)
        elif args.command in ("import-answers", "import-taps"):
            before = ledger.file_digest(args.answers)
            frozen = _json(args.deck)
            if frozen.get("version") == pairs_mod.GRID_DECK_VERSION:
                rows = pairs_mod.grid_records(frozen, _json(args.answers), before)
            else:
                rows = pairs_mod.tap_records(frozen, _json(args.answers), before)
            result = pairs_mod.append(args.pairs_ledger, rows)
            everything = pairs_mod.load(args.pairs_ledger)
            print(json.dumps({**result, "gridConsistency": pairs_mod.grid_consistency(everything),
                              "tapConsistency": pairs_mod.self_consistency(everything)}, indent=2))
        elif args.command == "deck":
            if len(args.experiment) != 1:
                raise ValueError("A deck plays clips from exactly one experiment directory")
            state = _load(args)
            name = args.experiment[0].name
            if args.format == "grid":
                built = pairs_mod.build_grid(
                    state["items"], experiment=name, screens=args.screens,
                    per_screen=args.per_screen, seed=args.seed, max_seconds=args.max_seconds,
                    repeats=4 if args.repeats is None else args.repeats)
                page_html = pairs_mod.grid_html(built)
            else:
                scores = None
                if args.active:
                    from adventure_intelligence import value_head as head
                    model = head.fit_selecting_l2(state["pairs"], state["items"], variant="full")
                    if model is None:
                        raise ValueError("--active needs earlier owner pairs to fit a head")
                    scores = {i: head.score(model, item) for i, item in state["items"].items()}
                built = pairs_mod.build_deck(
                    state["items"], experiment=name, pairs=args.pairs,
                    repeats=20 if args.repeats is None else args.repeats, seed=args.seed,
                    max_seconds=args.max_seconds, scores=scores)
                page_html = pairs_mod.deck_html(built)
            stem = args.experiment[0] / f"{args.format}-{pairs_mod.deck_digest(built)[:12]}"
            page, frozen = stem.with_suffix(".html"), stem.with_suffix(".deck.json")
            if page.exists() or frozen.exists():
                raise ValueError(f"This deck already exists: {page.name}")
            frozen.write_text(json.dumps(built, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            page.write_text(page_html, encoding="utf-8")
            print(json.dumps({"page": str(page), "deck": str(frozen),
                              "screensOrPairs": len(built.get("screens", built.get("pairs", []))),
                              "outings": built["outings"],
                              "rosterConfirmed": state["confirmed"]}, indent=2))
        else:
            from adventure_intelligence import value_eval
            if args.output.exists() or args.output.is_symlink():
                raise ValueError("Output directory exists; each evaluation needs a new one")
            state = _load(args)
            if not state["pairs"]:
                raise ValueError("No owner pairs found; run harvest to see which labels exist")
            answers = state["answers"]
            report = value_eval.evaluate(
                state["items"], state["pairs"], state["judgments"], pool_ids=state["pool_ids"],
                roster_confirmed=state["confirmed"], seed=args.seed,
                self_consistency={"taps": pairs_mod.self_consistency(answers),
                                  "grid": pairs_mod.grid_consistency(answers)})
            report["inputs"] = {
                "experiments": {f.name: ledger.file_digest(f / "experiment.json")
                                for f in args.experiment},
                "ledgerSha256": ledger.file_digest(args.ledger) if args.ledger else None,
                "pairsLedgerSha256": (ledger.file_digest(args.pairs_ledger)
                                      if args.pairs_ledger and args.pairs_ledger.exists() else None),
                "rosterSha256": ledger.file_digest(args.roster) if args.roster else None,
                "catalogSha256": (ledger.file_digest(args.workspace / "catalog.sqlite3")
                                  if args.workspace and (args.workspace / "catalog.sqlite3").is_file()
                                  else None),
                "ownerEvidence": state["collected"]["counts"],
                "rosterConfirmed": state["confirmed"], "embeddings": state["embeddings"],
                "seed": args.seed, "sourcesWithEvidence": len(state["sources"]),
                "sourcesListedInTwoExperiments": state["duplicates"],
                "labelledRecordingsNotInTheseExperiments": state["labelledSourcesWithoutEvidence"],
            }
            queue = value_eval.review_queue(state["items"], state["pairs"], state["pool_ids"])
            args.output.mkdir(parents=True)
            for name, doc in (("report.json", report), ("queue.json", queue)):
                (args.output / name).write_text(
                    json.dumps(doc, indent=2, sort_keys=True, allow_nan=False) + "\n",
                    encoding="utf-8")
            table = {name: row["macro"] for name, row in report["pairAccuracy"].items()}
            print(json.dumps({"gate": report["gate"], "coverage": report["coverage"],
                              "pairAccuracyMacro": dict(sorted(
                                  table.items(), key=lambda kv: -(kv[1] or 0)))}, indent=2))
            print(f"report: {args.output / 'report.json'}", file=sys.stderr)
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

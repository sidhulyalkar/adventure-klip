#!/usr/bin/env python3
"""Rank an outing's footage by what the owner would keep, and check it against the owner.

Works on discovery experiment output plus the owner ledgers. It never opens
video and never calls a model. Typical order:

    klip_value_experiment.py roster --workspace "$WS" > roster.json      # then check and confirm it
    klip_value_experiment.py deck --experiment "$WS/reports/discovery-001" --roster roster.json
    #   open the taps-*.html it wrote, answer, export
    klip_value_experiment.py import-taps --deck taps-*.deck.json --answers klip-tap-answers-*.json \\
        --pairs-ledger owner-pairs.jsonl
    klip_value_experiment.py evaluate --experiment "$WS/reports/discovery-001" \\
        --ledger owner-evidence.jsonl --pairs-ledger owner-pairs.jsonl --roster roster.json \\
        --workspace "$WS" --output "$WS/reports/value-001"

`evaluate` writes report.json (held-out comparison) and queue.json (ranked
segments with reasons) into a new directory and refuses to reuse one.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from adventure_intelligence import evidence_ledger as ledger
from adventure_intelligence import outing_roster as roster
from adventure_intelligence import value_pairs as pairs_mod


def _json(path: Path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Missing or symlinked input: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load(args):
    """Evidence, items and pairs shared by `deck` and `evaluate`."""
    from adventure_intelligence import value_eval, value_features as features
    sources, duplicates = {}, 0
    for folder in args.experiment:
        for sha, source in features.load_experiment(folder).items():
            if sha in sources:
                duplicates += 1  # first experiment listed wins; never mix two runs of one source
            else:
                sources[sha] = source
    mapping, confirmed = {}, False
    if args.roster:
        doc = roster.validate(_json(args.roster))
        mapping, confirmed = roster.outing_by_source(doc), doc["status"] == "owner_confirmed"
    embeddings = (features.attach_embeddings(sources, args.workspace)
                  if getattr(args, "workspace", None) else None)
    rows = ledger.load(args.ledger) if getattr(args, "ledger", None) else []
    taps = pairs_mod.load(args.pairs_ledger) if getattr(args, "pairs_ledger", None) else []

    def group_of(sha):
        return roster.group_of(mapping, sha)

    pool = features.segment_intervals(sources)
    items = features.build_items(
        sources, pool + value_eval.ledger_intervals(rows) + value_eval.pair_intervals(taps),
        group_of, seed=args.seed)
    pool_ids = {features.item_id(*interval) for interval in pool} & set(items)
    all_pairs = pairs_mod.from_ledger(rows, group_of, seed=args.seed) + pairs_mod.from_taps(
        taps, group_of)
    missing = sorted({r["sourceSha256"][:12] for r in rows if r["sourceSha256"] not in sources})
    return {"sources": sources, "items": items, "pool_ids": pool_ids, "pairs": all_pairs,
            "rows": rows, "taps": taps, "confirmed": confirmed, "embeddings": embeddings,
            "duplicates": duplicates, "ledgerSourcesWithoutEvidence": missing}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    ros = sub.add_parser("roster", help="propose outings from catalog capture times")
    ros.add_argument("--workspace", type=Path, required=True)
    ros.add_argument("--gap-hours", type=float, default=6.0)
    deck = sub.add_parser("deck", help="write a blind side-by-side tap page")
    ev = sub.add_parser("evaluate", help="held-out ladder and ranked review queue")
    for p in (deck, ev):
        p.add_argument("--experiment", type=Path, nargs="+", required=True)
        p.add_argument("--roster", type=Path)
        p.add_argument("--ledger", type=Path)
        p.add_argument("--pairs-ledger", type=Path)
        p.add_argument("--workspace", type=Path, help="catalog with cached frame embeddings")
        p.add_argument("--seed", type=int, default=0)
    deck.add_argument("--pairs", type=int, default=130)
    deck.add_argument("--repeats", type=int, default=20)
    deck.add_argument("--max-seconds", type=float, default=60.0)
    deck.add_argument("--active", action="store_true",
                      help="spend half the deck on pairs the current head separates least")
    ev.add_argument("--output", type=Path, required=True)
    imp = sub.add_parser("import-taps")
    imp.add_argument("--deck", type=Path, required=True)
    imp.add_argument("--answers", type=Path, required=True)
    imp.add_argument("--pairs-ledger", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "roster":
            print(json.dumps(roster.propose(roster.catalog_rows(args.workspace),
                                            gap_hours=args.gap_hours), indent=2))
        elif args.command == "import-taps":
            before = ledger.file_digest(args.answers)
            rows = pairs_mod.tap_records(_json(args.deck), _json(args.answers), before)
            result = pairs_mod.append(args.pairs_ledger, rows)
            print(json.dumps({**result, "selfConsistency": pairs_mod.self_consistency(
                pairs_mod.load(args.pairs_ledger))}, indent=2))
        elif args.command == "deck":
            if len(args.experiment) != 1:
                raise ValueError("A deck plays proxies from exactly one experiment directory")
            state = _load(args)
            scores = None
            if args.active:
                from adventure_intelligence import value_head as head
                model = head.fit_selecting_l2(state["pairs"], state["items"], variant="full")
                if model is None:
                    raise ValueError("--active needs earlier owner pairs to fit a head")
                scores = {i: head.score(model, item) for i, item in state["items"].items()}
            built = pairs_mod.build_deck(
                state["items"], experiment=args.experiment[0].name, pairs=args.pairs,
                repeats=args.repeats, seed=args.seed, max_seconds=args.max_seconds, scores=scores)
            stem = args.experiment[0] / f"taps-{pairs_mod.deck_digest(built)[:12]}"
            page, frozen = stem.with_suffix(".html"), stem.with_suffix(".deck.json")
            if page.exists() or frozen.exists():
                raise ValueError(f"This deck already exists: {page.name}")
            frozen.write_text(json.dumps(built, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            page.write_text(pairs_mod.deck_html(built), encoding="utf-8")
            print(json.dumps({"page": page.name, "deck": frozen.name,
                              "pairs": len(built["pairs"]), "uniquePairs": built["uniquePairs"],
                              "outings": built["outings"],
                              "rosterConfirmed": state["confirmed"]}, indent=2))
        else:
            from adventure_intelligence import value_eval
            if args.output.exists() or args.output.is_symlink():
                raise ValueError("Output directory exists; each evaluation needs a new one")
            if not args.ledger and not args.pairs_ledger:
                raise ValueError("Give --ledger and/or --pairs-ledger; there is nothing to learn from")
            state = _load(args)
            report = value_eval.evaluate(
                state["items"], state["pairs"], state["rows"], pool_ids=state["pool_ids"],
                roster_confirmed=state["confirmed"], seed=args.seed,
                self_consistency=pairs_mod.self_consistency(state["taps"]) if state["taps"] else None)
            report["inputs"] = {
                "experiments": {f.name: ledger.file_digest(f / "experiment.json")
                                for f in args.experiment},
                "ledgerSha256": ledger.file_digest(args.ledger) if args.ledger else None,
                "pairsLedgerSha256": (ledger.file_digest(args.pairs_ledger)
                                      if args.pairs_ledger and args.pairs_ledger.exists() else None),
                "rosterSha256": ledger.file_digest(args.roster) if args.roster else None,
                "rosterConfirmed": state["confirmed"], "embeddings": state["embeddings"],
                "seed": args.seed, "sourcesWithEvidence": len(state["sources"]),
                "sourcesListedInTwoExperiments": state["duplicates"],
                "ledgerSourcesWithoutEvidence": state["ledgerSourcesWithoutEvidence"],
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

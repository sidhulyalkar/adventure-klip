"""Does anything rank an outing's footage the way the owner would?

Every ranker is scored on the same owner pairs and the same kept spans, one
held-out outing at a time:

- pair accuracy: of the pairs where the owner preferred one interval, how
  often the ranker scores that interval higher;
- recall at budget: rank every segment of the outing, take the top share of
  footage, count how many owner-marked Keep spans are inside it.

The ladder runs from chance through single measurements (motion, audio,
novelty) to the learned head. The head is refitted for each outing on pairs
from the other outings only. A learned model that does not beat the best
single measurement here has not earned its place.
"""
from __future__ import annotations

import random

import numpy as np

from . import timeline_segments as ts
from . import value_head as head
from .value_features import item_id

VERSION = "klip-value-evaluation-v1"
BASELINES = ("motion_peak", "motion_mean", "audio_peak_rel", "audio_mean",
             "novelty_outing", "novelty_archive")
FUSION = ("audio_peak_rel", "novelty_outing")
BUDGETS = (0.1, 0.25)
MIN_GROUPS, MIN_PAIRS = 5, 100  # below either, results are a pilot and no claim is made


def baseline_scores(items: dict[str, dict]) -> dict[str, dict[str, float | None]]:
    """Single-measurement rankers, plus a rank fusion of audio surprise and outing novelty."""
    out = {name: {i: item["signals"][name] for i, item in items.items()} for name in BASELINES}
    fused: dict[str, float | None] = {i: None for i in items}
    by_group: dict[str, list[str]] = {}
    for i, item in items.items():
        by_group.setdefault(item["group"], []).append(i)
    for ids in by_group.values():
        ranks = []
        for name in FUSION:
            have = sorted((i for i in ids if out[name][i] is not None),
                          key=lambda i: out[name][i])
            ranks.append({i: (k + 0.5) / len(have) for k, i in enumerate(have)})
        for i in ids:
            if all(i in r for r in ranks):
                fused[i] = sum(r[i] for r in ranks) / len(ranks)
    out["fusion_audio_novelty"] = fused
    return out


def pair_accuracy(pairs: list[dict], scores: dict[str, float | None]) -> dict:
    """Agreement with decisive owner pairs. A missing or tied score counts as a coin flip,

    so every ranker is judged on the identical set of pairs.
    """
    hits, abstained, n = 0.0, 0, 0
    for p in pairs:
        if p["outcome"] == 0.5 or not p["evalEligible"]:
            continue
        a, b = (scores.get(i) for i in head._ids(p))
        n += 1
        if a is None or b is None:
            abstained += 1
            hits += 0.5
        elif a == b:
            hits += 0.5
        else:
            hits += float((a > b) == (p["outcome"] == 1.0))
    return {"pairs": n, "accuracy": hits / n if n else None, "abstained": abstained}


def _select(pool: list[dict], scores: dict[str, float | None], budget: float,
            rng: random.Random | None = None) -> list[dict]:
    """Highest-scored segments until the budget share of the pool's duration is reached.

    Unscored segments come last. `rng` shuffles instead of ranking (the chance baseline).
    """
    order = list(pool)
    if rng is not None:
        rng.shuffle(order)
    else:
        order.sort(key=lambda s: (scores.get(s["itemId"]) is None,
                                  -(scores.get(s["itemId"]) or 0.0), s["itemId"]))
    limit = budget * sum(s["endMs"] - s["startMs"] for s in pool)
    chosen, used = [], 0
    for s in order:
        if used >= limit:
            break
        chosen.append(s)
        used += s["endMs"] - s["startMs"]
    return chosen


def _recovered(chosen: list[dict], kept: dict[str, list[tuple[int, int]]]) -> int:
    count = 0
    for sha, spans in kept.items():
        intervals = [(s["startMs"], s["endMs"]) for s in chosen if s["sourceSha256"] == sha]
        count += ts.span_recall(intervals, spans)["recovered"]
    return count


def _macro(per_group: dict[str, float | None]) -> float | None:
    values = [v for v in per_group.values() if v is not None]
    return sum(values) / len(values) if values else None


def _bootstrap(per_group_a: dict, per_group_b: dict, *, seed: int, resamples: int = 2000):
    """Paired interval for mean(a - b), resampling whole outings."""
    groups = sorted(g for g in per_group_a
                    if per_group_a[g] is not None and per_group_b.get(g) is not None)
    if len(groups) < MIN_GROUPS:
        return {"groups": len(groups), "interval95": None,
                "reason": f"fewer than {MIN_GROUPS} outings"}
    deltas = np.array([per_group_a[g] - per_group_b[g] for g in groups])
    rng = np.random.default_rng(seed)
    means = deltas[rng.integers(0, len(deltas), size=(resamples, len(deltas)))].mean(axis=1)
    return {"groups": len(groups), "meanDelta": round(float(deltas.mean()), 4),
            "interval95": [round(float(np.quantile(means, q)), 4) for q in (0.025, 0.975)]}


def evaluate(items: dict[str, dict], pairs: list[dict], ledger_rows: list[dict], *,
             pool_ids: set[str], roster_confirmed: bool, seed: int = 0,
             self_consistency: dict | None = None) -> dict:
    """Leave-one-outing-out comparison of the baseline ladder and the learned head."""
    pairs = head.usable(pairs, items)
    scored = [p for p in pairs if p["outcome"] != 0.5 and p["evalEligible"]]
    groups = sorted({p["group"] for p in scored})
    rankers = baseline_scores(items)

    # Owner-marked Keep spans are the only evidence that can measure recall:
    # windows a pipeline picked were already filtered by that pipeline.
    kept: dict[str, dict[str, list[tuple[int, int]]]] = {}
    pool_sources = {items[i]["sourceSha256"] for i in pool_ids}
    spans_without_pool = 0
    for r in ledger_rows:
        if r["decision"] == "keep" and r["selection"] == "owner_marked_span":
            if r["sourceSha256"] in pool_sources:
                group = next(items[i]["group"] for i in pool_ids
                             if items[i]["sourceSha256"] == r["sourceSha256"])
                kept.setdefault(group, {}).setdefault(r["sourceSha256"], []).append(
                    (r["startMs"], r["endMs"]))
            else:
                spans_without_pool += 1
    pools: dict[str, list[dict]] = {}
    for i in sorted(pool_ids):
        pools.setdefault(items[i]["group"], []).append(items[i])

    # One head per held-out outing, trained on every other outing's pairs.
    for variant in head.VARIANTS:
        scores: dict[str, float | None] = {}
        for group in sorted(set(groups) | set(kept)):
            model = head.fit_selecting_l2([p for p in pairs if p["group"] != group], items,
                                          variant=variant)
            for i, item in items.items():
                if item["group"] == group:
                    scores[i] = head.score(model, item) if model else None
        rankers["head_" + variant] = scores

    accuracy = {}
    for name, scores in rankers.items():
        per_group = {g: pair_accuracy([p for p in scored if p["group"] == g], scores)
                     for g in groups}
        overall = pair_accuracy(scored, scores)
        accuracy[name] = {
            "macro": _round(_macro({g: v["accuracy"] for g, v in per_group.items()})),
            "micro": _round(overall["accuracy"]), "pairs": overall["pairs"],
            "abstained": overall["abstained"],
            "perOuting": {g: _round(v["accuracy"]) for g, v in per_group.items()}}
    accuracy["chance"] = {"macro": 0.5, "micro": 0.5, "pairs": len(scored), "abstained": 0,
                          "perOuting": {g: 0.5 for g in groups}}

    recall = {}
    for budget in BUDGETS:
        table = {}
        for name, scores in rankers.items():
            per_group = {g: _recovered(_select(pools[g], scores, budget), kept[g])
                         / sum(map(len, kept[g].values())) for g in sorted(kept)}
            table[name] = {"macro": _round(_macro(per_group)),
                           "perOuting": {g: _round(v) for g, v in per_group.items()}}
        rng = random.Random(f"{VERSION}:{seed}:{budget}")
        chance = {g: sum(_recovered(_select(pools[g], {}, budget, rng), kept[g])
                         for _ in range(200)) / 200 / sum(map(len, kept[g].values()))
                  for g in sorted(kept)}
        table["chance"] = {"macro": _round(_macro(chance)),
                           "perOuting": {g: _round(v) for g, v in chance.items()}}
        recall[str(budget)] = table

    singles = [n for n in rankers if not n.startswith("head_")]
    best = max(singles, key=lambda n: (accuracy[n]["macro"] or 0.0, n)) if groups else None
    deltas = {}
    if best:
        for variant in head.VARIANTS:
            name = "head_" + variant
            deltas[f"{name}_minus_{best}"] = _bootstrap(
                accuracy[name]["perOuting"], accuracy[best]["perOuting"], seed=seed)
        deltas[f"{best}_minus_chance"] = _bootstrap(
            accuracy[best]["perOuting"], accuracy["chance"]["perOuting"], seed=seed)

    return {
        "version": VERSION,
        "coverage": {
            "outingsWithScoredPairs": len(groups),
            "pairsUsable": len(pairs), "pairsScored": len(scored),
            "pairsByTier": _count(p["tier"] for p in pairs),
            "pairsByGoal": _count(p["goal"] for p in scored),
            "trainOnlyPairs": sum(not p["evalEligible"] for p in pairs),
            "ownerKeptSpansInPool": sum(len(v) for g in kept.values() for v in g.values()),
            "ownerKeptSpansOutsideAnyExperiment": spans_without_pool,
            "poolSegments": len(pool_ids),
            "unassignedGroups": sum(g.startswith("source:") for g in groups),
        },
        "pairAccuracy": accuracy, "recallAtBudget": recall,
        "bestBaseline": best, "pairedDeltas": deltas,
        "ownerSelfConsistency": self_consistency,
        "gate": _gate(groups, scored, best, deltas, roster_confirmed),
    }


def _gate(groups, scored, best, deltas, roster_confirmed) -> dict:
    reasons = []
    if not roster_confirmed:
        reasons.append("outing roster is not owner-confirmed")
    if any(g.startswith("source:") for g in groups):
        reasons.append("some recordings have no outing and were held out one by one")
    if len(groups) < MIN_GROUPS:
        reasons.append(f"{len(groups)} outings with scored pairs; need {MIN_GROUPS}")
    if len(scored) < MIN_PAIRS:
        reasons.append(f"{len(scored)} scored pairs; need {MIN_PAIRS}")
    if reasons:
        return {"status": "pilot_no_claim", "reasons": reasons}
    # The best baseline was picked on this same data, which favours
    # it. A head that still clears it has done so against a generous opponent.
    wins = [name for name, d in deltas.items()
            if name.startswith("head_") and d["interval95"] and d["interval95"][0] > 0]
    if wins:
        return {"status": "head_beats_best_baseline", "comparisons": wins,
                "reasons": ["95% outing-bootstrap interval of the difference is above zero"]}
    return {"status": "head_not_shown_better",
            "reasons": [f"no head variant clearly beats {best}; rank with {best} and "
                        "collect more pairs or better evidence before more modelling"]}


def review_queue(items: dict[str, dict], pairs: list[dict], pool_ids: set[str],
                 *, variant: str = "full", per_outing: int = 20) -> dict:
    """Fit on all pairs and rank each outing's segments, with the evidence behind each score.

    Scores for outings that supplied training pairs are in-sample. Use the
    evaluation report, not this queue, to judge the model.
    """
    model = head.fit_selecting_l2(pairs, items, variant=variant)
    if model is None:
        return {"version": VERSION, "model": None, "outings": {}}
    trained_on = {p["group"] for p in head.usable(pairs, items)}
    outings: dict[str, list[dict]] = {}
    for i in sorted(pool_ids):
        item = items[i]
        outings.setdefault(item["group"], []).append({
            "sourceSha256": item["sourceSha256"], "startMs": item["startMs"],
            "endMs": item["endMs"], "role": item["role"],
            "score": round(head.score(model, item), 4),
            "why": head.contributions(model, item),
            "isModelRankingNotOwnerJudgment": True})
    return {"version": VERSION, "model": head.export(model),
            "outings": {g: {"inSample": g in trained_on,
                            "segments": sorted(rows, key=lambda r: -r["score"])[:per_outing]}
                        for g, rows in outings.items()}}


def _round(value):
    return None if value is None else round(float(value), 4)


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items()))


def ledger_intervals(rows: list[dict]) -> list[tuple[str, int, int]]:
    return [(r["sourceSha256"], r["startMs"], r["endMs"]) for r in rows]


def pair_intervals(rows: list[dict]) -> list[tuple[str, int, int]]:
    return [(s["sourceSha256"], s["startMs"], s["endMs"])
            for r in rows for s in (r["left"], r["right"])]


__all__ = ["evaluate", "review_queue", "baseline_scores", "pair_accuracy",
           "ledger_intervals", "pair_intervals", "item_id"]

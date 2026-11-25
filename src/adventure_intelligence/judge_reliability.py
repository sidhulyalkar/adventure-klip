"""How far to trust each judge, per question, and what a cheaper panel would cost.

agreement()  From existing panels, no owner input: per question, how often both judges
             gave the same answer, Cohen's kappa (agreement beyond chance), and each
             model's yes-rate. The audit of a judge pass.
fact deck    Owner yes/no on a sample of facts: disputed ones (to settle them) and agreed
             ones as controls (agreement is not truth). Held-out days are never sampled.
reliability() From the owner's fact labels: per sport, question and model, sensitivity
             (yes when true) and specificity (no when false), Beta(1, 1)-smoothed, with
             false-yes / false-no counts as in judge_panel.calibrate.
resolve()    A disputed fact settled by the judges' measured reliability (naive Bayes over
             the votes, prior = the owner's base rate for that question), only when every
             voting model has at least MIN_LABELS labels for that question; otherwise it
             stays disputed. Resolved facts are marked as such, never as agreed.
simulate_cascade()  What asking the fast judge first would save: the slow judge is asked
             only when, for some question, the fast judge's answer is one the two judges
             agreed on less than `threshold` of the time. Reports slow calls saved and the
             share of the current panel's disputes that would go unseen.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from . import judge_panel as panel

VERSION = "klip-judge-reliability-v1"
MIN_LABELS = 8
RESOLVE_AT = 0.85


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def panels(reports: Path) -> list[dict]:
    """Every judged review clip with both judges' answers, across discovery folders."""
    out = []
    for path in sorted(Path(reports).glob("discovery-*/*/judgments/*.json")):
        doc = _read(path)
        if not doc or not isinstance(doc.get("panel"), dict) or "sport" not in doc["panel"] \
                or not isinstance(doc["panel"].get("facts"), dict):
            continue          # incomplete or foreign file: not a panel to learn from
        folder, sha = path.parents[2].name, path.parents[1].name
        out.append({"id": f"{folder}/{sha}/{path.stem}", "folder": folder, "sourceSha256": sha,
                    "segment": path.stem, "sport": doc["panel"]["sport"],
                    "facts": doc["panel"]["facts"], "frameTimesMs": doc.get("frameTimesMs", []),
                    "proxy": str(path.parents[1] / "proxies" / f"{path.stem}.mp4"),
                    "models": sorted({j["model"] for j in doc.get("judgments", [])
                                      if j.get("status") == "answered"})})
    return out


def _kappa(pairs: list[tuple[str, str]]) -> float | None:
    if len(pairs) < 2:
        return None
    labels = {a for p in pairs for a in p}
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    pe = sum((sum(a == l for a, _ in pairs) / n) * (sum(b == l for _, b in pairs) / n)
             for l in labels)
    return None if pe >= 1 else round((po - pe) / (1 - pe), 3)


def agreement(all_panels: list[dict]) -> dict:
    rows: dict[tuple, dict] = {}
    for p in all_panels:
        for key, fact in p["facts"].items():
            votes = fact.get("votes", {})
            if len(votes) < 2:
                continue
            (m1, a1), (m2, a2) = sorted(votes.items())[:2]
            r = rows.setdefault((p["sport"], key), {"pairs": [], "outcomes": defaultdict(int),
                                                     "yes": defaultdict(int), "models": (m1, m2)})
            r["outcomes"][fact["outcome"]] += 1
            if "unclear" not in (a1, a2):
                r["pairs"].append((a1, a2))
            for m, a in votes.items():
                r["yes"][m] += a == "yes"
    table = []
    for (sport, key), r in sorted(rows.items()):
        n_out = sum(r["outcomes"].values())
        pairs = r["pairs"]
        table.append({
            "sport": sport, "question": key,
            "text": {**panel.RUBRICS.get(sport, {}), **panel.SHARED}.get(key),
            "panels": n_out, "outcomes": dict(r["outcomes"]),
            "agreement": round(sum(a == b for a, b in pairs) / len(pairs), 3) if pairs else None,
            "kappa": _kappa(pairs),
            "yesRate": {m: round(v / n_out, 3) for m, v in r["yes"].items()},
            "disputedShare": round(r["outcomes"].get("disputed", 0) / n_out, 3)})
    return {"version": VERSION, "panels": len(all_panels), "byQuestion": table}


def deck_items(all_panels: list[dict], *, held_out: set[str], already: set[str],
               size: int = 40, control_share: float = 0.25, seed: str = "") -> list[dict]:
    """Disputed facts, spread across questions, plus agreed facts as controls."""
    import random
    rng = random.Random(seed or "klip-fact-deck")
    disputed, agreed = defaultdict(list), []
    for p in all_panels:
        if p["sourceSha256"] in held_out or not p["frameTimesMs"]:
            continue
        for key, fact in p["facts"].items():
            item = f'{p["id"]}|{key}'
            if item in already:
                continue
            entry = {"item": item, "panel": p, "question": key, "outcome": fact["outcome"],
                     "votes": fact.get("votes", {})}
            if fact["outcome"] == "disputed":
                disputed[(p["sport"], key)].append(entry)
            elif fact["outcome"] in ("agreed_yes", "agreed_no"):
                agreed.append(entry)
    for lst in disputed.values():
        rng.shuffle(lst)
    rng.shuffle(agreed)
    n_controls = round(size * control_share)
    picked, queues = [], [q for _, q in sorted(disputed.items())]
    while len(picked) < size - n_controls and any(queues):   # round-robin over questions
        for q in queues:
            if q and len(picked) < size - n_controls:
                picked.append(q.pop())
    picked += agreed[:size - len(picked)]
    rng.shuffle(picked)
    return picked


def reliability(labels: list[dict]) -> dict:
    cells: dict[tuple, dict] = {}
    base: dict[tuple, list] = defaultdict(list)
    for l in labels:
        if l["answer"] not in ("yes", "no"):
            continue
        base[(l["sport"], l["question"])].append(l["answer"] == "yes")
        for model, said in (l.get("votes") or {}).items():
            c = cells.setdefault((l["sport"], l["question"], model), {
                "n": 0, "truePos": 0, "trueNeg": 0, "falseYes": 0, "falseNo": 0, "unclear": 0})
            c["n"] += 1
            if said == "unclear":
                c["unclear"] += 1
            elif said == "yes":
                c["truePos" if l["answer"] == "yes" else "falseYes"] += 1
            else:
                c["trueNeg" if l["answer"] == "no" else "falseNo"] += 1
    out = []
    for (sport, q, model), c in sorted(cells.items()):
        pos, neg = c["truePos"] + c["falseNo"], c["trueNeg"] + c["falseYes"]
        out.append({"sport": sport, "question": q, "model": model, **c,
                    "sensitivity": round((c["truePos"] + 1) / (pos + 2), 3),
                    "specificity": round((c["trueNeg"] + 1) / (neg + 2), 3),
                    "accuracy": round((c["truePos"] + c["trueNeg"]) / max(1, c["n"] - c["unclear"]), 3)})
    overall: dict[str, dict] = {}
    for r in out:
        o = overall.setdefault(r["model"], {"n": 0, "correct": 0, "falseYes": 0, "falseNo": 0,
                                            "unclear": 0})
        for k in ("n", "falseYes", "falseNo", "unclear"):
            o[k] += r[k]
        o["correct"] += r["truePos"] + r["trueNeg"]
    for o in overall.values():
        decided = o["n"] - o["unclear"]
        o["accuracy"] = round(o["correct"] / decided, 3) if decided else None
    on_disputes: dict[str, list] = defaultdict(list)
    for l in labels:
        if l["answer"] in ("yes", "no") and l.get("outcome") == "disputed":
            for model, said in (l.get("votes") or {}).items():
                if said in ("yes", "no"):
                    on_disputes[model].append(said == l["answer"])
    return {"version": VERSION, "labels": len(labels),
            "byModel": overall,
            # who is right when the two judges disagree: the number that matters most
            "byModelOnDisputes": {m: {"n": len(v), "right": sum(v),
                                      "accuracy": round(sum(v) / len(v), 3) if v else None}
                                  for m, v in sorted(on_disputes.items())},
            "agreedFactsOwnerConfirmed": {
                "n": sum(1 for l in labels if l["answer"] in ("yes", "no")
                         and l.get("outcome") in ("agreed_yes", "agreed_no")),
                "right": sum(1 for l in labels if l["answer"] in ("yes", "no")
                             and l.get("outcome") == f"agreed_{l['answer']}")},
            "baseRate": {f"{s}:{q}": round((sum(v) + 1) / (len(v) + 2), 3)
                         for (s, q), v in sorted(base.items())},
            "byModelQuestion": out, "minLabels": MIN_LABELS, "resolveAt": RESOLVE_AT,
            "smallSampleCaution": len(labels) < 30}


def resolve(sport: str, question: str, votes: dict, rel: dict | None) -> tuple[str, float | None]:
    """('resolved_yes' | 'resolved_no' | 'disputed', P(yes)) for a disputed fact."""
    if not rel:
        return "disputed", None
    cells = {(r["sport"], r["question"], r["model"]): r for r in rel["byModelQuestion"]}
    prior = rel["baseRate"].get(f"{sport}:{question}")
    usable = {m: a for m, a in votes.items() if a in ("yes", "no")}
    if prior is None or not usable or any(
            cells.get((sport, question, m), {}).get("n", 0) < rel.get("minLabels", MIN_LABELS)
            for m in usable):
        return "disputed", None
    logit = math.log(prior / (1 - prior))
    for m, a in usable.items():
        c = cells[(sport, question, m)]
        se, sp = c["sensitivity"], c["specificity"]
        logit += math.log(se / (1 - sp)) if a == "yes" else math.log((1 - se) / sp)
    p = 1 / (1 + math.exp(-logit))
    at = rel.get("resolveAt", RESOLVE_AT)
    return ("resolved_yes" if p >= at else "resolved_no" if p <= 1 - at else "disputed"), round(p, 3)


def apply(facts: dict, sport: str, rel: dict | None) -> dict:
    """A copy of panel facts with disputes settled where reliability allows."""
    out = {}
    for key, fact in facts.items():
        if fact["outcome"] == "disputed":
            outcome, p = resolve(sport, key, fact.get("votes", {}), rel)
            if outcome != "disputed":
                out[key] = {**fact, "outcome": outcome, "pYes": p,
                            "resolvedBy": "owner-calibrated judge reliability"}
                continue
        out[key] = fact
    return out


def simulate_cascade(all_panels: list[dict], *, fast: str, slow: str,
                     threshold: float = 0.9, min_n: int = 20) -> dict:
    """Ask `slow` only when the fast answer is one the judges often disagree on."""
    cond: dict[tuple, list] = defaultdict(list)
    for p in all_panels:
        for key, fact in p["facts"].items():
            v = fact.get("votes", {})
            if fast in v and slow in v:
                cond[(p["sport"], key, v[fast])].append(v[fast] == v[slow])
    trusted = {k for k, xs in cond.items() if len(xs) >= min_n and sum(xs) / len(xs) >= threshold}
    total = skipped = hidden = disputes = 0
    for p in all_panels:
        both = [f.get("votes", {}) for f in p["facts"].values()]
        if not all(fast in v and slow in v for v in both):
            continue
        total += 1
        n_disp = sum(f["outcome"] == "disputed" for f in p["facts"].values())
        disputes += n_disp
        if all((p["sport"], k, f["votes"][fast]) in trusted for k, f in p["facts"].items()):
            skipped += 1
            hidden += n_disp
    return {"version": VERSION, "fast": fast, "slow": slow, "threshold": threshold,
            "minAnswers": min_n, "panels": total, "slowCallsSaved": skipped,
            "slowCallsSavedShare": round(skipped / total, 3) if total else None,
            "disputesThatWouldGoUnseen": hidden,
            "disputesUnseenShare": round(hidden / disputes, 3) if disputes else None,
            "trustedFastAnswers": sorted(f"{s}:{q}={a}" for s, q, a in trusted),
            "note": "Agreement is not truth: owner fact labels decide whether a cheaper "
                    "panel is good enough; this says how much it would change."}


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


MAX_DISPUTED = 0.4
MIN_KAPPA = 0.4
KAPPA_NEEDS_DISPUTED = 0.2   # kappa collapses when one answer dominates (underwater: 96%
                             # agreement, kappa 0.0), so it only counts alongside disagreement


def weak_questions(agreement_doc: dict, *, max_disputed: float = MAX_DISPUTED,
                   min_kappa: float = MIN_KAPPA) -> dict[tuple[str, str], str]:
    """(sport, question) -> why, for questions the judges cannot answer consistently."""
    out = {}
    for r in agreement_doc["byQuestion"]:
        if r["disputedShare"] > max_disputed:
            out[(r["sport"], r["question"])] = f"disputed on {r['disputedShare']:.0%} of clips"
        elif (r["kappa"] is not None and r["kappa"] < min_kappa
              and r["disputedShare"] > KAPPA_NEEDS_DISPUTED):
            out[(r["sport"], r["question"])] = (f"kappa {r['kappa']} with "
                                                f"{r['disputedShare']:.0%} disputed")
    return out


def demote(facts: dict, sport: str, weak: dict[tuple[str, str], str]) -> dict:
    """A copy of panel facts where answers to weak questions count as uncertain, even when
    the judges agreed: agreement on a question they usually disagree on is not evidence."""
    out = {}
    for key, fact in facts.items():
        why = weak.get((sport, key))
        if why and fact["outcome"] in ("agreed_yes", "agreed_no"):
            out[key] = {**fact, "outcome": "disputed", "agreedAs": fact["outcome"],
                        "demotedBy": f"low judge agreement on this question ({why})"}
        else:
            out[key] = fact
    return out

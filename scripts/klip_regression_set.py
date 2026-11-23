#!/usr/bin/env python3
"""A frozen set of owner-checked facts that every judge prompt or model change is scored on.

    build   freeze up to --size owner fact labels (yes/no, development days only), spread
            across questions, into REPORTS/regression-v1.json (once; --replace needs a reason)
    score   ask --model on those clips (the same frames the panel saw), compare with the
            owner's answers, and compare with the last score of the same model under a
            different prompt fingerprint: a drop of more than --tolerance is a regression

    klip_regression_set.py build --reports "$WS/reports"
    klip_regression_set.py score --reports "$WS/reports" --model gemma3:27b
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import sys

from adventure_intelligence import holdout
from adventure_intelligence import judge_panel as panel
from adventure_intelligence import owner_labels
from adventure_intelligence import run_lock
from adventure_intelligence import run_records

VERSION = "klip-regression-set-v1"
FILE = "regression-v1.json"


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build(reports: Path, size: int, *, replace: bool = False, reason: str | None = None) -> dict:
    target = reports / FILE
    current = _read(target)
    if current and not replace:
        raise ValueError(f"{FILE} is frozen; --replace with a --reason to rebuild it")
    if current and not reason:
        raise ValueError("Rebuilding the regression set needs a --reason")
    banned = holdout.held_out(reports)
    labels = [l for l in owner_labels.load(reports, "fact")
              if l["answer"] in ("yes", "no") and l["sourceSha256"] not in banned]
    by_q = defaultdict(list)
    for l in labels:
        by_q[(l["sport"], l["question"])].append(l)
    rng = random.Random("klip-regression")
    for v in by_q.values():
        rng.shuffle(v)
    picked, queues = [], [v for _, v in sorted(by_q.items())]
    while len(picked) < size and any(queues):
        for q in queues:
            if q and len(picked) < size:
                picked.append(q.pop())
    if not picked:
        raise ValueError("No owner fact labels yet; run the fact-check deck first")
    items = []
    for l in picked:
        folder, sha, seg = l["panel"].split("/")
        judged = _read(reports / folder / sha / "judgments" / f"{seg}.json") or {}
        items.append({"item": l["item"], "sport": l["sport"], "question": l["question"],
                      "answer": l["answer"], "panel": l["panel"], "sourceSha256": sha,
                      "proxy": str(reports / folder / sha / "proxies" / f"{seg}.mp4"),
                      "frameTimesMs": judged.get("frameTimesMs") or l["frameTimesMs"]})
    if current:
        hist = reports / "regression-history"
        hist.mkdir(exist_ok=True)
        (hist / f"regression-{len(list(hist.glob('*.json'))) + 1:03d}.json").write_text(
            json.dumps(current, indent=2), encoding="utf-8")
    doc = {"version": VERSION, "frozenAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "reason": reason, "items": items}
    target.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return {"items": len(items), "questions": len(by_q), "file": str(target)}


def score(reports: Path, model: str, *, tolerance: float, timeout: int, transport=None,
          frames=None, think: bool | None = None) -> dict:
    if frames is None:
        from adventure_intelligence.panel_frames import sample_frames as frames
    doc = _read(reports / FILE)
    if not doc:
        raise ValueError(f"No {FILE}; run build first")
    by_panel = defaultdict(list)
    for it in doc["items"]:
        by_panel[it["panel"]].append(it)
    right = wrong = unclear = failed = 0
    per_q: dict[str, list] = defaultdict(lambda: [0, 0])
    fingerprints = set()
    for pid, items in sorted(by_panel.items()):
        first = items[0]
        try:
            images = frames(Path(first["proxy"]), first["frameTimesMs"])
        except ValueError:
            failed += len(items)
            continue
        out = panel.ask(model, first["sport"], images, first["frameTimesMs"],
                        transport=transport, timeout=timeout, think=think)
        fingerprints.add(out["fingerprint"])
        if out["status"] != "answered":
            failed += len(items)
            continue
        for it in items:
            said = out["answers"].get(it["question"])
            cell = per_q[f'{it["sport"]}:{it["question"]}']
            cell[1] += 1
            if said == "unclear":
                unclear += 1
            elif said == it["answer"]:
                right += 1
                cell[0] += 1
            else:
                wrong += 1
    decided = right + wrong
    acc = round(right / decided, 3) if decided else None
    result = {"version": VERSION, "model": model, "think": think,
              "fingerprints": sorted(fingerprints),
              "items": len(doc["items"]), "right": right, "wrong": wrong, "unclear": unclear,
              "failed": failed, "accuracy": acc,
              "byQuestion": {q: {"right": r, "n": n} for q, (r, n) in sorted(per_q.items())},
              "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    previous = None
    for f in sorted((reports / "evaluations").glob("regression-*.json"), reverse=True):
        old = _read(f)
        if (old and old["model"] == model and old.get("think") == think
                and old["fingerprints"] != result["fingerprints"]):
            previous = old
            break
    if previous and previous.get("accuracy") is not None and acc is not None:
        delta = round(acc - previous["accuracy"], 3)
        result["comparedWith"] = {"fingerprints": previous["fingerprints"],
                                  "accuracy": previous["accuracy"], "delta": delta,
                                  "regression": delta < -tolerance}
    folder = reports / "evaluations"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    (folder / f"regression-{stamp}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main(argv=None, *, transport=None, frames=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "score"):
        sub.add_parser(name).add_argument("--reports", type=Path, required=True)
    b = sub.choices["build"]
    b.add_argument("--size", type=int, default=40)
    b.add_argument("--replace", action="store_true")
    b.add_argument("--reason")
    s = sub.choices["score"]
    s.add_argument("--model", required=True)
    s.add_argument("--tolerance", type=float, default=0.05)
    s.add_argument("--timeout", type=int, default=1500)
    s.add_argument("--think", choices=("off", "on", "default"), default="default")
    args = parser.parse_args(argv)
    try:
        reports = args.reports.expanduser()
        if args.command == "build":
            print(json.dumps(build(reports, args.size, replace=args.replace, reason=args.reason),
                             indent=2))
            return 0
        with run_lock.exclusive() if transport is None else _null():
            result = score(reports, args.model, tolerance=args.tolerance, timeout=args.timeout,
                           transport=transport, frames=frames,
                           think={"off": False, "on": True, "default": None}[args.think])
        print(json.dumps(result, indent=2))
        return 1 if (result.get("comparedWith") or {}).get("regression") else 0
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


if __name__ == "__main__":
    raise SystemExit(run_records.main_recorded(main, "klip_regression_set"))

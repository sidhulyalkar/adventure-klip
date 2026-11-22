#!/usr/bin/env python3
"""Audit the judges, collect owner fact checks, and measure judge reliability.

    agreement    per-question agreement and Cohen's kappa from existing panels (no input)
    deck         an owner fact-check page: disputed facts plus agreed controls, never
                 from held-out days; answers download as JSON
    import       those answers -> owner fact labels (owner-ledgers/fact-labels.jsonl)
    reliability  per sport/question/model sensitivity and specificity from the labels;
                 writes judge-reliability.json, which the story stage then uses to settle
                 disputes where the evidence allows
    cascade      what asking the fast judge first would save and hide

    klip_fact_check.py agreement   --reports "$WS/reports"
    klip_fact_check.py deck        --reports "$WS/reports" --size 40
    open "$WS/reports/fact-check/<deck>/index.html"
    klip_fact_check.py import      --reports "$WS/reports" --answers ~/Downloads/fact-answers-<deck>.json
    klip_fact_check.py reliability --reports "$WS/reports"
    klip_fact_check.py cascade     --reports "$WS/reports"
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
from pathlib import Path
import sys

from adventure_intelligence import holdout
from adventure_intelligence import judge_panel as panel
from adventure_intelligence import judge_reliability as jr
from adventure_intelligence import owner_labels


def _write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(doc if isinstance(doc, str) else json.dumps(doc, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def build_deck(reports: Path, size: int, frames_per_item: int, sampler=None) -> dict:
    if sampler is None:
        from adventure_intelligence.panel_frames import sample_frames as sampler
    all_panels = jr.panels(reports)
    already = {l["item"] for l in owner_labels.load(reports, "fact")}
    items = jr.deck_items(all_panels, held_out=holdout.held_out(reports), already=already,
                          size=size)
    if not items:
        raise ValueError("Nothing left to check (no disputed or agreed facts outside held-out days)")
    deck = hashlib.sha256("|".join(sorted(i["item"] for i in items)).encode()).hexdigest()[:12]
    folder = reports / "fact-check" / deck
    key, cards = {"deck": deck, "items": {}}, []
    for n, it in enumerate(items, 1):
        p = it["panel"]
        times = p["frameTimesMs"]
        step = max(1, len(times) // frames_per_item)
        shown = times[::step][:frames_per_item]
        imgs = sampler(Path(p["proxy"]), shown)
        names = []
        for k, b64 in enumerate(imgs):
            name = f"{n:03d}-{k}.jpg"
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(base64.b64decode(b64))
            names.append(name)
        text = {**panel.RUBRICS.get(p["sport"], {}), **panel.SHARED}.get(it["question"], it["question"])
        iid = f"q{n:03d}"
        key["items"][iid] = {"item": it["item"], "panel": p["id"], "sport": p["sport"],
                             "question": it["question"], "outcome": it["outcome"],
                             "votes": it["votes"], "sourceSha256": p["sourceSha256"],
                             "frameTimesMs": shown}
        clip = html.escape(os.path.relpath(p["proxy"], folder))
        cards.append(
            f'<section data-id="{iid}"><h3>{n}. {html.escape(text)}</h3>'
            f'<div class=g>{"".join(f"<img src={chr(34)}{x}{chr(34)}>" for x in names)}</div>'
            f'<p><a href="{clip}" target=_blank>play the clip</a> · {p["sport"]}</p>'
            '<button data-v="yes">Yes</button><button data-v="no">No</button>'
            '<button data-v="unclear">Can\'t tell</button></section>')
    page = ("<!doctype html><meta charset=utf-8><title>Fact check</title><style>"
            "body{font:15px system-ui;max-width:1100px;margin:2em auto;padding:0 1em}"
            ".g{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}.g img{width:100%}"
            "section{margin-bottom:2.2em}button{margin-right:.5em;padding:.4em .9em}"
            "button.on{background:#2b6;color:#fff}#bar{position:sticky;top:0;background:#fff;"
            "padding:.5em 0;border-bottom:1px solid #ccc}</style>"
            f"<div id=bar><b>Fact check</b> · {len(items)} questions about what is visible in "
            "these frames · <button id=dl>Download answers</button> <span id=n></span></div>"
            + "".join(cards) +
            "<script>const deck=%s,a={};" % json.dumps(deck) +
            "document.querySelectorAll('section').forEach(s=>s.querySelectorAll('button').forEach(b=>"
            "b.onclick=()=>{a[s.dataset.id]=b.dataset.v;s.querySelectorAll('button').forEach(x=>"
            "x.classList.toggle('on',x===b));document.getElementById('n').textContent="
            "Object.keys(a).length+' answered'}));"
            "document.getElementById('dl').onclick=()=>{const u=URL.createObjectURL(new Blob("
            "[JSON.stringify({deck,answers:a},null,2)],{type:'application/json'}));"
            "const l=document.createElement('a');l.href=u;l.download='fact-answers-'+deck+'.json';"
            "l.click()}</script>")
    _write(folder / "key.json", key)
    _write(folder / "index.html", page)
    return {"deck": deck, "items": len(items),
            "disputed": sum(i["outcome"] == "disputed" for i in items),
            "controls": sum(i["outcome"] != "disputed" for i in items),
            "page": str(folder / "index.html")}


def import_answers(reports: Path, answers_file: Path) -> dict:
    raw = answers_file.read_bytes()
    answers = json.loads(raw)
    key = json.loads((reports / "fact-check" / answers["deck"] / "key.json").read_text(encoding="utf-8"))
    items = []
    for iid, answer in (answers.get("answers") or {}).items():
        k = key["items"].get(iid)
        if k is None or answer not in ("yes", "no", "unclear"):
            continue
        items.append({"item": k["item"], "answer": answer, "sport": k["sport"],
                      "question": k["question"], "votes": k["votes"], "outcome": k["outcome"],
                      "sourceSha256": k["sourceSha256"], "panel": k["panel"],
                      "frameTimesMs": k["frameTimesMs"]})
    return owner_labels.append(reports, "fact", deck=key["deck"],
                               answers_sha256=hashlib.sha256(raw).hexdigest(), items=items)


def main(argv=None, *, sampler=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("agreement", "deck", "import", "reliability", "cascade"):
        sub.add_parser(name).add_argument("--reports", type=Path, required=True)
    sub.choices["deck"].add_argument("--size", type=int, default=40)
    sub.choices["deck"].add_argument("--frames", type=int, default=6)
    sub.choices["import"].add_argument("--answers", type=Path, required=True)
    sub.choices["cascade"].add_argument("--fast", default="gemma3:27b")
    sub.choices["cascade"].add_argument("--slow", default="qwen3-vl:32b")
    sub.choices["cascade"].add_argument("--threshold", type=float, default=0.9)
    args = parser.parse_args(argv)
    try:
        reports = args.reports.expanduser()
        if args.command == "agreement":
            result = jr.agreement(jr.panels(reports))
            _write(reports / "evaluations" / f"judge-agreement-{jr.stamp()}.json", result)
            for r in result["byQuestion"]:
                print(f'{r["sport"]:<11} {r["question"]:<24} n={r["panels"]:<4} '
                      f'agree={r["agreement"]}  kappa={r["kappa"]}  disputed={r["disputedShare"]}  '
                      f'yes={r["yesRate"]}', file=sys.stderr)
            print(json.dumps({"panels": result["panels"]}, indent=2))
        elif args.command == "deck":
            print(json.dumps(build_deck(reports, args.size, args.frames, sampler), indent=2))
        elif args.command == "import":
            print(json.dumps(import_answers(reports, args.answers), indent=2))
        elif args.command == "reliability":
            banned = holdout.held_out(reports)
            labels = [l for l in owner_labels.load(reports, "fact") if l["sourceSha256"] not in banned]
            result = jr.reliability(labels)
            _write(reports / "evaluations" / f"judge-reliability-{jr.stamp()}.json", result)
            _write(reports / "judge-reliability.json", result)
            print(json.dumps({k: result[k] for k in ("labels", "smallSampleCaution")} | {
                "cells": len(result["byModelQuestion"])}, indent=2))
        else:
            result = jr.simulate_cascade(jr.panels(reports), fast=args.fast, slow=args.slow,
                                         threshold=args.threshold)
            _write(reports / "evaluations" / f"cascade-{jr.stamp()}.json", result)
            print(json.dumps(result, indent=2))
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    from adventure_intelligence import run_records
    raise SystemExit(run_records.main_recorded(main, "klip_fact_check"))

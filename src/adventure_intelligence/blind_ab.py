"""Blind two-way comparisons: one page, one key, one exact sign test.

Each pair shows the same thing made two ways, A on a random side. The page never says
which is which; the key (key.json) does. Answers download as JSON and are scored with an
exact two-sided binomial sign test over decided pairs (ties excluded); no verdict below
MIN_DECIDED decided pairs.
"""
from __future__ import annotations

from hashlib import sha256
import html
import json
import math
import os
from pathlib import Path
import random

VERSION = "klip-blind-ab-v1"
MIN_DECIDED = 10


def write_deck(folder: Path, pairs: list[dict], *, variant: str, baseline: str,
               question: str, seed: str = "", loop: bool = True) -> dict:
    """pairs: [{"id", "variantPath", "baselinePath", "meta": {...}}]. Paths may be anywhere;
    the page links them relative to `folder`."""
    rng = random.Random(seed or f"klip-{variant}-vs-{baseline}")
    folder.mkdir(parents=True, exist_ok=True)
    order = list(pairs)
    rng.shuffle(order)
    key, cards = {}, []
    for n, p in enumerate(order, 1):
        variant_left = rng.random() < 0.5
        left, right = ((p["variantPath"], p["baselinePath"]) if variant_left
                       else (p["baselinePath"], p["variantPath"]))
        key[p["id"]] = {"variant": "left" if variant_left else "right", **p.get("meta", {})}
        attrs = "muted playsinline preload=metadata" + (" loop" if loop else " controls")
        cards.append(
            f'<section data-pair="{html.escape(p["id"])}"><h3>{n}</h3><div class="row">'
            f'<video {attrs} src="{html.escape(os.path.relpath(left, folder))}"></video>'
            f'<video {attrs} src="{html.escape(os.path.relpath(right, folder))}"></video></div>'
            '<div class="b"><button data-v="left">Left is better</button>'
            '<button data-v="same">No difference</button>'
            '<button data-v="right">Right is better</button></div></section>')
    deck = sha256(json.dumps({"variant": variant, "baseline": baseline, "pairs": key},
                             sort_keys=True).encode()).hexdigest()[:12]
    doc = {"version": VERSION, "deck": deck, "variant": variant, "baseline": baseline,
           "question": question, "pairs": key}
    (folder / "key.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    page = (
        "<!doctype html><meta charset=utf-8><title>Blind comparison</title><style>"
        "body{font:15px system-ui;max-width:1200px;margin:2em auto;padding:0 1em}"
        ".row{display:flex;gap:8px}.row video{width:50%;background:#000}"
        ".b{margin:.5em 0 2.5em}button{margin-right:.5em;padding:.4em .8em}"
        "button.on{background:#2b6;color:#fff}</style>"
        f"<h1>{html.escape(question)}</h1><p>{len(order)} pairs. Click a pair to play both "
        "from the start.</p>" + "".join(cards) +
        '<p><button id=dl>Download answers</button> <span id=n></span></p><script>'
        f"const deck={json.dumps(deck)},a={{}};"
        'document.querySelectorAll("section").forEach(s=>{const v=s.querySelectorAll("video");'
        's.querySelector(".row").onclick=()=>{v.forEach(x=>{x.currentTime=0;x.play()})};'
        's.querySelectorAll("button").forEach(b=>b.onclick=()=>{a[s.dataset.pair]=b.dataset.v;'
        's.querySelectorAll("button").forEach(x=>x.classList.toggle("on",x===b));'
        'document.getElementById("n").textContent=Object.keys(a).length+" answered"})});'
        'document.getElementById("dl").onclick=()=>{const u=URL.createObjectURL(new Blob('
        '[JSON.stringify({deck,answers:a},null,2)],{type:"application/json"}));'
        'const l=document.createElement("a");l.href=u;l.download="ab-answers-"+deck+".json";'
        'l.click()}</script>')
    (folder / "index.html").write_text(page, encoding="utf-8")
    return {"deck": deck, "pairs": len(order), "page": str(folder / "index.html")}


def sign_test(wins: int, losses: int) -> float:
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def score(key: dict, answers: dict) -> dict:
    if answers.get("deck") != key["deck"]:
        raise ValueError("These answers belong to a different comparison deck")
    won = lost = same = 0
    for pid, choice in (answers.get("answers") or {}).items():
        truth = key["pairs"].get(pid)
        if truth is None or choice not in ("left", "right", "same"):
            continue
        if choice == "same":
            same += 1
        elif choice == truth["variant"]:
            won += 1
        else:
            lost += 1
    p = sign_test(won, lost)
    v, b = key["variant"], key["baseline"]
    if won + lost < MIN_DECIDED:
        verdict = f"too few decided pairs to conclude (need {MIN_DECIDED} or more)"
    elif p < 0.05:
        verdict = f"{v} preferred" if won > lost else f"{b} preferred"
    else:
        verdict = f"no clear preference between {v} and {b}"
    return {"deck": key["deck"], "variant": v, "baseline": b, "pairs": len(key["pairs"]),
            "answered": won + lost + same, "preferVariant": won, "preferBaseline": lost,
            "noDifference": same, "signTestP": round(p, 4), "verdict": verdict}

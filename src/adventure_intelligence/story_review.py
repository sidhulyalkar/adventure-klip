"""Owner review of outing stories, claim by claim, and how accurate the stories are.

page() lays out every outing's story: each story beat and each moment, with the scene's
review clip playable right there, and three answers: true, partly true, wrong (with an
optional rewrite). Answers download as JSON; import_answers() stores them as owner labels
(owner_labels kind "claim"). evaluate() reports claim accuracy by what the claim rests on
(judges' facts, the describer's observations, subjects, signals), by what it names (animal,
jump, person, scenery...), by sport and by the story's harness status, development days
apart from held-out ones. That accuracy is what decides whether stories can be trusted.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import html
import json
import os
from pathlib import Path

from . import owner_labels
from .story_harness import _TERM, _words

VERSION = "klip-story-review-v1"


def _story_id(doc: dict) -> str:
    r = doc["record"]
    return sha256(f'{r["packetSha256"]}|{r["model"]}'.encode()).hexdigest()[:12]


def claims(doc: dict) -> list[dict]:
    """Every reviewable claim of one story file: beats and moments."""
    story = (doc["record"] or {}).get("story") or {}
    packet = doc["packet"]
    scenes = {s["id"]: s for s in packet["scenes"]}
    sid = _story_id(doc)
    out = []
    for i, b in enumerate(story.get("beats", []), 1):
        first = scenes.get((b.get("scenes") or [None])[0])
        out.append({"id": f"{sid}:beat{i}", "kind": "beat", "text": b["text"],
                    "scenes": b.get("scenes", []), "evidence": [],
                    "scene": first})
    for i, m in enumerate(story.get("moments", []), 1):
        out.append({"id": f"{sid}:moment{i}", "kind": "moment",
                    "text": f'{m["what"]}. Why: {m["whyItMatters"]}', "scenes": [m["scene"]],
                    "evidence": m.get("evidence", []), "confidence": m.get("confidence"),
                    "scene": scenes.get(m["scene"])})
    return out


def deck(story_docs: list[dict]) -> dict:
    items = {}
    for doc in story_docs:
        for c in claims(doc):
            s = c["scene"] or {}
            items[c["id"]] = {
                "outing": doc["packet"]["outing"], "sport": doc["packet"]["sport"],
                "kind": c["kind"], "text": c["text"], "scenes": c["scenes"],
                "evidence": c["evidence"], "confidence": c.get("confidence"),
                "sourceSha256": s.get("sourceSha256"), "file": s.get("file"),
                "harnessStatus": doc["record"]["harnessStatus"], "model": doc["record"]["model"]}
    deck_id = sha256(json.dumps(sorted(items), sort_keys=True).encode()).hexdigest()[:12]
    return {"version": VERSION, "deck": deck_id, "items": items}


def page(story_docs: list[dict], key: dict, base: Path, proxy_of) -> str:
    """proxy_of(scene) -> Path of its review clip, or None."""
    def rel(p):
        return html.escape(os.path.relpath(Path(p), base))

    sections = []
    for doc in story_docs:
        packet, record = doc["packet"], doc["record"]
        rows = []
        for c in claims(doc):
            s = c["scene"] or {}
            clip = proxy_of(s) if s else None
            video = (f'<video preload="none" controls src="{rel(clip)}"></video>'
                     if clip else "")
            ev = ", ".join(c["evidence"]) or "-"
            rows.append(
                f'<div class="claim" data-id="{html.escape(c["id"])}">'
                f'<div class="t"><span class="k">{c["kind"]}</span> '
                f'{html.escape(c["text"])}</div>'
                f'<div class="m">scenes {", ".join(c["scenes"])} · '
                f'{html.escape(s.get("file") or "?")} {s.get("clockStart") or ""} · '
                f'evidence: {html.escape(ev)}</div>{video}'
                '<div class="b"><button data-v="true">True</button>'
                '<button data-v="partly">Partly</button>'
                '<button data-v="wrong">Wrong</button>'
                '<input placeholder="what is actually true (optional)" size="60"></div></div>')
        if not rows:
            rows.append(f"<p>No story: {html.escape(str(record.get('failureDetail')))}</p>")
        sections.append(f"<section><h2>{html.escape(packet['outing'])}</h2>"
                        f"<p class=n>{record['harnessStatus']} · {record['model']}</p>"
                        + "".join(rows) + "</section>")
    script = ("const deck=%s,a={};" % json.dumps(key["deck"]) + """
document.querySelectorAll('.claim').forEach(el=>{const id=el.dataset.id,inp=el.querySelector('input');
 const save=v=>{a[id]={verdict:v,rewrite:inp.value||null};
   el.querySelectorAll('button').forEach(x=>x.classList.toggle('on',x.dataset.v===v));
   document.getElementById('n').textContent=Object.keys(a).length+' claims answered';};
 el.querySelectorAll('button').forEach(b=>b.onclick=()=>save(b.dataset.v));
 inp.onchange=()=>{if(a[id])a[id].rewrite=inp.value||null;};});
document.getElementById('dl').onclick=()=>{const u=URL.createObjectURL(new Blob(
 [JSON.stringify({deck,claims:a},null,2)],{type:'application/json'}));
 const l=document.createElement('a');l.href=u;l.download='claim-answers-'+deck+'.json';l.click();};""")
    return ("<!doctype html><meta charset=utf-8><title>Story review</title><style>"
            "body{font:15px system-ui;max-width:1000px;margin:2em auto;padding:0 1em}"
            ".claim{border-top:1px solid #ddd;padding:.8em 0}.k{font-size:12px;color:#fff;"
            "background:#777;border-radius:3px;padding:0 4px}.m{color:#666;font-size:13px}"
            "video{width:100%;max-height:300px;background:#000;margin:.3em 0}"
            "button{margin-right:.4em;padding:.25em .8em}button.on{background:#2b6;color:#fff}"
            ".n{color:#666}#bar{position:sticky;top:0;background:#fff;padding:.5em 0;"
            "border-bottom:1px solid #ccc;z-index:2}</style>"
            "<div id=bar><b>Story review</b> · is each claim true of the clip? · "
            "<button id=dl>Download answers</button> <span id=n></span></div>"
            + "".join(sections) + f"<script>{script}</script>")


def import_answers(reports: Path, key: dict, answers: dict, answers_sha256: str) -> dict:
    if answers.get("deck") != key["deck"]:
        raise ValueError("These answers belong to a different story review page")
    items = []
    for cid, ans in (answers.get("claims") or {}).items():
        c = key["items"].get(cid)
        if c is None or ans.get("verdict") not in ("true", "partly", "wrong"):
            continue
        items.append({"item": cid, "verdict": ans["verdict"], "rewrite": ans.get("rewrite"),
                      **{k: c[k] for k in ("outing", "sport", "kind", "text", "scenes",
                                           "evidence", "confidence", "sourceSha256", "file",
                                           "harnessStatus", "model")}})
    return owner_labels.append(reports, "claim", deck=key["deck"],
                               answers_sha256=answers_sha256, items=items)


def _rate(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"claims": 0}
    t = sum(r["verdict"] == "true" for r in rows)
    p = sum(r["verdict"] == "partly" for r in rows)
    return {"claims": n, "true": t, "partly": p, "wrong": n - t - p,
            "trueShare": round(t / n, 3), "atLeastPartlyShare": round((t + p) / n, 3)}


def evaluate(labels: list[dict], *, held_out: set[str]) -> dict:
    out = {"version": VERSION, "labels": len(labels)}
    for split, keep in (("development", lambda l: l.get("sourceSha256") not in held_out),
                        ("heldOut", lambda l: l.get("sourceSha256") in held_out)):
        rows = [l for l in labels if keep(l)]
        by = defaultdict(lambda: defaultdict(list))
        for l in rows:
            by["kind"][l["kind"]].append(l)
            by["sport"][l["sport"]].append(l)
            by["harnessStatus"][l["harnessStatus"]].append(l)
            for ref in l.get("evidence") or ["(none)"]:
                by["restsOn"][ref.split(":")[0]].append(l)
            for concept in sorted({_TERM[w] for w in _words(l["text"]) if w in _TERM}) or ["(nothing named)"]:
                by["names"][concept].append(l)
        out[split] = {"overall": _rate(rows),
                      **{k: {g: _rate(v) for g, v in sorted(groups.items())}
                         for k, groups in by.items()}}
    out["wrongClaims"] = [{"outing": l["outing"], "text": l["text"][:160],
                           "evidence": l.get("evidence"), "ownerSays": l.get("rewrite")}
                          for l in labels if l["verdict"] == "wrong"][:40]
    return out

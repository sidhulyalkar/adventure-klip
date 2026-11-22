"""Boundary labels from watching the reels, and how well each boundary method does.

The gallery shows each outing's preview reel and, under it, every clip on its whole review
clip with handles: set in/out at the playhead (I / O keys), nudge by 0.1 or 0.5 s, play the
trim, mark the trim fine or the clip not a keeper. Answers download as JSON; importing them
writes owner boundary labels (owner_labels kind "boundary") in proxy seconds and in
camera-original milliseconds.

evaluate() scores any boundary method against those labels. The baseline is what the
recipe proposed (fixed sport windows around the anchor); other methods (flow onsets, audio
onsets, gyroscope) plug in as functions of the label. Metrics, split into development and
held-out days:
  median |in error|, median |out error|, share within 0.5 s / 1 s at both ends,
  truncation rate (the owner extended the clip by more than 0.5 s at either end: the
  method cut off action, worse than slack) and slack rate (the owner trimmed it shorter).
"""
from __future__ import annotations

from hashlib import sha256
import html
import json
import os
from pathlib import Path
from statistics import median

from . import owner_labels

VERSION = "klip-boundary-review-v1"
TOLERANCE_S = 0.5


def clip_id(recipe: dict, clip: dict) -> str:
    return f'{recipe["recipeSha256"][:12]}:{clip["scene"]}'


def deck_key(shown: list[dict]) -> dict:
    clips = {}
    for x in shown:
        recipe = x["recipe"]
        for c in recipe["clips"]:
            clips[clip_id(recipe, c)] = {
                "recipeSha256": recipe["recipeSha256"], "outing": recipe["outing"],
                "sport": recipe["sport"], "scene": c["scene"], "sourceSha256": c["sourceSha256"],
                "file": c["file"], "proxy": c["proxy"], "anchor": c["anchor"],
                "anchorSeconds": c["anchorSeconds"],
                "proposedIn": c["proxyInSeconds"], "proposedOut": c["proxyOutSeconds"],
                "segmentStartMs": c["originalInMs"] - int(round(c["proxyInSeconds"] * 1000))}
    deck = sha256(json.dumps(sorted(clips), sort_keys=True).encode()).hexdigest()[:12]
    return {"version": VERSION, "deck": deck, "clips": clips}


def page(shown: list[dict], key: dict, base: Path) -> str:
    """The gallery: reels, and a trim panel per clip. Paths are relative to `base`."""
    def rel(p):
        return html.escape(os.path.relpath(Path(p), base))

    sections = []
    for x in shown:
        recipe, render = x["recipe"], x.get("preview")
        video = (f'<video controls preload="metadata" src="{rel(render["path"])}"></video>'
                 if render else "<p>not rendered</p>")
        rows = []
        for c in recipe["clips"]:
            cid = clip_id(recipe, c)
            rows.append(
                f'<div class="clip" data-id="{html.escape(cid)}" data-in="{c["proxyInSeconds"]}" '
                f'data-out="{c["proxyOutSeconds"]}">'
                f'<div class="meta"><b>{html.escape(c["clockStart"] or "?")}</b> '
                f'{html.escape(c["file"])} · scene {c["scene"]} · anchor: {html.escape(c["anchor"])}'
                f'<br>{html.escape(c.get("what") or "")} <i>{html.escape(c.get("whyItMatters") or "")}</i></div>'
                f'<video preload="metadata" src="{rel(c["proxy"])}" controls></video>'
                '<div class="ctl">in <span class="vin"></span> '
                '<button data-a="in-0.5">−0.5</button><button data-a="in-0.1">−0.1</button>'
                '<button data-a="in+0.1">+0.1</button><button data-a="in+0.5">+0.5</button>'
                '<button data-a="in=now">in = playhead (I)</button> · out <span class="vout"></span> '
                '<button data-a="out-0.5">−0.5</button><button data-a="out-0.1">−0.1</button>'
                '<button data-a="out+0.1">+0.1</button><button data-a="out+0.5">+0.5</button>'
                '<button data-a="out=now">out = playhead (O)</button></div>'
                '<div class="ctl"><button data-a="play">▶ play trim</button> '
                '<button data-a="ok">trim is fine</button> '
                '<button data-a="reject">not a keeper</button> <span class="state"></span></div></div>')
        sections.append(
            f"<section><h2>{html.escape(recipe['outing'])}</h2>{video}"
            f"<p class=note>Draft, model-proposed (story by {html.escape(recipe['story']['model'])}, "
            f"harness {html.escape(recipe['story']['harnessStatus'])}). Recipe "
            f"<code>{recipe['recipeSha256'][:12]}</code>. Trim each clip below.</p>{''.join(rows)}</section>")
    script = """
const deck=%s,a={};let focus=null;
const f=x=>x.toFixed(2)+'s';
document.querySelectorAll('.clip').forEach(el=>{
 const v=el.querySelector('video'),id=el.dataset.id;let i=+el.dataset.in,o=+el.dataset.out;
 const show=()=>{el.querySelector('.vin').textContent=f(i);el.querySelector('.vout').textContent=f(o);};
 const save=(verdict)=>{a[id]={in:+i.toFixed(3),out:+o.toFixed(3),verdict};
   el.querySelector('.state').textContent=verdict;
   document.getElementById('n').textContent=Object.keys(a).length+' clips answered';};
 el.addEventListener('mouseenter',()=>focus={v,set:(k)=>{if(k==='in')i=Math.min(v.currentTime,o-0.2);else o=Math.max(v.currentTime,i+0.2);show();save('adjusted');}});
 v.addEventListener('loadedmetadata',()=>{v.currentTime=i;});
 v.addEventListener('timeupdate',()=>{if(v.dataset.trim&&v.currentTime>=o){v.pause();delete v.dataset.trim;}});
 el.querySelectorAll('button').forEach(b=>b.onclick=()=>{const act=b.dataset.a;
  if(act==='play'){v.currentTime=i;v.dataset.trim=1;v.play();return;}
  if(act==='ok'){save(a[id]&&a[id].verdict==='adjusted'?'adjusted':'ok');return;}
  if(act==='reject'){save('reject');return;}
  const m=act.match(/^(in|out)([=+-])(.*)$/);let t;
  if(m[2]==='=')t=v.currentTime;else t=(m[1]==='in'?i:o)+(m[2]==='+'?1:-1)*parseFloat(m[3]);
  t=Math.max(0,Math.min(v.duration||1e9,t));
  if(m[1]==='in')i=Math.min(t,o-0.2);else o=Math.max(t,i+0.2);
  show();save('adjusted');if(m[2]!=='=')v.currentTime=(m[1]==='in'?i:Math.max(i,o-1));});
 show();});
document.addEventListener('keydown',e=>{if(!focus)return;
 if(e.key==='i'||e.key==='I')focus.set('in');if(e.key==='o'||e.key==='O')focus.set('out');});
document.getElementById('dl').onclick=()=>{const u=URL.createObjectURL(new Blob(
 [JSON.stringify({deck,boundaries:a},null,2)],{type:'application/json'}));
 const l=document.createElement('a');l.href=u;l.download='boundary-answers-'+deck+'.json';l.click();};
""" % json.dumps(key["deck"])
    return ("<!doctype html><meta charset=utf-8><title>Klip highlight drafts</title><style>"
            "body{font:15px system-ui;max-width:1000px;margin:2em auto;padding:0 1em}"
            "section>video{width:100%;background:#000}.clip{border-top:1px solid #ccc;padding:1em 0}"
            ".clip video{width:100%;max-height:360px;background:#000}.ctl{margin:.3em 0}"
            "button{margin:0 .15em;padding:.2em .5em}.state{color:#2b6;font-weight:600}"
            ".note{color:#555}#bar{position:sticky;top:0;background:#fff;padding:.5em 0;"
            "border-bottom:1px solid #ccc;z-index:2}</style>"
            "<div id=bar><b>Highlight drafts</b> · hover a clip, press I / O to set in/out at the "
            "playhead · <button id=dl>Download trim answers</button> <span id=n></span></div>"
            + "".join(sections) + f"<script>{script}</script>")


def import_answers(reports: Path, key: dict, answers: dict, answers_sha256: str) -> dict:
    if answers.get("deck") != key["deck"]:
        raise ValueError("These trim answers belong to a different gallery")
    items = []
    for cid, ans in (answers.get("boundaries") or {}).items():
        c = key["clips"].get(cid)
        if c is None or ans.get("verdict") not in ("ok", "adjusted", "reject"):
            continue
        a_in, a_out = float(ans["in"]), float(ans["out"])
        if ans["verdict"] != "reject" and not 0 <= a_in < a_out:
            continue
        items.append({
            "item": cid, "verdict": ans["verdict"], "sport": c["sport"],
            "outing": c["outing"], "sourceSha256": c["sourceSha256"], "file": c["file"],
            "recipeSha256": c["recipeSha256"], "scene": c["scene"], "anchor": c["anchor"],
            "anchorSeconds": c["anchorSeconds"],
            "proposed": {"inSeconds": c["proposedIn"], "outSeconds": c["proposedOut"]},
            "owner": {"inSeconds": a_in, "outSeconds": a_out,
                      "originalInMs": c["segmentStartMs"] + int(round(a_in * 1000)),
                      "originalOutMs": c["segmentStartMs"] + int(round(a_out * 1000))}})
    return owner_labels.append(reports, "boundary", deck=key["deck"],
                               answers_sha256=answers_sha256, items=items)


def _metrics(pairs: list[tuple[float, float, float, float]]) -> dict:
    if not pairs:
        return {"clips": 0}
    d_in = [abs(p_in - o_in) for p_in, p_out, o_in, o_out in pairs]
    d_out = [abs(p_out - o_out) for p_in, p_out, o_in, o_out in pairs]
    trunc = [o_in < p_in - TOLERANCE_S or o_out > p_out + TOLERANCE_S
             for p_in, p_out, o_in, o_out in pairs]
    slack = [o_in > p_in + TOLERANCE_S or o_out < p_out - TOLERANCE_S
             for p_in, p_out, o_in, o_out in pairs]
    n = len(pairs)
    return {"clips": n, "medianInErrorS": round(median(d_in), 3),
            "medianOutErrorS": round(median(d_out), 3),
            "within0_5s": round(sum(a <= .5 and b <= .5 for a, b in zip(d_in, d_out)) / n, 3),
            "within1s": round(sum(a <= 1 and b <= 1 for a, b in zip(d_in, d_out)) / n, 3),
            "truncationRate": round(sum(trunc) / n, 3), "slackRate": round(sum(slack) / n, 3)}


def evaluate(labels: list[dict], *, held_out: set[str], methods: dict | None = None) -> dict:
    """methods: name -> fn(label) -> (in_s, out_s) | None. 'fixed' (the recipe) is always in."""
    methods = {"fixed": lambda l: (l["proposed"]["inSeconds"], l["proposed"]["outSeconds"]),
               **(methods or {})}
    usable = [l for l in labels if l["verdict"] in ("ok", "adjusted")]
    out = {"version": VERSION, "labels": len(labels),
           "rejectedAsNotKeepers": sum(l["verdict"] == "reject" for l in labels),
           "methods": {}}
    for name, fn in methods.items():
        res = {}
        for split, keep in (("development", lambda l: l["sourceSha256"] not in held_out),
                            ("heldOut", lambda l: l["sourceSha256"] in held_out)):
            rows = []
            by = {}
            for l in usable:
                if not keep(l):
                    continue
                got = fn(l)
                if got is None:
                    continue
                pair = (got[0], got[1], l["owner"]["inSeconds"], l["owner"]["outSeconds"])
                rows.append(pair)
                by.setdefault(f'{l["sport"]}/{l["anchor"].split(" ")[0]}', []).append(pair)
            res[split] = {**_metrics(rows), "bySportAndAnchor": {k: _metrics(v) for k, v in
                                                                 sorted(by.items())}}
        out["methods"][name] = res
    return out

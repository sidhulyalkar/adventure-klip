"""Owner review of proposed highlights (stage D), and what it says about stages A and B.

One page, one clip per item, in a fixed shuffled order. The deck mixes:
  * moments the verifier accepted or was unsure about (what the owner should see first);
  * a sample of moments the verifier REJECTED (does stage B throw good footage away?);
  * the search's random audit windows from footage it did NOT propose (what does stage A miss?).
The page never says which is which, nor what any model thought, so the answers are blind.

For each clip the owner answers keep / maybe / skip, ticks what it shows (an animal by name,
a companion, speed, a jump...), can mark the best part with in/out buttons, and can add a
note. Answers download as JSON; import_answers() stores them as owner labels (kind
"highlight"); evaluate() turns them into search recall, verifier precision and species
accuracy, development and held-out apart.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import html
import json
import os
from pathlib import Path

from . import owner_labels

VERSION = "klip-highlight-review-v1"
ANSWERS_VERSION = "klip-highlight-review-answers-v1"
DECISIONS = ("keep", "maybe", "skip")
WHAT = {
    "snorkeling": ["manta_ray", "eagle_ray", "stingray", "shark", "sea_turtle", "octopus",
                   "dolphin", "eel", "other_animal", "companion", "reef_or_scenery",
                   "nothing_special", "bad_footage"],
    "ski": ["speed", "jump", "powder", "trees", "steep", "friend", "full_run", "crash",
            "scenery", "lift_or_line", "nothing_special", "bad_footage"],
}
ANIMAL_WHAT = {"manta_ray", "eagle_ray", "stingray", "shark", "sea_turtle", "octopus",
               "dolphin", "eel", "other_animal"}
REJECTED_SHARE = 0.15


def _h(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _key(deck_id: str, item_id: str) -> str:
    return sha256(f"{deck_id}|{item_id}".encode()).hexdigest()


def build_deck(candidates: list[dict], audit: list[dict], verified: dict[str, dict], *,
               rejected_share: float = REJECTED_SHARE, max_items: int | None = None) -> dict:
    """The review deck: verified and uncertain moments, a sample of rejected ones, and the
    audit windows. `verified` maps candidate id -> stage B record."""
    chosen = []
    rejected = []
    for c in candidates:
        r = verified.get(c["id"])
        if not r:
            continue
        if r["verdict"] in ("verified", "verified_not_airborne", "uncertain"):
            chosen.append((c, r, "proposed"))
        elif r["verdict"] == "rejected":
            rejected.append((c, r, "rejected_sample"))
    rejected.sort(key=lambda x: sha256(f'rejected|{x[0]["id"]}'.encode()).hexdigest())
    if rejected:
        chosen += rejected[:max(3, round(rejected_share * len(rejected)))]
    chosen += [(a, None, "audit") for a in audit]
    items = {}
    for c, r, role in chosen:
        items[c["id"]] = {
            "role": role, "kind": c["kind"], "sport": c["sport"],
            "sourceSha256": c["sourceSha256"], "file": c.get("file"), "day": c.get("day"),
            "outing": c.get("outing"), "heldOut": bool(c.get("heldOut")),
            "startMs": c["startMs"], "endMs": c["endMs"], "peakMs": c.get("peakMs"),
            "searchTop": c.get("top"), "searchScore": c.get("score"),
            "verdict": r["verdict"] if r else None, "verifierAnimal": (r or {}).get("animal"),
            "verifierModel": (r or {}).get("model")}
    if max_items is not None and len(items) > max_items:
        # Keep every audit window and rejected sample; trim the proposed moments by score.
        fixed = {k: v for k, v in items.items() if v["role"] != "proposed"}
        room = max(0, max_items - len(fixed))
        best = sorted((k for k, v in items.items() if v["role"] == "proposed"),
                      key=lambda k: -(items[k]["searchScore"] or 0))[:room]
        items = {**fixed, **{k: items[k] for k in best}}
    deck_id = sha256(json.dumps(sorted(items), sort_keys=True).encode()).hexdigest()[:12]
    order = sorted(items, key=lambda k: _key(deck_id, k))
    return {"version": VERSION, "deck": deck_id, "order": order, "items": items,
            "counts": dict(Counter(v["role"] for v in items.values()))}


_CSS = """
body{font:15px/1.45 system-ui,sans-serif;margin:0 auto;max-width:960px;padding:16px;color:#1b1b1b}
h1{font-size:20px} .item{border:1px solid #bbb;border-radius:8px;padding:12px;margin:16px 0}
.item.done{border-color:#2e7d32} video{width:100%;max-height:60vh;background:#000}
fieldset{border:0;padding:0;margin:8px 0} legend{font-weight:600;margin-bottom:4px}
label{display:inline-block;margin:2px 10px 2px 0;padding:4px 6px;border-radius:4px}
label:focus-within{outline:3px solid #1565c0} input[type=text]{width:100%;font:inherit}
button{font:inherit;padding:4px 10px;margin-right:6px} .meta{color:#444;font-size:13px}
#bar{position:sticky;top:0;background:#fff;padding:8px 0;border-bottom:1px solid #ddd}
"""

_JS = """
const KEY = document.body.dataset.deck, STORE = 'klip-highlight-review-' + KEY;
let answers = {};
try { answers = JSON.parse(localStorage.getItem(STORE) || '{}'); } catch (e) { answers = {}; }
function save() {
  try { localStorage.setItem(STORE, JSON.stringify(answers)); } catch (e) {}
  const n = Object.values(answers).filter(a => a.decision).length;
  document.getElementById('count').textContent = n + ' of ' + TOTAL + ' answered';
}
function entry(id) { return answers[id] = answers[id] || {what: []}; }
function load(item) {
  const id = item.dataset.id, a = answers[id];
  if (!a) return;
  item.querySelectorAll('input[type=radio]').forEach(r => r.checked = r.value === a.decision);
  item.querySelectorAll('input[type=checkbox]').forEach(c => c.checked = (a.what||[]).includes(c.value));
  item.querySelector('input[type=text]').value = a.note || '';
  show(item);
}
function show(item) {
  const a = answers[item.dataset.id] || {}, out = item.querySelector('.marks');
  out.textContent = (a.bestStartMs != null ? 'best part from ' + (a.bestStartMs/1000).toFixed(1) + ' s' : '')
    + (a.bestEndMs != null ? ' to ' + (a.bestEndMs/1000).toFixed(1) + ' s' : '');
  item.classList.toggle('done', !!a.decision);
}
const TOTAL = document.querySelectorAll('.item').length;
document.querySelectorAll('.item').forEach(item => {
  const id = item.dataset.id, start = +item.dataset.start, video = item.querySelector('video');
  load(item);
  item.querySelectorAll('input[type=radio]').forEach(r => r.addEventListener('change', () => {
    entry(id).decision = r.value; show(item); save(); }));
  item.querySelectorAll('input[type=checkbox]').forEach(c => c.addEventListener('change', () => {
    const a = entry(id); a.what = [...item.querySelectorAll('input[type=checkbox]:checked')].map(x => x.value);
    save(); }));
  item.querySelector('input[type=text]').addEventListener('input', e => { entry(id).note = e.target.value; save(); });
  item.querySelector('.in').addEventListener('click', () => {
    entry(id).bestStartMs = Math.round(start + video.currentTime * 1000); show(item); save(); });
  item.querySelector('.out').addEventListener('click', () => {
    entry(id).bestEndMs = Math.round(start + video.currentTime * 1000); show(item); save(); });
  item.querySelector('.clear').addEventListener('click', () => {
    const a = entry(id); delete a.bestStartMs; delete a.bestEndMs; show(item); save(); });
});
document.getElementById('download').addEventListener('click', () => {
  const blob = new Blob([JSON.stringify({version: 'ANSWERS_VERSION', deck: KEY, answers}, null, 1)],
                        {type: 'application/json'});
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob); link.download = 'highlight-answers-' + KEY + '.json';
  link.click();
});
save();
"""


def page(deck: dict, base: Path, clip_of) -> str:
    """clip_of(item id) -> Path of the item's clip (cut to [startMs, endMs]) or None."""
    blocks = []
    for n, item_id in enumerate(deck["order"], 1):
        it = deck["items"][item_id]
        clip = clip_of(item_id)
        src = _h(os.path.relpath(Path(clip), base)) if clip else ""
        video = (f'<video preload="none" controls src="{src}" '
                 f'aria-label="Clip {n}"></video>' if clip else
                 '<p role="note">Clip not cut yet: run the verify stage first.</p>')
        group = f"q{n}"
        decision = "".join(
            f'<label><input type="radio" name="{group}-d" value="{d}"> {d}</label>'
            for d in DECISIONS)
        what = "".join(
            f'<label><input type="checkbox" value="{w}"> {w.replace("_", " ")}</label>'
            for w in WHAT.get(it["sport"], []))
        blocks.append(
            f'<section class="item" data-id="{_h(item_id)}" data-start="{it["startMs"]}" '
            f'aria-labelledby="{group}-h">'
            f'<h2 id="{group}-h">Clip {n}</h2>'
            f'<p class="meta">{_h(it.get("file"))} at {it["startMs"] / 1000:.0f}-'
            f'{it["endMs"] / 1000:.0f} s · {_h(it.get("outing") or it.get("day") or "")}</p>'
            f'{video}'
            f'<fieldset><legend>Worth keeping?</legend>{decision}</fieldset>'
            f'<fieldset><legend>What does it show? (tick all)</legend>{what}</fieldset>'
            f'<fieldset><legend>Best part (pause the clip at the moment, then press)</legend>'
            f'<button type="button" class="in">Best part starts here</button>'
            f'<button type="button" class="out">Best part ends here</button>'
            f'<button type="button" class="clear">Clear</button> '
            f'<span class="marks" aria-live="polite"></span></fieldset>'
            f'<label style="display:block">Note <input type="text" maxlength="300"></label>'
            f'</section>')
    js = _JS.replace("ANSWERS_VERSION", ANSWERS_VERSION)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Klip highlights review {deck["deck"]}</title><style>{_CSS}</style></head>'
            f'<body data-deck="{deck["deck"]}"><h1>Highlights review</h1>'
            f'<div id="bar"><span id="count" aria-live="polite"></span> '
            f'<button type="button" id="download">Download answers</button></div>'
            f'<p>Some clips were picked by the search, some at random. Judge each on its own: '
            f'would you keep it for a highlight reel?</p>'
            f'{"".join(blocks)}<script>{js}</script></body></html>')


def import_answers(reports: Path, deck: dict, answers_doc: dict, raw: bytes) -> dict:
    if answers_doc.get("version") != ANSWERS_VERSION or answers_doc.get("deck") != deck["deck"]:
        raise ValueError(f"These answers are not for deck {deck['deck']}")
    items, skipped = [], 0
    for item_id, a in (answers_doc.get("answers") or {}).items():
        it = deck["items"].get(item_id)
        if it is None or not isinstance(a, dict) or a.get("decision") not in DECISIONS:
            skipped += 1
            continue
        allowed = set(WHAT.get(it["sport"], []))
        what = sorted(w for w in a.get("what") or [] if w in allowed)
        best = [a.get("bestStartMs"), a.get("bestEndMs")]
        ok = all(type(b) is int and it["startMs"] <= b <= it["endMs"] for b in best)
        items.append({"item": item_id, "sourceSha256": it["sourceSha256"],
                      "startMs": it["startMs"], "endMs": it["endMs"], "sport": it["sport"],
                      "decision": a["decision"], "what": what,
                      "bestStartMs": best[0] if ok and best[0] < best[1] else None,
                      "bestEndMs": best[1] if ok and best[0] < best[1] else None,
                      "note": str(a.get("note") or "")[:300]})
    result = owner_labels.append(reports, "highlight", deck=deck["deck"],
                                 answers_sha256=sha256(raw).hexdigest(), items=items)
    return {**result, "skipped": skipped}


def evaluate(deck: dict, labels: list[dict], *, audit_window_s: float = 10.0) -> dict:
    """What the owner's answers say about the search and the verifier.

    keepRate per role: proposed (verifier accepted / unsure), rejected_sample, audit. A kept
    audit window is a highlight the search missed; a kept rejected sample is one the verifier
    threw away. Species accuracy compares the verifier's animal with the owner's ticks."""
    mine = {l["item"]: l for l in labels if l["item"] in deck["items"]}
    out = {"version": VERSION, "deck": deck["deck"], "answered": len(mine),
           "items": len(deck["items"])}
    for split in ("development", "heldOut"):
        rows = [(deck["items"][k], l) for k, l in mine.items()
                if deck["items"][k]["heldOut"] == (split == "heldOut")]
        roles: dict[str, Counter] = defaultdict(Counter)
        kinds: dict[str, Counter] = defaultdict(Counter)
        species = Counter()
        for it, l in rows:
            roles[it["role"]][l["decision"]] += 1
            if it["role"] != "audit":
                kinds[it["kind"]][l["decision"]] += 1
            animal = it.get("verifierAnimal")
            if it["kind"] == "animal" and animal:
                ticked = set(l["what"]) & ANIMAL_WHAT
                if animal in ("no_animal", "unclear"):
                    said = "none" if animal == "no_animal" else "unclear"
                    species[f"verifier_{said}_owner_{'animal' if ticked else 'none'}"] += 1
                elif ticked:
                    species["right" if animal in ticked else "wrong_species"] += 1
                else:
                    species["owner_saw_no_animal"] += 1

        def rate(c: Counter, *keys):
            n = sum(c.values())
            return round(sum(c[k] for k in keys) / n, 3) if n else None
        audit = roles.get("audit", Counter())
        doc = {"byRole": {r: {**dict(c), "keepRate": rate(c, "keep"),
                              "keepOrMaybeRate": rate(c, "keep", "maybe")}
                          for r, c in sorted(roles.items())},
               "byKind": {k: {**dict(c), "keepOrMaybeRate": rate(c, "keep", "maybe")}
                          for k, c in sorted(kinds.items())},
               "verifierSpecies": dict(species)}
        if audit:
            # Audit windows are a uniform sample of unproposed footage, so the kept share
            # times windows-per-hour estimates keepable windows the search never proposed.
            doc["estimatedMissedKeepsPerUnproposedHour"] = round(
                rate(audit, "keep") * 3600 / audit_window_s, 2)
            doc["auditAnswered"] = sum(audit.values())
        out[split] = doc
    return out

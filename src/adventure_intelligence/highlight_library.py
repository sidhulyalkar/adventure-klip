"""The highlights library: one list of every moment the owner has judged worth keeping.

Owner judgments are scattered over the review surfaces built so far:
  * the review app's grid rounds (owner-pairs.jsonl): per screen, the clips ticked to keep
    and the one starred as best; unticked clips on an answered screen were not kept;
  * the review app's tap rounds (same ledger): which of two clips he would rather keep;
  * the reel gallery's trim handles (owner labels, kind "boundary"): ok / adjusted / reject,
    with the owner's in and out points;
  * the highlights review page (owner labels, kind "highlight"): keep / maybe / skip, what
    it shows, the best part, a 1-5 rating and a note.
This module gathers them into moments: overlapping windows of one recording are one moment,
its evidence listed with provenance. The strongest owner evidence sets its tier:

    owner_best    starred best of a grid screen, or rated 5
    owner_keep    ticked keep, trimmed ok/adjusted, kept on the review page, or rated 4
    owner_maybe   maybe on the review page, or rated 3
    to_review     only model proposals (verified by stage B), never judged by the owner

A moment the owner only ever skipped is excluded (and listed as such). A moment kept once
and skipped once is kept and flagged as a conflict, for the owner to settle on the page.

The library is derived: it never rewrites a ledger and is rebuilt from them each time.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from hashlib import sha256
import json
import re

VERSION = "klip-highlight-library-v1"
TIERS = ("owner_best", "owner_keep", "owner_maybe", "to_review")
POSITIVE = {"best": 3, "keep": 2, "maybe": 1}
SAME_MOMENT = 0.5      # overlap, as a share of the shorter window, that makes one moment
SEQUENCE_GAP_MS = 60_000   # kept moments closer than this in one outing play as a sequence
HIGHLIGHT_STARS = 4        # rated this or higher (or starred best): a highlight to show
MEMORY_STARS = 2           # kept but rated this or lower: a personal memory, not a highlight
_DJI = re.compile(r"DJI_(\d{14})")


def _digest(obj) -> str:
    return sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _ev(sha: str, start: int, end: int, judgment: str, source: str, ref: str, **extra) -> dict:
    return {"sourceSha256": sha, "startMs": int(start), "endMs": int(end),
            "judgment": judgment, "source": source, "ref": ref, **extra}


# ---------------------------------------------------------------- evidence from each surface

def grid_evidence(rows: list[dict]) -> list[dict]:
    """The latest answer to each grid screen (an owner can re-answer a screen)."""
    latest = {}
    for r in rows:
        if r.get("version") == "klip-owner-grid-ledger-v1":
            latest[(r["deckSha256"], r["index"])] = r
    out = []
    for (deck, index), r in sorted(latest.items()):
        if r.get("cannotJudge"):
            continue
        for pos, clip in enumerate(r["clips"]):
            judgment = "best" if r.get("best") == pos else "keep" if pos in r["keep"] else "skip"
            out.append(_ev(clip["sourceSha256"], clip["startMs"], clip["endMs"], judgment,
                           "grid", f"{deck[:12]}#{index}",
                           repeat=r.get("repeatOf") is not None))
    return out


def tap_evidence(rows: list[dict]) -> list[dict]:
    """Rather-keep choices: soft preference, never a keep or a skip on its own."""
    latest = {}
    for r in rows:
        if r.get("version") == "klip-owner-pair-ledger-v1" and r.get("choice") in ("left", "right"):
            latest[r.get("pairId") or r["pairEvidenceId"]] = r
    out = []
    for key, r in sorted(latest.items()):
        win, lose = (r["left"], r["right"]) if r["choice"] == "left" else (r["right"], r["left"])
        out.append(_ev(win["sourceSha256"], win["startMs"], win["endMs"], "preferred", "tap", key[:16]))
        out.append(_ev(lose["sourceSha256"], lose["startMs"], lose["endMs"], "not_preferred",
                       "tap", key[:16]))
    return out


def boundary_evidence(labels: list[dict]) -> list[dict]:
    out = []
    for l in labels:
        if l.get("verdict") == "reject":
            # The reel clip was rejected: the window shown to the owner stands for it.
            o = l.get("owner") or {}
            s = o.get("originalInMs")
            e = o.get("originalOutMs")
            if s is None or e is None:
                continue
            out.append(_ev(l["sourceSha256"], s, e, "skip", "trim", l["item"]))
        elif l.get("verdict") in ("ok", "adjusted"):
            o = l["owner"]
            out.append(_ev(l["sourceSha256"], o["originalInMs"], o["originalOutMs"], "keep",
                           "trim", l["item"], trimmed=True))
    return out


def review_evidence(labels: list[dict]) -> list[dict]:
    out = []
    for l in labels:
        stars = l.get("stars")
        judgment = {"keep": "keep", "maybe": "maybe", "skip": "skip"}[l["decision"]]
        if stars == 5:
            judgment = "best"
        elif stars == 4 and judgment != "skip":
            judgment = "keep"
        start, end = l["startMs"], l["endMs"]
        trimmed = l.get("bestStartMs") is not None and l.get("bestEndMs") is not None
        if trimmed:
            start, end = l["bestStartMs"], l["bestEndMs"]
        out.append(_ev(l["sourceSha256"], start, end, judgment, "review", l["item"],
                       trimmed=trimmed, what=l.get("what") or [], stars=stars,
                       note=l.get("note") or None, moreBefore=bool(l.get("moreBefore")),
                       moreAfter=bool(l.get("moreAfter"))))
    return out


def proposal_evidence(candidates: list[dict], verified: dict[str, dict]) -> list[dict]:
    out = []
    for c in candidates:
        r = verified.get(c["id"])
        if r and r.get("verdict") in ("verified", "verified_not_airborne"):
            out.append(_ev(c["sourceSha256"], c["startMs"], c["endMs"], "proposed", "search",
                           c["id"], kind=c["kind"],
                           what=[r["animal"]] if r.get("animal") else []))
    return out


# ---------------------------------------------------------------- merging

def _overlap(a: dict, b: dict) -> float:
    inter = min(a["endMs"], b["endMs"]) - max(a["startMs"], b["startMs"])
    shorter = min(a["endMs"] - a["startMs"], b["endMs"] - b["startMs"])
    return inter / shorter if shorter > 0 and inter > 0 else 0.0


def cluster(evidence: list[dict]) -> list[list[dict]]:
    """Evidence on one recording whose windows overlap enough is one moment."""
    out = []
    by_sha = defaultdict(list)
    for e in evidence:
        if e["endMs"] > e["startMs"]:
            by_sha[e["sourceSha256"]].append(e)
    for sha in sorted(by_sha):
        items = sorted(by_sha[sha], key=lambda e: (e["startMs"], e["endMs"]))
        parent = list(range(len(items)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                if items[j]["startMs"] >= items[i]["endMs"]:
                    break
                if _overlap(items[i], items[j]) >= SAME_MOMENT:
                    parent[find(j)] = find(i)
        groups = defaultdict(list)
        for i, e in enumerate(items):
            groups[find(i)].append(e)
        out += [groups[k] for k in sorted(groups)]
    return out


def _window(group: list[dict]) -> tuple[int, int, str]:
    """Owner trims first, then the tightest owner-judged window, then the proposal."""
    def span(e):
        return e["endMs"] - e["startMs"]
    trims = [e for e in group if e.get("trimmed") and e["judgment"] in POSITIVE]
    if trims:
        e = max(trims, key=lambda e: (e["source"] == "review", -span(e)))
        return e["startMs"], e["endMs"], "owner_trim"
    owner = [e for e in group if e["judgment"] in POSITIVE]
    if owner:
        e = min(owner, key=span)
        return e["startMs"], e["endMs"], "owner_judged"
    e = min(group, key=span)
    return e["startMs"], e["endMs"], "proposal"


def moment(group: list[dict]) -> dict:
    counts = Counter(e["judgment"] for e in group)
    stars = [e["stars"] for e in group if e.get("stars")]
    if counts["best"]:
        tier = "owner_best"
    elif counts["keep"]:
        tier = "owner_keep"
    elif counts["maybe"]:
        tier = "owner_maybe"
    elif counts["skip"]:
        tier = None                       # judged, never kept
    elif counts["proposed"]:
        tier = "to_review"
    else:
        tier = None                       # only soft tap preferences
    start, end, basis = _window(group)
    sha = group[0]["sourceSha256"]
    rating = round(sum(stars) / len(stars), 2) if stars else None
    return {
        "id": sha256(f"{VERSION}|{sha}|{start}|{end}".encode()).hexdigest()[:16],
        "sourceSha256": sha, "startMs": start, "endMs": end, "windowBasis": basis,
        "tier": tier,
        "conflict": bool((counts["best"] or counts["keep"]) and counts["skip"]),
        "judgments": dict(counts),
        "stars": rating,
        "purpose": purpose(tier, rating),
        "wantMore": {"before": any(e.get("moreBefore") for e in group),
                     "after": any(e.get("moreAfter") for e in group)},
        "what": sorted({w for e in group for w in e.get("what") or []}),
        "notes": [e["note"] for e in group if e.get("note")],
        "preferredOver": counts["preferred"], "lostTo": counts["not_preferred"],
        "evidence": [{k: e[k] for k in ("source", "ref", "judgment", "startMs", "endMs")}
                     for e in group],
    }


def purpose(tier: str | None, stars: float | None) -> str | None:
    """What a kept moment is for, from the owner's rating: a highlight to show (starred best
    or 4-5), a personal memory (kept but rated 1-2), or kept without saying (unrated or 3)."""
    if tier not in ("owner_best", "owner_keep", "owner_maybe"):
        return None
    if tier == "owner_best" or (stars is not None and stars >= HIGHLIGHT_STARS):
        return "highlight"
    if tier == "owner_maybe":
        return "maybe"
    if stars is not None and stars <= MEMORY_STARS:
        return "memory"
    return "keep"


def clock_ms(file: str | None) -> int | None:
    """Milliseconds since 2000 at the start of a DJI recording, from its file name."""
    m = _DJI.search(file or "")
    if not m:
        return None
    try:
        t = datetime.strptime(m.group(1), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return (t - datetime(2000, 1, 1)) // timedelta(milliseconds=1)


def link_sequences(moments: list[dict], gap_ms: int = SEQUENCE_GAP_MS) -> list[dict]:
    """Owner-kept moments of one outing that follow each other within gap_ms (on one
    recording, or across consecutive recordings by their file-name clocks) form a sequence:
    a line through a park, the run before a crash. Sets moment["sequence"] = {id, index,
    of} on members of sequences of two or more and returns the sequences."""
    rows = []
    for m in moments:
        if m["tier"] in ("owner_best", "owner_keep", "owner_maybe"):
            base = clock_ms(m.get("file"))
            key = m.get("outing") or m.get("day") or m["sourceSha256"]
            if base is None:          # without a clock, only one recording can be ordered
                key, base = f'{key}|{m["sourceSha256"]}', 0
            rows.append((key, base + m["startMs"], base + m["endMs"], m))
    rows.sort(key=lambda r: (r[0], r[1]))
    chains, last = [], None
    for key, a, b, m in rows:
        if last and last[0] == key and a - last[2] <= gap_ms:
            chains[-1].append(m)
            last = (key, last[1], max(last[2], b))
        else:
            chains.append([m])
            last = (key, a, b)
    out = []
    for chain in chains:
        if len(chain) < 2:
            continue
        sid = sha256("|".join(m["id"] for m in chain).encode()).hexdigest()[:12]
        for i, m in enumerate(chain, 1):
            m["sequence"] = {"id": sid, "index": i, "of": len(chain)}
        out.append({"id": sid, "outing": chain[0].get("outing"), "sport": chain[0].get("sport"),
                    "members": [m["id"] for m in chain],
                    "purpose": "highlight" if any(m["purpose"] == "highlight" for m in chain)
                    else "keep"})
    return out


def score(m: dict) -> float:
    """Ordering inside a tier: owner rating, then net preferences, then how often kept."""
    j = m["judgments"]
    return ((m["stars"] or 0) * 10 + (m["preferredOver"] - m["lostTo"])
            + j.get("best", 0) * 3 + j.get("keep", 0) - j.get("skip", 0))


def build(evidence: list[dict], context: dict[str, dict]) -> dict:
    """context: sha -> {sport, outing, day, file, heldOut, tags} for display and grouping."""
    moments, excluded = [], []
    for group in cluster(evidence):
        m = moment(group)
        m.update({k: (context.get(m["sourceSha256"]) or {}).get(k)
                  for k in ("sport", "outing", "day", "file", "heldOut", "tags")})
        (moments if m["tier"] else excluded).append(m)
    sequences = link_sequences(moments)
    rank = {t: i for i, t in enumerate(TIERS)}
    moments.sort(key=lambda m: (rank[m["tier"]], -score(m), m["sourceSha256"], m["startMs"]))
    by = lambda key: dict(Counter(f'{m["tier"]}' if key == "tier" else m.get(key) or "unknown"
                                  for m in moments))
    owner = [m for m in moments if m["tier"] != "to_review"]
    per_outing = Counter(m.get("outing") or "unknown" for m in owner)
    return {"version": VERSION, "evidenceSha256": _digest(evidence),
            "moments": moments, "excluded": [{k: m[k] for k in ("id", "sourceSha256", "startMs",
                                                                 "endMs", "judgments")}
                                             for m in excluded],
            "counts": {"byTier": by("tier"), "bySport": dict(Counter(
                m.get("sport") or "unknown" for m in owner)),
                       "ownerMoments": len(owner), "conflicts": sum(m["conflict"] for m in moments),
                       "excludedSkipped": len(excluded), "outingsWithOwnerHighlights": len(per_outing),
                       "byPurpose": dict(Counter(m["purpose"] for m in owner)),
                       "evidenceBySource": dict(Counter(e["source"] for e in evidence))},
            "perOuting": dict(per_outing.most_common()),
            "sequences": sequences,
            "isDerivedFromOwnerLedgers": True}


def ranked(library: dict, *, sport: str | None = None, limit: int = 30) -> list[dict]:
    """The best owner moments, best first: tier, then rating and preferences."""
    rows = [m for m in library["moments"] if m["tier"] != "to_review"
            and (sport is None or m.get("sport") == sport)]
    return rows[:limit]


def deck(library: dict, media_of, *, tiers=("owner_best", "owner_keep", "owner_maybe"),
         include_conflicts: bool = True) -> dict:
    """A highlights-review deck of library moments, grouped by sport and outing in time
    order, so the owner can rate (1-5), trim and annotate them. media_of(moment) ->
    (path, mediaStartMs, mediaEndMs) of a clip containing the window, or None."""
    from . import highlight_review as review
    items, media = {}, {}
    for m in library["moments"]:
        if m["tier"] not in tiers and not (include_conflicts and m["conflict"]):
            continue
        found = media_of(m)
        items[m["id"]] = {
            "role": "library", "kind": m["tier"], "sport": m.get("sport") or "unknown",
            "sourceSha256": m["sourceSha256"], "file": m.get("file"), "day": m.get("day"),
            "outing": m.get("outing"), "heldOut": bool(m.get("heldOut")),
            "startMs": m["startMs"], "endMs": m["endMs"], "peakMs": None,
            "mediaStartMs": found[1] if found else m["startMs"],
            "mediaEndMs": found[2] if found else m["endMs"],
            "searchTop": None, "searchScore": None, "verdict": None, "verifierAnimal": None,
            "verifierModel": None, "sequence": m.get("sequence"),
            "clockMs": (clock_ms(m.get("file")) or 0) + m["startMs"]}
        if found:
            media[m["id"]] = str(found[0])
    deck_id = sha256(json.dumps(sorted(items), sort_keys=True).encode()).hexdigest()[:12]
    # In time order within an outing, so the parts of a sequence sit one after another.
    order = sorted(items, key=lambda k: (items[k]["sport"], items[k]["outing"] or "",
                                         items[k]["clockMs"], items[k]["sourceSha256"]))
    return {"version": review.VERSION, "deck": deck_id, "order": order, "items": items,
            "media": media, "counts": dict(Counter(v["kind"] for v in items.values())),
            "library": library["evidenceSha256"][:12]}

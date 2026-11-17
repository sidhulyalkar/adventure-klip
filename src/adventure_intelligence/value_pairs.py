"""Owner preferences as pairs: "I would keep A over B".

Two sources, both human:
- the evidence ledger: a Keep and a Skip from the same outing and goal;
- taps: the owner watched two clips side by side and picked one.

Taps live in their own append-only ledger, one line per answer. An interval
nobody judged never appears on the losing side of a pair: unreviewed footage
is unknown, not worse.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import os
import random

from .value_features import item_id

VERSION = "klip-owner-pair-ledger-v1"
DECK_VERSION = "klip-tap-deck-v1"
ANSWERS_VERSION = "klip-tap-answers-v1"
CHOICES = ("left", "right", "tie", "cannot_tell")
TAP_GOAL = "outing_highlight"
GROUP_MASS = 50.0  # one outing's Keep/Skip pairs count for at most this many


def _side(sha: str, start: int, end: int) -> dict:
    return {"sourceSha256": sha, "startMs": start, "endMs": end}


def _side_id(side: dict) -> str:
    return item_id(side["sourceSha256"], side["startMs"], side["endMs"])


def from_ledger(records: list[dict], group_of, *, cap: int = 400, seed: int = 0) -> list[dict]:
    """Every Keep over every Skip within one outing and one goal.

    `records` are evidence-ledger rows or owner_evidence judgments; both carry
    sourceSha256, startMs, endMs, decision, goal and evidenceId.

    Intervals that overlap in the same recording are not paired: skipping a
    wide window does not rank it below a moment the owner kept inside it.
    """
    buckets: dict[tuple, dict[str, list[dict]]] = {}
    for r in records:
        if r["decision"] in ("keep", "skip"):
            key = (group_of(r["sourceSha256"]), r["goal"])
            buckets.setdefault(key, {"keep": [], "skip": []})[r["decision"]].append(r)
    out = []
    for (group, goal), sides in sorted(buckets.items()):
        made = {}
        for keep in sides["keep"]:
            for skip in sides["skip"]:
                same = keep["sourceSha256"] == skip["sourceSha256"]
                if same and min(keep["endMs"], skip["endMs"]) > max(keep["startMs"], skip["startMs"]):
                    continue
                left = _side(keep["sourceSha256"], keep["startMs"], keep["endMs"])
                right = _side(skip["sourceSha256"], skip["startMs"], skip["endMs"])
                made.setdefault((_side_id(left), _side_id(right)), {
                    "left": left, "right": right, "outcome": 1.0,
                    "tier": "owner_keep_over_skip", "group": group, "groups": [group],
                    "goal": goal, "evalEligible": True,
                    "evidenceIds": [keep["evidenceId"], skip["evidenceId"]]})
        rows = [made[k] for k in sorted(made)]
        if len(rows) > cap:
            rows = random.Random(f"{seed}:{group}:{goal}").sample(rows, cap)
        for row in rows:
            row["weight"] = min(1.0, GROUP_MASS / len(rows))
        out += rows
    return out


_OUTCOME = {"left": 1.0, "right": 0.0, "tie": 0.5}


def from_taps(rows: list[dict], group_of) -> list[dict]:
    """Latest answer per deck item. Ties train as 0.5; 'cannot tell' is dropped.

    A consistency repeat is not a second pair: it is averaged into the
    original answer, so an owner who answered both ways yields a tie.
    """
    latest = {}
    for row in rows:
        if row.get("version", VERSION) == VERSION:
            latest[(row["deckSha256"], row["index"])] = row
    outcome = {k: _OUTCOME.get(r["choice"]) for k, r in latest.items()}
    for (deck, _), row in latest.items():
        origin = (deck, row["repeatOf"])
        if row["repeatOf"] is None or origin not in latest:
            continue
        again = _OUTCOME.get(row["choice"])  # sides were swapped
        if again is not None:
            first = outcome[origin]
            outcome[origin] = 1.0 - again if first is None else (first + 1.0 - again) / 2
    out = []
    for key, row in latest.items():
        if row["repeatOf"] is not None and (key[0], row["repeatOf"]) in latest:
            continue
        if outcome[key] is None:
            continue
        group = group_of(row["left"]["sourceSha256"])
        if group != group_of(row["right"]["sourceSha256"]):
            continue  # an outing roster edit split this pair; it no longer ranks within one outing
        out.append({
            "left": row["left"], "right": row["right"],
            "outcome": outcome[key],
            "tier": "owner_tap", "group": group, "groups": [group], "goal": TAP_GOAL,
            "weight": 1.0,
            # Pairs picked because a model was unsure are harder than average
            # and would understate accuracy, so they train but are not scored.
            "evalEligible": row["sampling"] == "random",
            "evidenceIds": [row["pairEvidenceId"]]})
    return sorted(out, key=lambda p: (p["group"], _side_id(p["left"]), _side_id(p["right"])))


def self_consistency(rows: list[dict]) -> dict:
    """How often the owner gave the same answer to a pair shown twice, sides swapped.

    This is the ceiling for any model's agreement with these taps.
    """
    by_deck: dict[tuple, dict] = {}
    for row in rows:
        if row.get("version", VERSION) == VERSION:
            by_deck[(row["deckSha256"], row["index"])] = row
    flip = {"left": "right", "right": "left", "tie": "tie", "cannot_tell": "cannot_tell"}
    repeats = same = decisive = decisive_same = 0
    for (deck, _), row in by_deck.items():
        first = by_deck.get((deck, row["repeatOf"])) if row["repeatOf"] is not None else None
        if first is None:
            continue
        repeats += 1
        agree = flip[row["choice"]] == first["choice"]
        same += agree
        if row["choice"] in ("left", "right") and first["choice"] in ("left", "right"):
            decisive += 1
            decisive_same += agree
    return {"repeatedPairs": repeats,
            "sameAnswer": round(same / repeats, 4) if repeats else None,
            "decisiveRepeats": decisive,
            "decisiveSameAnswer": round(decisive_same / decisive, 4) if decisive else None}


def from_direct(pairs: list[dict], group_of) -> list[dict]:
    """Owner A/B choices made elsewhere (Studio, blind pair study).

    A pair from two outings still teaches preference, so it trains whenever
    neither of its outings is held out. It never scores a model, because the
    evaluation question is a choice within one outing.
    """
    out = []
    for p in pairs:
        groups = sorted({group_of(p["left"]["sourceSha256"]),
                         group_of(p["right"]["sourceSha256"])})
        same = len(groups) == 1
        out.append({"left": p["left"], "right": p["right"], "outcome": p["outcome"],
                    "tier": "owner_" + p["origin"].replace("-", "_"),
                    "group": groups[0] if same else "cross:" + "|".join(groups),
                    "groups": groups, "goal": p["goal"], "weight": 1.0,
                    "evalEligible": same, "evidenceIds": [p["evidenceId"]]})
    return out


def from_ratings(judgments: list[dict], group_of, *, min_gap: float = 1.0,
                 cap: int = 200, seed: int = 0) -> list[dict]:
    """Two Keeps rated in the same review session: the clearly better-rated one wins.

    Ratings describe quality aspects, not the keep decision itself, so these
    pairs are weaker: half weight, training only.
    """
    sessions: dict[tuple, list[dict]] = {}
    for j in judgments:
        if j["decision"] == "keep" and len(j.get("ratings") or {}) >= 2:
            key = (group_of(j["sourceSha256"]), j["goal"], j["session"])
            sessions.setdefault(key, []).append(j)
    out = []
    for (group, goal, _), rows in sorted(sessions.items()):
        made = []
        for i, a in enumerate(rows):
            for b in rows[i + 1:]:
                shared = sorted(set(a["ratings"]) & set(b["ratings"]))
                if len(shared) < 2:
                    continue
                gap = (sum(a["ratings"][k] for k in shared)
                       - sum(b["ratings"][k] for k in shared)) / len(shared)
                if abs(gap) < min_gap:
                    continue
                win, lose = (a, b) if gap > 0 else (b, a)
                made.append({
                    "left": _side(win["sourceSha256"], win["startMs"], win["endMs"]),
                    "right": _side(lose["sourceSha256"], lose["startMs"], lose["endMs"]),
                    "outcome": 1.0, "tier": "owner_rating_order", "group": group,
                    "groups": [group], "goal": goal, "weight": 0.5, "evalEligible": False,
                    "evidenceIds": [win["evidenceId"], lose["evidenceId"]]})
        if len(made) > cap:
            made = random.Random(f"{seed}:{group}:{goal}").sample(made, cap)
        out += made
    return out


# ---- grid: four clips from one outing --------------------------------------------

GRID_DECK_VERSION = "klip-grid-deck-v1"
GRID_ANSWERS_VERSION = "klip-grid-answers-v1"
GRID_VERSION = "klip-owner-grid-ledger-v1"
GRID_GOAL = "outing_highlight"


def build_grid(items: dict[str, dict], *, experiment: str, screens: int = 30,
               per_screen: int = 4, repeats: int = 4, seed: int = 0,
               max_seconds: float = 60.0) -> dict:
    """Screens of segments drawn uniformly at random from one outing and sport.

    Uniform draws over every segment of the timeline (proposals, controls and
    unreviewed alike) make the keep rate and the recall measured from these
    answers unbiased estimates for the whole outing.
    """
    if not (1 <= screens <= 200 and 2 <= per_screen <= 6 and 0 <= repeats <= screens):
        raise ValueError("Grid needs 1..200 screens of 2..6 clips")
    rng = random.Random(f"{GRID_DECK_VERSION}:{seed}:{experiment}")
    pools: dict[tuple, list[dict]] = {}
    for item in items.values():
        if item["hasProxy"] and item["endMs"] - item["startMs"] <= max_seconds * 1000:
            pools.setdefault((item["group"], item["sport"]), []).append(item)
    pools = {k: sorted(v, key=lambda i: i["itemId"]) for k, v in sorted(pools.items())
             if len(v) >= per_screen}
    if not pools:
        raise ValueError(f"No outing has {per_screen} playable segments to compare")
    queues = {}
    for key, pool in pools.items():
        order = list(pool)
        rng.shuffle(order)
        queues[key] = order
    built, keys = [], list(pools)
    while len(built) < screens:
        progressed = False
        for key in keys:
            if len(built) >= screens:
                break
            queue = queues[key]
            if len(queue) < per_screen:  # this outing is used up; reshuffle it
                queue[:] = list(pools[key])
                rng.shuffle(queue)
            clips = [queue.pop() for _ in range(per_screen)]
            built.append({"group": key[0], "sport": key[1], "clips": clips})
            progressed = True
        if not progressed:
            break
    entries = [dict(e, repeatOfEntry=None) for e in built]
    for origin in rng.sample(list(entries), min(repeats, len(entries))):
        at = next(i for i, e in enumerate(entries) if e is origin)
        clips = list(origin["clips"])
        while len(clips) > 1 and clips == origin["clips"]:
            rng.shuffle(clips)
        position = rng.randint(min(len(entries), at + 6), len(entries))
        entries.insert(position, {**origin, "clips": clips, "repeatOfEntry": origin})
    index_of = {id(e): i for i, e in enumerate(entries)}
    return {"version": GRID_DECK_VERSION, "experiment": experiment, "seed": seed,
            "goal": GRID_GOAL,
            "question": "Tick every clip you would keep from this outing. Star the best one.",
            "screens": [{
                "index": i, "group": e["group"], "sport": e["sport"],
                "clips": [_side(c["sourceSha256"], c["startMs"], c["endMs"]) for c in e["clips"]],
                "roles": [c["role"] for c in e["clips"]], "sampling": "random",
                "repeatOf": index_of[id(e["repeatOfEntry"])] if e["repeatOfEntry"] else None,
            } for i, e in enumerate(entries)],
            "outings": len(pools)}


def grid_records(deck: dict, answers: dict, export_digest: str) -> list[dict]:
    """Validate an exported grid answer file against its deck; one row per screen."""
    digest = deck_digest(deck)
    if (not isinstance(answers, dict) or answers.get("version") != GRID_ANSWERS_VERSION
            or answers.get("deckSha256") != digest or not isinstance(answers.get("answers"), list)):
        raise ValueError("Answers do not belong to this deck")
    out, seen = [], set()
    for a in answers["answers"]:
        index = a.get("index") if isinstance(a, dict) else None
        if type(index) is not int or not 0 <= index < len(deck["screens"]) or index in seen:
            raise ValueError("Invalid or duplicate grid answer")
        seen.add(index)
        screen = deck["screens"][index]
        n = len(screen["clips"])
        skipped = a.get("cannotJudge") is True
        keep, best = a.get("keep"), a.get("best")
        if (not isinstance(keep, list) or any(type(k) is not int or not 0 <= k < n for k in keep)
                or len(set(keep)) != len(keep)
                or (best is not None and (type(best) is not int or best not in keep))):
            raise ValueError("Grid answer must tick clip positions and star a ticked clip")
        if skipped:
            keep, best = [], None
        body = {"version": GRID_VERSION, "deckSha256": digest, "index": index,
                "clips": screen["clips"], "roles": screen["roles"],
                "keep": sorted(keep), "best": best, "cannotJudge": skipped,
                "sampling": screen["sampling"], "repeatOf": screen["repeatOf"],
                "goal": deck["goal"], "reviewer": "owner", "exportSha256": export_digest}
        body["pairEvidenceId"] = sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]
        out.append(body)
    return out


def _grid_screens(rows: list[dict]) -> dict[tuple, dict]:
    latest = {}
    for row in rows:
        if row.get("version") == GRID_VERSION:
            latest[(row["deckSha256"], row["index"])] = row
    return latest


def grid_judgments(rows: list[dict]) -> list[dict]:
    """Keep/skip for each clip shown. A clip answered both ways on a repeat is dropped."""
    votes: dict[str, dict] = {}
    for row in _grid_screens(rows).values():
        if row["cannotJudge"]:
            continue
        for k, clip in enumerate(row["clips"]):
            key = _side_id(clip)
            entry = votes.setdefault(key, {"clip": clip, "decisions": set(), "ids": [],
                                           "sampling": row["sampling"]})
            entry["decisions"].add("keep" if k in row["keep"] else "skip")
            entry["ids"].append(f'{row["pairEvidenceId"]}:{k}')
    out = []
    for _, entry in sorted(votes.items()):
        if len(entry["decisions"]) != 1:
            continue
        clip = entry["clip"]
        out.append({"sourceSha256": clip["sourceSha256"], "startMs": clip["startMs"],
                    "endMs": clip["endMs"], "decision": next(iter(entry["decisions"])),
                    "selection": "random_segment_sample", "goal": GRID_GOAL,
                    "origin": "grid", "session": "grid", "ratings": {},
                    "evidenceId": sha256("|".join(sorted(entry["ids"])).encode()).hexdigest()[:32]})
    return out


def from_grid(rows: list[dict], group_of) -> list[dict]:
    """Within one screen: each ticked clip over each unticked one; the star over other ticks.

    One screen of four can give up to six orderings and four keep/skip labels.
    A repeat screen adds no pairs of its own; where it contradicts the
    original, that screen's star pairs are dropped and contradicted clips are
    left out of the keep/skip pairs.
    """
    screens = _grid_screens(rows)
    contradicted, bad_star = set(), set()
    for (deck, _), row in screens.items():
        origin = screens.get((deck, row["repeatOf"])) if row["repeatOf"] is not None else None
        if origin is None or origin["cannotJudge"] or row["cannotJudge"]:
            continue
        first = {_side_id(c): k in origin["keep"] for k, c in enumerate(origin["clips"])}
        for k, c in enumerate(row["clips"]):
            if first.get(_side_id(c)) != (k in row["keep"]):
                contradicted.add(_side_id(c))
        a = _side_id(origin["clips"][origin["best"]]) if origin["best"] is not None else None
        b = _side_id(row["clips"][row["best"]]) if row["best"] is not None else None
        if a != b:
            bad_star.add((deck, row["repeatOf"]))
    out, seen = [], set()
    for (deck, index), row in sorted(screens.items()):
        if row["cannotJudge"] or row["repeatOf"] is not None:
            continue
        group = group_of(row["clips"][0]["sourceSha256"])
        if any(group_of(c["sourceSha256"]) != group for c in row["clips"]):
            continue
        clips = [(k, c) for k, c in enumerate(row["clips"]) if _side_id(c) not in contradicted]
        kept = [c for k, c in clips if k in row["keep"]]
        dropped = [c for k, c in clips if k not in row["keep"]]
        made = [(w, lose, "owner_grid_keep") for w in kept for lose in dropped]
        if row["best"] is not None and (deck, index) not in bad_star:
            star = row["clips"][row["best"]]
            made += [(star, other, "owner_grid_best") for other in kept
                     if _side_id(other) != _side_id(star)]
        for win, lose, tier in made:
            key = (_side_id(win), _side_id(lose))
            if key in seen or _side_id(win) == _side_id(lose):
                continue
            seen.add(key)
            out.append({"left": win, "right": lose, "outcome": 1.0, "tier": tier,
                        "group": group, "groups": [group], "goal": GRID_GOAL, "weight": 1.0,
                        "evalEligible": row["sampling"] == "random",
                        "evidenceIds": [row["pairEvidenceId"]]})
    return out


def grid_consistency(rows: list[dict]) -> dict:
    """Agreement between a screen and its shuffled repeat: per clip keep, and the star."""
    screens = _grid_screens(rows)
    clips = same = stars = same_star = 0
    for (deck, _), row in screens.items():
        origin = screens.get((deck, row["repeatOf"])) if row["repeatOf"] is not None else None
        if origin is None or origin["cannotJudge"] or row["cannotJudge"]:
            continue
        first = {_side_id(c): k in origin["keep"] for k, c in enumerate(origin["clips"])}
        for k, c in enumerate(row["clips"]):
            clips += 1
            same += first[_side_id(c)] == (k in row["keep"])
        if origin["best"] is not None or row["best"] is not None:
            stars += 1
            same_star += (origin["best"] is not None and row["best"] is not None
                          and _side_id(origin["clips"][origin["best"]])
                          == _side_id(row["clips"][row["best"]]))
    return {"repeatedClips": clips,
            "sameKeepDecision": round(same / clips, 4) if clips else None,
            "repeatedStars": stars,
            "sameStar": round(same_star / stars, 4) if stars else None}


# ---- tap deck -----------------------------------------------------------------

def build_deck(items: dict[str, dict], *, experiment: str, pairs: int = 130,
               repeats: int = 20, seed: int = 0, max_seconds: float = 60.0,
               scores: dict[str, float] | None = None) -> dict:
    """Blind pairs of playable segments from the same outing and sport.

    Without `scores` every pair is a uniform random draw, so the answers can
    both train and test. With `scores` (a fitted head), half the pairs are the
    ones the model separates least; those are marked and used for training only.
    Ranked proposals and unranked controls are mixed and their role is hidden,
    which is how misses of the proposal ranking become visible.
    """
    if not (1 <= pairs <= 500 and 0 <= repeats <= pairs):
        raise ValueError("Deck needs 1..500 pairs and no more repeats than pairs")
    rng = random.Random(f"{DECK_VERSION}:{seed}:{experiment}")
    pools: dict[tuple, list[dict]] = {}
    skipped_long = 0
    for item in items.values():
        if not item["hasProxy"]:
            continue
        if item["endMs"] - item["startMs"] > max_seconds * 1000:
            skipped_long += 1
            continue
        pools.setdefault((item["group"], item["sport"]), []).append(item)
    pools = {k: sorted(v, key=lambda i: i["itemId"]) for k, v in sorted(pools.items())
             if len(v) >= 2}
    if not pools:
        raise ValueError("No outing has two playable segments to compare")
    candidates = []
    for pool in pools.values():
        every = [(a, b) for i, a in enumerate(pool) for b in pool[i + 1:]]
        rng.shuffle(every)
        candidates.append(every)
    drawn, seen = [], set()
    # Round-robin over outings so no single day supplies most of the deck.
    while len(drawn) < pairs * (4 if scores else 1) and any(candidates):
        for every in candidates:
            if every:
                a, b = every.pop()
                drawn.append((a, b))
    chosen = []
    if scores:
        def margin(pair):
            a, b = (scores.get(p["itemId"]) for p in pair)
            return float("inf") if a is None or b is None else abs(a - b)
        hard = sorted(drawn, key=margin)[:pairs // 2]
        hard_ids = {(a["itemId"], b["itemId"]) for a, b in hard}
        rest = [p for p in drawn if (p[0]["itemId"], p[1]["itemId"]) not in hard_ids]
        rng.shuffle(rest)
        chosen = [(p, "uncertain") for p in hard] + [(p, "random") for p in rest[:pairs - len(hard)]]
        rng.shuffle(chosen)
    else:
        chosen = [(p, "random") for p in drawn[:pairs]]
    entries = []
    for (a, b), sampling in chosen:
        if rng.random() < 0.5:
            a, b = b, a
        key = tuple(sorted((a["itemId"], b["itemId"])))
        if key in seen:
            continue
        seen.add(key)
        entries.append({"left": a, "right": b, "sampling": sampling})
    # Repeats come back with sides swapped, ten or more pairs later where the deck allows.
    for origin in rng.sample(list(entries), min(repeats, len(entries))):
        at = next(i for i, e in enumerate(entries) if e is origin)
        position = rng.randint(min(len(entries), at + 10), len(entries))
        entries.insert(position, {"left": origin["right"], "right": origin["left"],
                                  "sampling": origin["sampling"], "repeatOfEntry": origin})
    index_of = {id(e): i for i, e in enumerate(entries)}
    deck_pairs = []
    for i, e in enumerate(entries):
        origin = e.get("repeatOfEntry")
        deck_pairs.append({
            "index": i,
            "left": _side(e["left"]["sourceSha256"], e["left"]["startMs"], e["left"]["endMs"]),
            "right": _side(e["right"]["sourceSha256"], e["right"]["startMs"], e["right"]["endMs"]),
            "group": e["left"]["group"], "sport": e["left"]["sport"],
            "roles": [e["left"]["role"], e["right"]["role"]],
            "sampling": e["sampling"],
            "repeatOf": index_of[id(origin)] if origin is not None else None})
    return {"version": DECK_VERSION, "experiment": experiment, "seed": seed,
            "goal": TAP_GOAL, "question": "Which would you rather keep from this outing?",
            "pairs": deck_pairs, "uniquePairs": len(deck_pairs) - sum(
                p["repeatOf"] is not None for p in deck_pairs),
            "outings": len(pools), "skippedLongerThanSeconds": {
                "limit": max_seconds, "segments": skipped_long},
            "usesModelScores": bool(scores)}


def deck_digest(deck: dict) -> str:
    return sha256(json.dumps(deck, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def tap_records(deck: dict, answers: dict, export_digest: str) -> list[dict]:
    """Validate an exported answer file against its deck and return ledger rows."""
    digest = deck_digest(deck)
    if (not isinstance(answers, dict) or answers.get("version") != ANSWERS_VERSION
            or answers.get("deckSha256") != digest or not isinstance(answers.get("answers"), list)):
        raise ValueError("Answers do not belong to this deck")
    out, seen = [], set()
    for a in answers["answers"]:
        if (not isinstance(a, dict) or type(a.get("index")) is not int
                or not 0 <= a["index"] < len(deck["pairs"]) or a["index"] in seen
                or a.get("choice") not in CHOICES):
            raise ValueError("Invalid or duplicate tap answer")
        seen.add(a["index"])
        pair = deck["pairs"][a["index"]]
        body = {"version": VERSION, "deckSha256": digest, "index": pair["index"],
                "left": pair["left"], "right": pair["right"], "choice": a["choice"],
                "sampling": pair["sampling"], "repeatOf": pair["repeatOf"],
                "roles": pair["roles"], "goal": deck["goal"], "reviewer": "owner",
                "exportSha256": export_digest}
        body["pairEvidenceId"] = sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:32]
        out.append(body)
    return out


def load(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if any(r.get("version") not in (VERSION, GRID_VERSION) for r in rows):
        raise ValueError("Pair ledger contains records from an unknown version")
    return rows


def append(path: Path, records: list[dict]) -> dict:
    """Add unseen answers. Existing lines are never rewritten."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Refusing a symlinked ledger")
    seen = {r["pairEvidenceId"] for r in load(path)}
    fresh = [r for r in records if r["pairEvidenceId"] not in seen]
    if fresh:
        with open(path, "a", encoding="utf-8") as handle:
            for r in fresh:
                handle.write(json.dumps(r, sort_keys=True, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return {"added": len(fresh), "alreadyPresent": len(records) - len(fresh)}


def deck_html(deck: dict) -> str:
    """One self-contained page. Shows two clips, hides roles, scores and source IDs."""
    public = {"deckSha256": deck_digest(deck), "question": deck["question"],
              "pairs": [{"index": p["index"],
                         "left": _proxy(p["left"]), "right": _proxy(p["right"])}
                        for p in deck["pairs"]]}
    data = json.dumps(public, separators=(",", ":")).replace("<", "\\u003c")
    return _PAGE.replace("__DECK__", data).replace("__ANSWERS_VERSION__", ANSWERS_VERSION)


def _proxy(side: dict) -> str:
    return f'{side["sourceSha256"]}/proxies/{side["startMs"]}-{side["endMs"]}.mp4'


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Klip taps</title>
<style>
body{font:16px/1.4 system-ui,sans-serif;margin:0;background:#14171a;color:#e8e8e8}
main{max-width:1400px;margin:0 auto;padding:16px}
.clips{display:grid;grid-template-columns:1fr 1fr;gap:12px}
video{width:100%;background:#000;border-radius:6px}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0;align-items:center}
button{font:inherit;padding:10px 16px;border-radius:6px;border:1px solid #556;background:#232a31;color:inherit;cursor:pointer}
button:focus-visible{outline:3px solid #7fb4ff}
progress{width:220px} .muted{color:#9aa4ad} h1{font-size:20px;margin:4px 0}
</style></head><body><main>
<h1 id="question"></h1>
<p class="muted">Keys: &larr; left, &rarr; right, &darr; about equal, &uarr; cannot tell, U undo,
S sound (off / left / right).
Answers are kept in this browser until you export them.</p>
<div class="clips">
<video id="left" muted loop playsinline controls aria-label="Left clip"></video>
<video id="right" muted loop playsinline controls aria-label="Right clip"></video>
</div>
<div class="bar">
<button id="pickLeft">&larr; Left</button><button id="pickTie">&darr; About equal</button>
<button id="pickNone">&uarr; Cannot tell</button><button id="pickRight">Right &rarr;</button>
<button id="undo">Undo</button><button id="speed">Speed 1x</button>
<button id="sound" aria-pressed="false">Sound: off</button>
<progress id="progress" value="0" max="1"></progress>
<span id="status" role="status" aria-live="polite"></span>
<button id="export">Export answers</button>
</div>
</main>
<script id="deck" type="application/json">__DECK__</script>
<script>
'use strict';
const deck=JSON.parse(document.getElementById('deck').textContent);
const key='klip-taps-'+deck.deckSha256;
const el=id=>document.getElementById(id);
let answers=[];
try{answers=JSON.parse(localStorage.getItem(key)||'[]');}catch(e){answers=[];}
if(!Array.isArray(answers))answers=[];
let rate=1,heard='off',exported=answers.length===0;
function sound(){
  heard={off:'left',left:'right',right:'off'}[heard];
  el('left').muted=heard!=='left';el('right').muted=heard!=='right';
  el('sound').textContent='Sound: '+heard;el('sound').setAttribute('aria-pressed',String(heard!=='off'));
}
function save(){try{localStorage.setItem(key,JSON.stringify(answers));}catch(e){}exported=false;}
function show(){
  const n=answers.length,total=deck.pairs.length;
  el('progress').max=total;el('progress').value=n;
  if(n>=total){el('status').textContent='Done: '+total+' pairs. Export your answers.';
    for(const s of['left','right']){el(s).pause();el(s).removeAttribute('src');}return;}
  const pair=deck.pairs[n];
  el('status').textContent='Pair '+(n+1)+' of '+total;
  for(const s of['left','right']){const v=el(s);v.src=pair[s];v.playbackRate=rate;
    v.muted=heard!==s;v.play().catch(()=>{});}
}
function pick(choice){
  if(answers.length>=deck.pairs.length)return;
  answers.push({index:deck.pairs[answers.length].index,choice:choice});save();show();
}
el('question').textContent=deck.question;
el('pickLeft').onclick=()=>pick('left');el('pickRight').onclick=()=>pick('right');
el('pickTie').onclick=()=>pick('tie');el('pickNone').onclick=()=>pick('cannot_tell');
el('sound').onclick=sound;
el('undo').onclick=()=>{answers.pop();save();show();};
el('speed').onclick=()=>{rate=rate===1?2:1;el('speed').textContent='Speed '+rate+'x';
  for(const s of['left','right'])el(s).playbackRate=rate;};
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='BUTTON'&&(e.key===' '||e.key==='Enter'))return;
  const map={ArrowLeft:'left',ArrowRight:'right',ArrowDown:'tie',ArrowUp:'cannot_tell'};
  if(map[e.key]){e.preventDefault();pick(map[e.key]);}
  else if(e.key==='u'||e.key==='U'){answers.pop();save();show();}
  else if(e.key==='s'||e.key==='S'){sound();}
});
el('export').onclick=()=>{
  const body=JSON.stringify({version:'__ANSWERS_VERSION__',deckSha256:deck.deckSha256,
    answers:answers},null,2);
  const link=document.createElement('a');
  link.href=URL.createObjectURL(new Blob([body],{type:'application/json'}));
  link.download='klip-tap-answers-'+deck.deckSha256.slice(0,12)+'.json';
  link.click();URL.revokeObjectURL(link.href);exported=true;
  el('status').textContent='Exported '+answers.length+' answers.';
};
window.addEventListener('beforeunload',e=>{if(!exported){e.preventDefault();e.returnValue='';}});
show();
</script></body></html>
"""


def grid_html(deck: dict) -> str:
    """Self-contained grid page. Hides roles, sampling and source identities."""
    public = {"deckSha256": deck_digest(deck), "question": deck["question"],
              "screens": [{"index": s["index"], "clips": [_proxy(c) for c in s["clips"]]}
                          for s in deck["screens"]]}
    data = json.dumps(public, separators=(",", ":")).replace("<", "\\u003c")
    return (_GRID_PAGE.replace("__DECK__", data)
            .replace("__ANSWERS_VERSION__", GRID_ANSWERS_VERSION))


_GRID_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Klip grid review</title>
<style>
body{font:15px/1.4 system-ui,sans-serif;margin:0;background:#14171a;color:#e8e8e8}
main{max-width:1500px;margin:0 auto;padding:12px 16px}
h1{font-size:18px;margin:4px 0} .muted{color:#9aa4ad;margin:2px 0 10px}
.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.tile{position:relative;border:4px solid transparent;border-radius:8px;cursor:pointer;background:#000}
.tile.keep{border-color:#3ecf6e} .tile.best{border-color:#ffc83d}
.tile video{width:100%;display:block;border-radius:4px;aspect-ratio:16/9;background:#000;
  max-height:calc((100vh - 190px) / 2);object-fit:contain}
.mark:empty{display:none}
.badge{position:absolute;top:6px;left:8px;background:#000a;padding:2px 8px;border-radius:4px;font-weight:600}
.mark{position:absolute;top:6px;right:8px;background:#000a;padding:2px 8px;border-radius:4px}
.tile.heard .badge::after{content:" \\1F50A"}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0;align-items:center}
button{font:inherit;padding:8px 14px;border-radius:6px;border:1px solid #556;background:#232a31;color:inherit;cursor:pointer}
button.primary{background:#2c5d3d;border-color:#3ecf6e}
button:focus-visible,.tile:focus-visible{outline:3px solid #7fb4ff}
progress{width:200px}
</style></head><body><main>
<h1 id="question"></h1>
<p class="muted">1-4 or click: keep / not keep &middot; Shift+1-4 or double-click: best &middot;
Enter: next (nothing ticked = none worth keeping) &middot; X: can't judge &middot; U: back &middot;
S: sound &middot; F: speed. Answers stay in this browser until you export them.</p>
<div class="grid" id="grid"></div>
<div class="bar">
<button id="next" class="primary">Next (Enter)</button><button id="cannot">Can't judge (X)</button>
<button id="undo">Back (U)</button><button id="sound" aria-pressed="false">Sound: off</button>
<button id="speed">Speed 1x</button><progress id="progress" value="0" max="1"></progress>
<span id="status" role="status" aria-live="polite"></span>
<button id="export">Export answers</button>
</div>
</main>
<script id="deck" type="application/json">__DECK__</script>
<script>
'use strict';
const deck=JSON.parse(document.getElementById('deck').textContent);
const key='klip-grid-'+deck.deckSha256;
const el=id=>document.getElementById(id);
let answers=[];
try{answers=JSON.parse(localStorage.getItem(key)||'[]');}catch(e){answers=[];}
if(!Array.isArray(answers))answers=[];
let rate=1,heard=-1,keep=new Set(),best=null,exported=answers.length===0;
function save(){try{localStorage.setItem(key,JSON.stringify(answers));}catch(e){}exported=false;}
function tiles(){return [...document.querySelectorAll('.tile')];}
function paint(){
  tiles().forEach((t,k)=>{t.classList.toggle('keep',keep.has(k));t.classList.toggle('best',best===k);
    t.classList.toggle('heard',heard===k);t.setAttribute('aria-pressed',String(keep.has(k)));
    t.querySelector('.mark').textContent=best===k?'\\u2605 best':keep.has(k)?'keep':'';
    t.querySelector('video').muted=heard!==k;});
  const n=answers.length,total=deck.screens.length;
  el('progress').max=total;el('progress').value=n;
  el('next').textContent=keep.size?'Next: keep '+keep.size+' (Enter)':'Next: none worth keeping (Enter)';
  el('sound').textContent='Sound: '+(heard<0?'off':String(heard+1));
  el('sound').setAttribute('aria-pressed',String(heard>=0));
}
function show(){
  const n=answers.length,total=deck.screens.length;
  const grid=el('grid');grid.innerHTML='';keep=new Set();best=null;
  if(n>=total){el('status').textContent='Done: '+total+' screens. Export your answers.';paint();return;}
  el('status').textContent='Screen '+(n+1)+' of '+total;
  deck.screens[n].clips.forEach((src,k)=>{
    const t=document.createElement('div');t.className='tile';t.tabIndex=0;t.setAttribute('role','button');
    t.setAttribute('aria-label','Clip '+(k+1));
    t.innerHTML='<span class="badge">'+(k+1)+'</span><span class="mark"></span>';
    const v=document.createElement('video');v.src=src;v.loop=true;v.muted=true;v.playsInline=true;
    v.playbackRate=rate;t.appendChild(v);v.play().catch(()=>{});
    t.addEventListener('click',()=>toggle(k));t.addEventListener('dblclick',()=>star(k));
    grid.appendChild(t);});
  paint();
}
function toggle(k){if(keep.has(k)){keep.delete(k);if(best===k)best=null;}else keep.add(k);paint();}
function star(k){if(best===k){best=null;}else{best=k;keep.add(k);}paint();}
function submit(cannot){
  if(answers.length>=deck.screens.length)return;
  answers.push({index:deck.screens[answers.length].index,keep:cannot?[]:[...keep].sort(),
    best:cannot?null:best,cannotJudge:!!cannot});save();show();
}
function back(){if(answers.length){answers.pop();save();show();}}
function sound(){const n=tiles().length;heard=heard+1>=n?-1:heard+1;paint();}
el('question').textContent=deck.question;
el('next').onclick=()=>submit(false);el('cannot').onclick=()=>submit(true);
el('undo').onclick=back;el('sound').onclick=sound;
el('speed').onclick=()=>{rate=rate===1?2:1;el('speed').textContent='Speed '+rate+'x';
  tiles().forEach(t=>t.querySelector('video').playbackRate=rate);};
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='BUTTON'&&(e.key===' '||e.key==='Enter'))return;
  const digit=e.code&&e.code.startsWith('Digit')?Number(e.code.slice(5))-1:-1;
  if(digit>=0&&digit<tiles().length){e.preventDefault();e.shiftKey?star(digit):toggle(digit);}
  else if(e.key==='Enter'){e.preventDefault();submit(false);}
  else if(e.key==='x'||e.key==='X'){submit(true);}
  else if(e.key==='u'||e.key==='U'){back();}
  else if(e.key==='s'||e.key==='S'){sound();}
  else if(e.key==='f'||e.key==='F'){el('speed').click();}
});
el('export').onclick=()=>{
  const body=JSON.stringify({version:'__ANSWERS_VERSION__',deckSha256:deck.deckSha256,
    answers:answers},null,2);
  const link=document.createElement('a');
  link.href=URL.createObjectURL(new Blob([body],{type:'application/json'}));
  link.download='klip-grid-answers-'+deck.deckSha256.slice(0,12)+'.json';
  link.click();URL.revokeObjectURL(link.href);exported=true;
  el('status').textContent='Exported '+answers.length+' screens.';
};
window.addEventListener('beforeunload',e=>{if(!exported){e.preventDefault();e.returnValue='';}});
show();
</script></body></html>
"""

"""Highlight verification (stage B): closed questions about each proposed moment.

The search (highlight_search) is cheap and recall-oriented; it over-proposes. Each proposal
gets one schema-constrained VLM call with dense frames around its peak and questions that
have a fixed set of answers (which animal, how many, in which frames, how clear; skiing or
riding a lift, airborne or not, terrain). Free-text description is NOT asked for: the claim
review showed most story errors were vague or invented detail, not wrong categories.

The question is blind: the prompt never says which animal or action the search suspected,
so the verifier cannot simply agree. search-versus-verifier agreement is reported instead.

Verdicts are model output, recorded with model, schema version, frame times and the raw
answer. They decide what reaches the owner first; they never become owner labels.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json

VERSION = "klip-highlight-verify-v1"
FRAMES = 12
CORE_SHARE = 2 / 3          # share of frames inside the tight window around the peak
MARGIN_MS = 50

ANIMALS = ("manta_ray", "eagle_ray", "stingray", "shark", "sea_turtle", "octopus", "dolphin",
           "eel", "other_animal", "no_animal", "unclear")
SPECIES = ANIMALS[:8]
YNU = ("yes", "no", "unclear")
CLARITY = ("clear", "somewhat_clear", "poor")
DISTANCE = ("close", "medium", "far", "not_visible")
SKI_ACTIVITY = ("skiing_downhill", "on_chairlift_or_gondola", "standing_or_in_line",
                "walking_or_indoors", "unclear")
TERRAIN = ("groomed", "powder", "moguls", "trees", "terrain_park", "unclear")
SPEED = ("fast", "moderate", "slow", "stopped", "unclear")

MARINE_KINDS = ("animal", "companion", "reef_scene")
SKI_KINDS = ("ski_jump", "ski_action", "ski_run")
# Verification order when the call budget is short: what the owner asked for first.
PRIORITY = ("animal", "ski_jump", "ski_action", "companion", "reef_scene", "ski_run")


def frame_plan(length_ms: int, peak_ms: int, core_ms: int = 3000, n: int = FRAMES) -> list[int]:
    """Clip-relative frame times: most inside peak +- core_ms, the rest across the clip."""
    end = max(0, length_ms - MARGIN_MS)
    if end <= 0:
        return [0]
    peak = min(max(peak_ms, 0), end)
    n_core = max(1, round(n * CORE_SHARE))
    lo, hi = max(0, peak - core_ms), min(end, peak + core_ms)
    core = [round(lo + (hi - lo) * (i + 0.5) / n_core) for i in range(n_core)]
    rest = n - n_core
    wide = [round(end * (i + 0.5) / rest) for i in range(rest)] if rest > 0 else []
    times = sorted(set(core + wide))
    out = []
    for t in times:              # frames closer than 200 ms add nothing at 2 s evidence spacing
        if not out or t - out[-1] >= 200:
            out.append(t)
    return out


def _frames_prop(n: int) -> dict:
    return {"type": "array", "maxItems": n,
            "items": {"type": "integer", "enum": list(range(1, max(1, n) + 1))}}


def marine_schema(n: int) -> dict:
    return {"type": "object", "additionalProperties": False,
            "required": ["animal", "otherAnimal", "count", "framesWithAnimal", "distance",
                         "clarity", "snorkelerInFrame"],
            "properties": {
                "animal": {"type": "string", "enum": list(ANIMALS)},
                "otherAnimal": {"type": "string", "maxLength": 40},
                "count": {"type": "integer", "minimum": 0, "maximum": 30},
                "framesWithAnimal": _frames_prop(n),
                "distance": {"type": "string", "enum": list(DISTANCE)},
                "clarity": {"type": "string", "enum": list(CLARITY)},
                "snorkelerInFrame": {"type": "string", "enum": list(YNU)}}}


def ski_schema(n: int) -> dict:
    return {"type": "object", "additionalProperties": False,
            "required": ["activity", "airborne", "framesAirborne", "terrain", "speed",
                         "otherSkierAhead", "clarity"],
            "properties": {
                "activity": {"type": "string", "enum": list(SKI_ACTIVITY)},
                "airborne": {"type": "string", "enum": list(YNU)},
                "framesAirborne": _frames_prop(n),
                "terrain": {"type": "string", "enum": list(TERRAIN)},
                "speed": {"type": "string", "enum": list(SPEED)},
                "otherSkierAhead": {"type": "string", "enum": list(YNU)},
                "clarity": {"type": "string", "enum": list(CLARITY)}}}


def _listing(stamps_ms: list[int]) -> str:
    return ", ".join(f"frame {i} at {t / 1000:.1f}s" for i, t in enumerate(stamps_ms, 1))


def marine_prompt(stamps_ms: list[int], length_ms: int) -> str:
    n = len(stamps_ms)
    return (
        f"These are {n} frames, in time order, from one {length_ms / 1000:.0f}-second clip "
        f"filmed by a snorkeler's underwater action camera: {_listing(stamps_ms)}.\n"
        "Answer only from what is visible in these frames.\n"
        "1. animal: the most notable animal clearly visible in any frame. manta_ray: a very "
        "large ray with head fins; eagle_ray: a ray with white spots on a dark back and a long "
        "tail; stingray: a flat, round-bodied ray, often on or near the sand; sea_turtle; "
        "shark; octopus; dolphin; eel (e.g. a moray). Small reef fish, schools of fish, coral, "
        "sea urchins and people do not count: answer no_animal if nothing else is visible. Use "
        "other_animal (and name it in otherAnimal) for any other large animal. Use unclear if "
        "something may be an animal but you cannot tell what.\n"
        "2. count: how many of that animal are visible at once (0 for no_animal).\n"
        f"3. framesWithAnimal: every frame number (1 to {n}) in which it is visible; empty "
        "for no_animal.\n"
        "4. distance: how far it is from the camera.\n"
        "5. clarity: how clearly it can be seen (water visibility, focus, light).\n"
        "6. snorkelerInFrame: whether another snorkeler or swimmer is in any frame.\n"
        "Do not describe touching or holding anything.")


def ski_prompt(stamps_ms: list[int], length_ms: int) -> str:
    n = len(stamps_ms)
    return (
        f"These are {n} frames, in time order, from one {length_ms / 1000:.0f}-second clip "
        f"filmed by a skier's first-person action camera: {_listing(stamps_ms)}.\n"
        "Answer only from what is visible in these frames.\n"
        "1. activity: what the skier is doing for most of the clip.\n"
        "2. airborne: whether the camera is in the air off a jump or drop in any frame (the "
        "snow falls away below, or the horizon tilts mid-air); framesAirborne lists those "
        f"frame numbers (1 to {n}), empty unless yes.\n"
        "3. terrain: the main kind of snow or terrain being skied.\n"
        "4. speed: how fast the skier appears to move.\n"
        "5. otherSkierAhead: whether another skier is visible skiing ahead or alongside.\n"
        "6. clarity: how clearly the scene can be seen (focus, light, lens).")


def check_marine(a: dict, n: int) -> list[str]:
    out = []
    frames = a.get("framesWithAnimal") or []
    if any(not isinstance(f, int) or not 1 <= f <= n for f in frames):
        out.append(f"framesWithAnimal must be frame numbers 1 to {n}")
    if a.get("animal") == "no_animal":
        if frames:
            out.append("no_animal must list no frames")
        if a.get("count"):
            out.append("no_animal must have count 0")
    elif a.get("animal") in (*SPECIES, "other_animal"):
        if not frames:
            out.append(f"{a['animal']} must list the frames in which it is visible")
        if not a.get("count"):
            out.append(f"{a['animal']} must have a count of at least 1")
    if a.get("animal") == "other_animal" and not (a.get("otherAnimal") or "").strip():
        out.append("other_animal must name the animal in otherAnimal")
    return out


def check_ski(a: dict, n: int) -> list[str]:
    out = []
    frames = a.get("framesAirborne") or []
    if any(not isinstance(f, int) or not 1 <= f <= n for f in frames):
        out.append(f"framesAirborne must be frame numbers 1 to {n}")
    if a.get("airborne") == "yes" and not frames:
        out.append("airborne yes must list the airborne frames")
    if a.get("airborne") != "yes" and frames:
        out.append("framesAirborne must be empty unless airborne is yes")
    return out


def question(kind: str):
    """(schema, prompt, check) builders for a candidate kind."""
    if kind in MARINE_KINDS:
        return marine_schema, marine_prompt, check_marine
    if kind in SKI_KINDS:
        return ski_schema, ski_prompt, check_ski
    raise ValueError(f"No verification questions for kind {kind}")


def verdict(candidate: dict, answer: dict | None) -> dict:
    """What the answer means for this proposal: verified, rejected or uncertain, and why.
    A poor-clarity sighting is kept as uncertain: the owner may still want it."""
    if not answer:
        return {"verdict": "unanswered", "reason": "no accepted answer"}
    kind = candidate["kind"]
    clear = answer.get("clarity") != "poor"
    if kind == "animal":
        animal = answer.get("animal")
        if animal in (*SPECIES, "other_animal"):
            agrees = animal == candidate.get("top")
            return {"verdict": "verified" if clear else "uncertain",
                    "reason": f"{animal} in {len(answer.get('framesWithAnimal') or [])} frames"
                              + ("" if clear else ", poor clarity"),
                    "animal": animal, "searchAgrees": agrees}
        return {"verdict": "rejected" if animal == "no_animal" else "uncertain",
                "reason": animal, "animal": animal, "searchAgrees": False}
    if kind == "companion":
        ok = answer.get("snorkelerInFrame") == "yes"
        return {"verdict": "verified" if ok and clear else "rejected" if not ok else "uncertain",
                "reason": f"snorkeler in frame: {answer.get('snorkelerInFrame')}"}
    if kind == "reef_scene":
        c = answer.get("clarity")
        return {"verdict": {"clear": "verified", "poor": "rejected"}.get(c, "uncertain"),
                "reason": f"clarity {c}"}
    activity = answer.get("activity")
    if activity in ("on_chairlift_or_gondola", "standing_or_in_line", "walking_or_indoors"):
        return {"verdict": "rejected", "reason": activity}
    if activity != "skiing_downhill":
        return {"verdict": "uncertain", "reason": activity}
    if kind == "ski_jump":
        air = answer.get("airborne")
        return {"verdict": "verified" if air == "yes" and clear else
                "uncertain" if air == "unclear" or not clear else "verified_not_airborne",
                "reason": f"airborne {air}", "airborne": air}
    return {"verdict": "verified" if clear else "uncertain",
            "reason": f"skiing, {answer.get('speed')}, {answer.get('terrain')}"}


def queue(candidates: list[dict], *, kinds=PRIORITY, done: set[str] = frozenset()) -> list[dict]:
    """Candidates still to verify, in priority order, best score first within a kind."""
    rank = {k: i for i, k in enumerate(kinds)}
    todo = [c for c in candidates if c["kind"] in rank and c["id"] not in done]
    return sorted(todo, key=lambda c: (rank[c["kind"]], -(c.get("score") or 0), c["id"]))


def ask(candidate: dict, images: list[str], stamps_ms: list[int], length_ms: int, *,
        model: str, transport=None, timeout: int = 300) -> dict:
    """One verification call through the story harness's checked-JSON caller."""
    from .story_harness import call_json
    schema_of, prompt_of, check_of = question(candidate["kind"])
    n = len(stamps_ms)
    messages = [{"role": "user", "content": prompt_of(stamps_ms, length_ms), "images": images}]
    rec = call_json(model, messages, schema=schema_of(n), check=lambda a: check_of(a, n),
                    transport=transport, timeout=timeout, output_budget=512)
    answer = rec.get("result") if rec["status"] in ("accepted", "repaired") else None
    return {"version": VERSION, "candidateId": candidate["id"], "kind": candidate["kind"],
            "sourceSha256": candidate["sourceSha256"], "startMs": candidate["startMs"],
            "endMs": candidate["endMs"], "frameTimesMs": stamps_ms, "model": model,
            "status": rec["status"], "attempts": rec.get("attempts"),
            "violations": rec.get("violations", []), "failureDetail": rec.get("failureDetail"),
            "usage": rec.get("usage"), "answer": answer,
            **verdict(candidate, answer), "isModelOutputNotOwnerJudgment": True}


def summarize(candidates: list[dict], results: dict[str, dict]) -> dict:
    """Verdicts by kind, verifier species versus the search's top concept, and failures."""
    by_kind: dict[str, Counter] = defaultdict(Counter)
    confusion: Counter = Counter()
    for c in candidates:
        r = results.get(c["id"])
        by_kind[c["kind"]][r["verdict"] if r else "not_asked"] += 1
        if r and c["kind"] == "animal" and r.get("animal"):
            confusion[f'{c.get("top")} -> {r["animal"]}'] += 1
    asked = [r for r in results.values()]
    return {"version": VERSION, "byKind": {k: dict(v) for k, v in sorted(by_kind.items())},
            "searchToVerifier": dict(confusion.most_common()),
            "calls": len(asked), "failed": sum(r["status"] == "failed" for r in asked),
            "violating": sum(r["status"] == "violating" for r in asked)}


def digest(obj) -> str:
    return sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()

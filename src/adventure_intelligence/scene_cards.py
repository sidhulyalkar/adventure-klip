"""Scene cards: a local vision-language model describes what a segment shows.

A card holds checkable facts (yes / no / unclear), a people count, the activity it
sees, the animal it names if any, and one caption. It never says whether a segment
is worth keeping: that is learned from the owner's choices. Everything here is a model
observation; nothing becomes owner ground truth.

Same safeguards as the fact-judge panel (judge_panel.py):
- control questions whose honest answer is "no" (this footage is unedited and outdoors);
- a malformed or failed reply is an abstention, never a "no";
- model, prompt fingerprint and frame times are kept with every card;
- several model families can be asked; their facts are averaged, disagreement kept.

Cards are written per segment to <folder>/<sha>/<start>-<end>.json and the run is
resumable: a model whose card exists for the same fingerprint is not asked again.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import os
import random

from . import judge_panel as panel

VERSION = "klip-scene-card-v1"
ANSWERS = panel.ANSWERS
FACTS = {
    "underwater": "Is the camera underwater in most frames?",
    "snow_visible": "Is snow visible?",
    "water_surface_visible": "Is the surface of the sea, a lake or a river visible?",
    "other_person_visible": "Is a person other than the camera wearer visible?",
    "face_visible": "Is someone's face clearly visible?",
    "people_interacting": "Are people looking at the camera, waving, talking or doing something together?",
    "animal_visible": "Is an animal (fish, turtle, bird, mammal...) clearly visible?",
    "animal_large_in_frame": "Does an animal fill a large part of at least one frame?",
    "airborne": "Is the camera wearer or another person clearly in the air (jumping)?",
    "fall_or_crash": "Does someone fall, crash or wipe out?",
    "splash_or_spray": "Is there a splash, spray of snow or a wave breaking close to the camera?",
    "wide_scenic_view": "Is there a wide, open view of landscape or seascape?",
    "dramatic_light": "Is there sunset, sunrise or other striking light?",
    "something_changes": "Does something notable happen or appear between the first and last frame?",
    "idle_or_waiting": "Do most frames show standing, waiting, a lift, a queue or walking with nothing happening?",
    "camera_steady": "Is the view steady enough to follow what happens?",
    "lens_obstructed": "Is the lens blocked, fogged, or covered by water drops or a hand?",
}
CONTROLS = {
    "control_text_overlay": "Is there on-screen text, a title or a graphic added in editing?",
    "control_office": "Is this inside an office with desks and computer monitors?",
}
ACTIVITIES = ("ski", "snowboard", "snorkeling", "swim", "kayak", "surf", "boat", "bike",
              "hike", "beach", "travel", "other", "unclear")
PEOPLE = ("0", "1", "2-3", "4+", "unclear")
QUESTIONS = {**FACTS, **CONTROLS}


def schema() -> dict:
    ids = sorted(QUESTIONS)
    return {
        "type": "object", "additionalProperties": False,
        "required": ["caption", "activity", "people", "animal", "answers"],
        "properties": {
            "caption": {"type": "string", "maxLength": 240},
            "activity": {"type": "string", "enum": list(ACTIVITIES)},
            "people": {"type": "string", "enum": list(PEOPLE)},
            "animal": {"type": "string", "maxLength": 40},
            "answers": {"type": "object", "additionalProperties": False, "required": ids,
                        "properties": {i: {"type": "string", "enum": list(ANSWERS)}
                                       for i in ids}},
        },
    }


def prompt(stamps_ms: list[int]) -> str:
    listed = "\n".join(f"- {k}: {q}" for k, q in sorted(QUESTIONS.items()))
    times = ", ".join(f"{t / 1000:.1f}s" for t in stamps_ms)
    return (
        "These are frames, in time order, from one continuous clip filmed with a "
        f"first-person action camera, taken at: {times}.\n"
        "Describe only what is visible. Do not guess what happened between frames and "
        "do not judge whether the clip is good or worth keeping.\n"
        "- caption: one plain sentence saying what the frames show.\n"
        "- activity: what the camera wearer is doing; unclear if you cannot tell.\n"
        "- people: how many people other than the camera wearer are visible at most.\n"
        "- animal: the animal you see (for example 'sea turtle', 'reef fish'), or 'none'.\n"
        "- answers: yes, no or unclear for each question. Use unclear whenever the frames "
        "do not show enough to decide.\n\n" + listed
    )


def fingerprint(model: str) -> str:
    body = json.dumps({"version": VERSION, "model": model, "schema": schema(),
                       "prompt": prompt([])}, sort_keys=True)
    return sha256(body.encode()).hexdigest()[:20]


def validate(raw: object) -> dict:
    if (not isinstance(raw, dict)
            or set(raw) != {"caption", "activity", "people", "animal", "answers"}
            or not isinstance(raw["caption"], str) or len(raw["caption"]) > 240
            or raw["activity"] not in ACTIVITIES or raw["people"] not in PEOPLE
            or not isinstance(raw["animal"], str) or len(raw["animal"]) > 40
            or not isinstance(raw["answers"], dict) or set(raw["answers"]) != set(QUESTIONS)
            or any(v not in ANSWERS for v in raw["answers"].values())):
        raise ValueError("Scene card does not match the schema")
    return raw


def ask(model: str, images: list[str], stamps_ms: list[int], *, transport=None,
        timeout: int = 300) -> dict:
    """One model, one segment. Any failure is an abstention, never a 'no'."""
    from .adaptive_judge_v2 import _send_local
    if len(images) != len(stamps_ms) or not images:
        raise ValueError("Each frame needs a timestamp")
    base = {"model": model, "family": panel.family(model), "fingerprint": fingerprint(model),
            "frames": len(images)}
    body = {"model": model, "stream": False, "format": schema(), "options": {"temperature": 0},
            "messages": [{"role": "user", "content": prompt(stamps_ms), "images": images}]}
    try:
        reply = _send_local(body, timeout=timeout, transport=transport)
        card = validate(json.loads(reply["message"]["content"]))
    except (RuntimeError, ValueError, KeyError, TypeError) as error:
        return {**base, "status": "abstained", "failure": type(error).__name__,
                "notANegative": True}
    animal = card["animal"].strip().lower()
    return {**base, "status": "answered", "caption": card["caption"],
            "activity": card["activity"], "people": card["people"],
            "animal": None if animal in ("", "none", "no", "n/a") else animal,
            "facts": {k: v for k, v in card["answers"].items() if k in FACTS},
            "controlFailures": sorted(k for k in CONTROLS if card["answers"][k] == "yes"),
            "ownerVerified": False}


_VALUE = {"yes": 1.0, "no": 0.0, "unclear": None}
_PEOPLE = {"0": 0.0, "1": 1 / 3, "2-3": 2 / 3, "4+": 1.0, "unclear": None}


def features(cards: list[dict]) -> dict[str, float]:
    """Numeric facts for ranking: mean over models that answered and passed controls.

    'unclear' is missing, not 0.5; a fact nobody could see is left out.
    """
    usable = [c for c in cards if c.get("status") == "answered" and not c["controlFailures"]]
    out: dict[str, float] = {}
    for name in FACTS:
        values = [_VALUE[c["facts"][name]] for c in usable if _VALUE[c["facts"][name]] is not None]
        if values:
            out[f"card:{name}"] = sum(values) / len(values)
    people = [_PEOPLE[c["people"]] for c in usable if _PEOPLE[c["people"]] is not None]
    if people:
        out["card:people"] = sum(people) / len(people)
    return out


def _path(folder: Path, sha: str, start: int, end: int) -> Path:
    return Path(folder) / sha / f"{start}-{end}.json"


def load(folder: Path) -> dict[tuple[str, int, int], dict]:
    """Every stored card file keyed by (sha, start, end)."""
    out = {}
    for path in sorted(Path(folder).glob("*/*.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if doc.get("version") == VERSION:
            out[(doc["sourceSha256"], doc["startMs"], doc["endMs"])] = doc
    return out


def targets(judgments: list[dict], pairs: list[dict], scanned: dict[str, dict], group_of, *,
            controls_per_day: int = 4, control_ms: int | None = None, seed: int = 0) -> list[dict]:
    """Segments to describe: everything the owner judged, plus random controls.

    The controls are random windows from scanned recordings of the same days, so the
    cards' facts can be compared between what the owner looked at and the rest.
    scanned: sha -> {"durationMs"}.
    """
    seen, out = set(), []

    def add(sha, start, end, role):
        key = (sha, int(start), int(end))
        if key not in seen and end > start:
            seen.add(key)
            out.append({"sourceSha256": sha, "startMs": int(start), "endMs": int(end), "role": role})

    for j in judgments:
        add(j["sourceSha256"], j["startMs"], j["endMs"], "owner_labelled")
    for p in pairs:
        for side in ("left", "right"):
            s = p[side]
            add(s["sourceSha256"], s["startMs"], s["endMs"], "owner_labelled")
    lengths = sorted(t["endMs"] - t["startMs"] for t in out) or [8000]
    window = control_ms or lengths[len(lengths) // 2]
    days: dict[str, list[str]] = {}
    for sha in sorted(scanned):
        days.setdefault(group_of(sha), []).append(sha)
    labelled_days = {group_of(t["sourceSha256"]) for t in out}
    for day in sorted(days):
        if day not in labelled_days:
            continue
        rng = random.Random(f"{seed}:{day}")
        candidates = [s for s in days[day] if scanned[s]["durationMs"] > window]
        for _ in range(controls_per_day if candidates else 0):
            sha = rng.choice(candidates)
            start = rng.randrange(0, scanned[sha]["durationMs"] - window)
            add(sha, start, start + window, "random_control")
    return out


def describe(segments: list[dict], path_of, folder: Path, models: list[str], *,
             frames=None, transport=None, max_frames: int = 6, log=lambda m: None) -> dict:
    """Ask each model for a card per segment; resumable and failure-isolated."""
    if frames is None:
        from .panel_frames import sample_frames as frames
    totals = {"segments": len(segments), "asked": 0, "answered": 0, "abstained": 0,
              "alreadyDone": 0, "frameFailures": 0}
    for k, seg in enumerate(segments, 1):
        sha, start, end = seg["sourceSha256"], seg["startMs"], seg["endMs"]
        out = _path(folder, sha, start, end)
        previous = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {"cards": []}
        done = {c["model"]: c for c in previous["cards"]
                if c["status"] == "answered" and c["fingerprint"] == fingerprint(c["model"])}
        todo = [m for m in models if m not in done]
        if not todo:
            totals["alreadyDone"] += 1
            continue
        path = path_of(sha)
        if path is None:
            continue
        times = panel.frame_plan(start, end, seg.get("eventsMs"), max_frames=max(4, max_frames))
        try:
            images = frames(Path(path), times)
        except (ValueError, OSError) as error:
            totals["frameFailures"] += 1
            log(f"{sha[:12]} {start}-{end}: frames failed ({error})")
            continue
        cards = [done.get(m) or ask(m, images, times, transport=transport) for m in models]
        for card in cards:
            if card["model"] in todo:
                totals["asked"] += 1
                totals["answered" if card["status"] == "answered" else "abstained"] += 1
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".json.partial")
        tmp.write_text(json.dumps({"version": VERSION, "sourceSha256": sha, "startMs": start,
                                   "endMs": end, "role": seg["role"], "frameTimesMs": times,
                                   "cards": cards}, indent=2), encoding="utf-8")
        os.replace(tmp, out)
        shown = next((c["caption"] for c in cards if c["status"] == "answered"), "no answer")
        log(f"{k}/{len(segments)} {Path(path).name} {start / 1000:.0f}-{end / 1000:.0f}s "
            f"[{seg['role']}]: {shown}")
    return totals

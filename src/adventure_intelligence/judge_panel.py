"""A panel of local vision models that reports checkable facts about a segment.

Judges are never asked whether a clip is a highlight. They answer narrow
questions about what is visible (is the landing in frame, is an animal large
in frame), each of which the owner can verify in seconds. Whether those facts
make a clip worth keeping is decided elsewhere, from owner feedback.

Safeguards against a judge that agrees with everything:
- control questions from another sport, whose answer should be "no";
- at least two model families, so errors are less correlated;
- a failed or malformed reply is an abstention, never a "no";
- disagreement goes to the owner instead of a majority vote.

Everything here is a model observation. None of it is owner ground truth.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re

VERSION = "klip-fact-judge-panel-v1"
ANSWERS = ("yes", "no", "unclear")

SHARED = {
    "camera_steady": "Is the camera view steady enough to follow what happens?",
    "lens_obstructed": "Is the lens blocked, fogged or covered by water drops or a hand?",
    "wide_scenic_view": "Is there a wide, open view of landscape or seascape?",
    "scene_changes": "Does the setting clearly change between the first and last frame?",
}
RUBRICS = {
    "ski": {
        "airborne_visible": "Is the skier or the camera clearly off the snow in any frame?",
        "takeoff_visible": "Is a jump lip, rail or box visible just before leaving the snow?",
        "landing_visible": "Is there a frame showing the return to snow after being airborne?",
        "riding_after_landing": "After the landing, do the frames show continued riding and no fall?",
        "park_feature_in_frame": "Is a built feature (jump, rail or box) visible in any frame?",
        "stopped_or_standing": "Do most frames show standing still, a lift or a queue?",
    },
    "snorkeling": {
        "underwater": "Are most frames taken underwater?",
        "animal_clearly_visible": "Is a distinct animal clearly visible in any frame?",
        "animal_large_in_frame": "Does an animal fill a substantial part of any frame?",
        "animal_in_most_frames": "Is the same animal visible in most of the frames?",
        "reef_structure_visible": "Is coral or rock reef structure clearly visible?",
        "water_clear": "Is the water clear enough to see detail several metres away?",
    },
    "bike": {
        "trail_visible": "Is a trail or track ahead clearly visible?",
        "airborne_visible": "Are the wheels or the camera clearly off the ground in any frame?",
        "technical_feature": "Is a drop, rock section, berm or built feature visible?",
        "stopped_or_standing": "Do most frames show the bike stopped or being walked?",
    },
}
# Asked alongside the real rubric. A "yes" marks the judge as unreliable here.
CONTROLS = {
    "ski": {"control_fish": RUBRICS["snorkeling"]["animal_clearly_visible"].replace(
        "a distinct animal", "a fish or other sea animal")},
    "bike": {"control_fish": "Is a fish or other sea animal clearly visible in any frame?"},
    "snorkeling": {"control_snow": "Is snow or a ski slope visible in any frame?"},
}


NUM_CTX = 8192          # first try; enough for gemma3 (256 tokens/image)
MAX_CTX = 32768         # bound on the KV cache a judge may ask for on a 64 GB machine
_CTX_BY_MODEL: dict[str, int] = {}  # what each model turned out to need, this process


def _context_needed(error: Exception) -> int | None:
    """Context to retry with, if Ollama refused the request as longer than its window."""
    m = re.search(r"n_prompt_tokens[^0-9]*(\d+)", str(error))
    if "exceed" not in str(error) or not m:
        return None
    need = int(m.group(1)) + 2048           # room for the JSON answer
    ctx = -(-need // 4096) * 4096
    return ctx if ctx <= MAX_CTX else None


def usage(reply: dict) -> dict:
    """Where the time went, from Ollama's own counters (prefill vs generation)."""
    ns = 1e9
    msg = reply.get("message") or {}
    content, thinking = msg.get("content") or "", msg.get("thinking") or ""
    out = {"promptTokens": reply.get("prompt_eval_count"),
           "outputTokens": reply.get("eval_count"),
           # where output tokens went: the answer, hidden reasoning, or JSON whitespace
           "answerChars": len(content), "whitespaceChars": sum(c.isspace() for c in content),
           "thinkingChars": len(thinking)}
    for key, name in (("load_duration", "loadSeconds"), ("prompt_eval_duration", "prefillSeconds"),
                      ("eval_duration", "generateSeconds"), ("total_duration", "totalSeconds")):
        if isinstance(reply.get(key), (int, float)):
            out[name] = round(reply[key] / ns, 2)
    return out


def questions(sport: str) -> dict[str, str]:
    if sport not in RUBRICS:
        raise ValueError("Unknown sport rubric")
    return {**RUBRICS[sport], **SHARED, **CONTROLS[sport]}


def family(model: str) -> str:
    """'qwen3-vl:32b' -> 'qwen', 'gemma3:27b' -> 'gemma'. Same family, similar blind spots."""
    match = re.match(r"(?:[\w.-]+/)?([a-z]+)", model.lower())
    if not match:
        raise ValueError("Unrecognized model name")
    return match.group(1)


def frame_plan(start_ms: int, end_ms: int, events_ms: list[int] | None = None,
               *, max_frames: int = 16) -> list[int]:
    """Frame times: an even spread, plus a burst around each event candidate.

    Seven evenly spaced stills cannot show a landing. Bursts put frames just
    before, at and after each candidate so take-off and outcome are both seen.
    """
    if not (type(start_ms) is int and type(end_ms) is int and 0 <= start_ms < end_ms):
        raise ValueError("Invalid segment interval")
    if type(max_frames) is not int or not 4 <= max_frames <= 32:
        raise ValueError("max_frames must be 4..32")
    events = sorted(t for t in (events_ms or []) if start_ms <= t < end_ms)
    burst = [t + d for t in events for d in (-1000, -400, 0, 400, 1000, 2000)]
    burst = sorted({t for t in burst if start_ms <= t < end_ms})
    spread_n = max(4, max_frames - len(burst))
    step = (end_ms - start_ms) / spread_n
    spread = [int(start_ms + step * (i + 0.5)) for i in range(spread_n)]
    times = sorted(set(spread) | set(burst))
    if len(times) > max_frames:  # thin the bursts evenly, keep the spread
        keep = max_frames - len(spread)
        extra = [t for t in burst if t not in set(spread)]
        stride = len(extra) / keep if keep > 0 else 0
        times = sorted(set(spread) | {extra[int(i * stride)] for i in range(max(0, keep))})
    return times[:max_frames]


def schema(sport: str) -> dict:
    ids = sorted(questions(sport))
    return {
        "type": "object", "additionalProperties": False,
        "required": ["answers", "description"],
        "properties": {
            "description": {"type": "string", "maxLength": 400},
            "answers": {
                "type": "object", "additionalProperties": False, "required": ids,
                "properties": {i: {"type": "string", "enum": list(ANSWERS)} for i in ids},
            },
        },
    }


def prompt(sport: str, stamps_ms: list[int]) -> str:
    listed = "\n".join(f"- {key}: {text}" for key, text in sorted(questions(sport).items()))
    times = ", ".join(f"{t / 1000:.1f}s" for t in stamps_ms)
    return (
        "You are shown frames from one continuous first-person action camera clip, "
        f"in time order, taken at: {times}.\n"
        "Report only what is visible in these frames. Do not guess what happened "
        "between frames and do not judge whether the clip is good.\n"
        "Answer every question with yes, no or unclear. Use unclear whenever the "
        "frames do not show enough to decide. Several questions will not apply to "
        "this clip; answer those no.\n"
        "Also give one plain sentence describing what the frames show.\n\n" + listed
    )


def fingerprint(model: str, sport: str) -> str:
    body = json.dumps({"version": VERSION, "model": model, "schema": schema(sport),
                       "prompt": prompt(sport, [])}, sort_keys=True)
    return sha256(body.encode()).hexdigest()[:20]


def validate(raw: object, sport: str) -> dict:
    expected = set(questions(sport))
    if (not isinstance(raw, dict) or set(raw) != {"answers", "description"}
            or not isinstance(raw["description"], str) or len(raw["description"]) > 400
            or not isinstance(raw["answers"], dict) or set(raw["answers"]) != expected
            or any(v not in ANSWERS for v in raw["answers"].values())):
        raise ValueError("Judge reply does not match the rubric schema")
    return raw


def with_think(body: dict, think: bool | None) -> dict:
    """Ollama's reasoning switch. None leaves the model's default (the first judge pass
    ran that way, so its answers stay comparable); False asks for the answer only."""
    return body if think is None else {**body, "think": bool(think)}


def think_unsupported(error: Exception) -> bool:
    text = str(error).lower()
    return "think" in text and ("support" in text or "invalid" in text)


def ask(model: str, sport: str, images: list[str], stamps_ms: list[int], *,
        transport=None, timeout: int = 300, think: bool | None = None) -> dict:
    """One judge, one segment. Any failure is recorded as an abstention."""
    from .adaptive_judge_v2 import _send_local
    if len(images) != len(stamps_ms) or not images:
        raise ValueError("Each frame needs a timestamp")
    # Explicit context, sized per model: Ollama refuses a request longer than its window
    # (qwen3-vl spends ~1,050 tokens per frame, gemma3 256), so a refusal is retried once
    # with a window that fits, and the window used is recorded with the judgment.
    ctx = _CTX_BY_MODEL.get(model, NUM_CTX)
    for attempt in (1, 2, 3):
        options = {"temperature": 0, "num_ctx": ctx}
        base = {"model": model, "family": family(model), "sport": sport,
                "fingerprint": fingerprint(model, sport), "frames": len(images),
                "options": options, "think": think}
        body = with_think({"model": model, "stream": False, "format": schema(sport),
                           "options": options, "keep_alive": "30m",  # stay loaded
                           "messages": [{"role": "user", "content": prompt(sport, stamps_ms),
                                         "images": images}]}, think)
        try:
            reply = _send_local(body, timeout=timeout, transport=transport)
            parsed = validate(json.loads(reply["message"]["content"]), sport)
            _CTX_BY_MODEL[model] = ctx
            break
        except (RuntimeError, ValueError, KeyError, TypeError) as error:
            if think is not None and think_unsupported(error):
                think = None          # a model without the switch: ask it plainly
                continue
            bigger = _context_needed(error) if attempt < 3 else None
            if bigger and bigger > ctx:
                ctx = bigger
                continue
            return {**base, "status": "abstained", "failure": type(error).__name__,
                    "failureDetail": str(error)[:300], "notANegative": True}
    controls = [k for k in CONTROLS[sport] if parsed["answers"][k] == "yes"]
    return {**base, "status": "answered", "usage": usage(reply),
            "answers": {k: v for k, v in parsed["answers"].items() if k not in CONTROLS[sport]},
            "modelDescription": parsed["description"],
            "controlFailures": controls, "ownerVerified": False}


def combine(judgments: list[dict]) -> dict:
    """Per-question outcome across judges. Disagreement is surfaced, not voted away."""
    if len({j["sport"] for j in judgments}) != 1:
        raise ValueError("Panel judgments must share one sport rubric")
    sport = judgments[0]["sport"]
    usable = [j for j in judgments if j["status"] == "answered" and not j["controlFailures"]]
    families = {j["family"] for j in usable}
    facts = {}
    for key in sorted({**RUBRICS[sport], **SHARED}):
        votes = {j["model"]: j["answers"][key] for j in usable}
        distinct = set(votes.values())
        if len(families) < 2:
            outcome = "insufficient_independent_judges"
        elif len(distinct) == 1 and distinct != {"unclear"}:
            outcome = "agreed_" + next(iter(distinct))
        elif distinct <= {"unclear"}:
            outcome = "unclear"
        else:
            outcome = "disputed"
        facts[key] = {"outcome": outcome, "votes": votes}
    return {
        "version": VERSION, "sport": sport, "facts": facts,
        "judges": len(judgments), "usableJudges": len(usable),
        "independentFamilies": sorted(families),
        "abstained": [j["model"] for j in judgments if j["status"] == "abstained"],
        "failedControls": [j["model"] for j in judgments
                           if j["status"] == "answered" and j["controlFailures"]],
        "needsOwner": sorted(k for k, v in facts.items() if v["outcome"] == "disputed"),
        "isModelObservationNotOwnerTruth": True,
    }


def calibrate(panels: list[dict], owner: list[dict]) -> dict:
    """Score each judge per question against the owner's own yes/no answers.

    `owner` rows are {"index": panel position, "answers": {question: yes|no}}.
    Reports false-yes and false-no separately: a judge that over-claims
    animals fails differently from one that misses them.
    """
    table: dict[tuple, dict] = {}
    for row in owner:
        facts = panels[row["index"]]["facts"]
        for key, truth in row["answers"].items():
            if truth not in ("yes", "no") or key not in facts:
                raise ValueError("Owner answers must be yes/no for rubric questions")
            for model, said in facts[key]["votes"].items():
                cell = table.setdefault((model, key), {
                    "n": 0, "correct": 0, "falseYes": 0, "falseNo": 0, "unclear": 0})
                cell["n"] += 1
                if said == "unclear":
                    cell["unclear"] += 1
                elif said == truth:
                    cell["correct"] += 1
                else:
                    cell["falseYes" if said == "yes" else "falseNo"] += 1
    return {"version": VERSION, "ownerItems": len(owner),
            "byModelQuestion": [
                {"model": m, "question": q, **c,
                 "accuracy": round(c["correct"] / c["n"], 3)}
                for (m, q), c in sorted(table.items())],
            "smallSampleCaution": len(owner) < 30}

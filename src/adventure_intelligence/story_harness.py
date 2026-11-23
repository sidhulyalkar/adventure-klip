"""Grounded outing stories: a harness that keeps LLM stories tied to the footage.

Pipeline, per outing (one day of one activity):

1. quality   (no model)  Per review segment: sharpness, exposure, flatness (covered lens)
                         from proxy frames, and shake from the motion timeline. Measured
                         relative to the same recording, because underwater footage is
                         softer than snow footage by nature. Verdict usable / degraded /
                         unusable, with reasons.
2. describe  (VLM)       Per segment: dated observations, subjects, setting, camera motion
                         and the model's view of filming quality. Every observation names
                         the second it was seen at; times outside the segment are refused.
3. story     (LLM)       Per outing: chronological beats, moments with a reason they matter
                         and the evidence for it, a best-first shortlist.
4. validate  (code)      Every claim is checked against the packet the model was given:
                         segments exist, beats are in clock order, evidence references
                         resolve, named animals/events appear in that segment's evidence,
                         time-of-day words match the capture clock, "first"/"last" only for
                         the first/last reviewed scene, no unusable scene in the shortlist,
                         a degraded scene only with its flaw acknowledged.
5. repair    (LLM, once) The violations are sent back once. What still fails is removed by
                         the harness and listed; nothing invalid is kept silently.

Model output here is a proposal, never an owner label (isModelProposalNotOwnerJudgment).
Thresholds are v1 and uncalibrated: they are recorded with every result and are to be
checked against the owner's keep/skip answers.
"""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

import numpy as np

from . import judge_panel as panel

VERSION = "klip-grounded-outing-stories-v1"

# ---------------------------------------------------------------- quality (no model)

QUALITY = {  # v1, uncalibrated; recorded with every result
    "frameSize": (320, 180),
    "darkMeanLuma": 30, "darkPixelShare": 0.6,      # a dark frame
    "flatLumaStd": 8.0,                              # covered lens / pocket / blank
    "clippedPixelShare": 0.5,                        # blown out
    "softVsRecording": 0.35, "softAbsolute": 8.0,    # Laplacian variance
    "shakyVsRecording": 2.5,                         # motion jitter vs recording median
    "unusableFrameShare": 0.6, "degradedFrameShare": 0.6,
}


def frame_metrics(gray: np.ndarray) -> dict:
    """Sharpness, exposure and flatness of one 8-bit grayscale frame."""
    g = gray.astype(np.float32)
    lap = (-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
    return {"sharpness": round(float(lap.var()), 2), "meanLuma": round(float(g.mean()), 1),
            "lumaStd": round(float(g.std()), 1),
            "darkShare": round(float((g < 16).mean()), 3),
            "clippedShare": round(float((g > 245).mean()), 3)}


def decode_gray(proxy: Path, times_ms: list[int]) -> list[np.ndarray]:
    """Grayscale frames at proxy-relative times (no camera original is opened)."""
    from .media import require_tool
    ffmpeg = require_tool("ffmpeg")
    w, h = QUALITY["frameSize"]
    out = []
    for t in times_ms:
        job = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{t / 1000:.3f}",
             "-i", str(proxy), "-frames:v", "1", "-vf", f"scale={w}:{h},format=gray",
             "-f", "rawvideo", "-"], capture_output=True, timeout=30, check=False)
        if job.returncode != 0 or len(job.stdout) != w * h:
            raise ValueError(f"Could not decode a frame at {t} ms of {proxy.name}")
        out.append(np.frombuffer(job.stdout, np.uint8).reshape(h, w))
    return out


def jitter(series: list[dict], start: int, end: int) -> float | None:
    """Median absolute step of the motion-change signal: high when the camera shakes."""
    vals = [r["change"] for r in series if start <= r["t_ms"] < end]
    if len(vals) < 3:
        return None
    return float(np.median(np.abs(np.diff(vals))))


def segment_quality(frames: list[dict], recording_sharpness: float | None,
                    shake_ratio: float | None, facts: dict | None) -> dict:
    """Verdict for one segment from its frame metrics, shake and the judges' shared facts."""
    q = QUALITY
    flags, per_frame = [], []
    for m in frames:
        bad = []
        if m["meanLuma"] < q["darkMeanLuma"] and m["darkShare"] > q["darkPixelShare"]:
            bad.append("dark")
        if m["lumaStd"] < q["flatLumaStd"]:
            bad.append("blank_or_covered")
        if m["clippedShare"] > q["clippedPixelShare"]:
            bad.append("blown_out")
        soft_floor = max(q["softAbsolute"], q["softVsRecording"] * (recording_sharpness or 0))
        if m["sharpness"] < soft_floor:
            bad.append("soft")
        per_frame.append(bad)
    n = max(1, len(per_frame))
    share = {f: sum(f in b for b in per_frame) / n
             for f in ("dark", "blank_or_covered", "blown_out", "soft")}
    unusable = [f for f in ("dark", "blank_or_covered")
                if share[f] >= q["unusableFrameShare"]]
    degraded = [f for f in ("blown_out", "soft") if share[f] >= q["degradedFrameShare"]]
    if shake_ratio is not None and shake_ratio > q["shakyVsRecording"]:
        degraded.append("shaky")
    facts = facts or {}
    # Judges' view of the lens only degrades a scene: on owner footage the two judges
    # agreed "obstructed" on 15 snorkeling scenes while disputing the question 39% of the
    # time (likely underwater haze), too weak to drop a scene from every reel.
    if facts.get("lens_obstructed", {}).get("outcome") == "agreed_yes":
        degraded.append("lens_obstructed_by_judges")
    if facts.get("camera_steady", {}).get("outcome") == "agreed_no":
        degraded.append("unsteady_by_judges")
    flags = unusable + degraded
    verdict = "unusable" if unusable else "degraded" if degraded else "usable"
    return {"verdict": verdict, "flags": flags,
            "frameShare": {k: round(v, 2) for k, v in share.items()},
            "shakeVsRecording": None if shake_ratio is None else round(shake_ratio, 2),
            "frames": frames, "thresholds": QUALITY, "isMeasurementNotOwnerJudgment": True}


# ---------------------------------------------------------------- bounded JSON calls

STORY_OUTPUT_TOKENS = 3072
DESCRIBE_OUTPUT_TOKENS = 1024
TOKENS_PER_IMAGE = 1100     # measured on qwen3-vl (Ollama resizes every image to its grid)
CHARS_PER_TOKEN = 3.0       # conservative for English prompts and JSON
CTX_STEP, MAX_CTX = 4096, 32768


def estimate_prompt_tokens(messages: list[dict]) -> int:
    text = sum(len(m.get("content") or "") for m in messages)
    images = sum(len(m.get("images") or []) for m in messages)
    return int(text / CHARS_PER_TOKEN) + images * TOKENS_PER_IMAGE


def context_for(messages: list[dict], output_budget: int) -> int:
    """A window that holds the prompt and the whole answer: Ollama stops at the window,
    and a JSON answer cut there is unparseable (the first owner run lost 108 of 145
    descriptions and one story this way)."""
    need = estimate_prompt_tokens(messages) + output_budget + 256
    return min(MAX_CTX, -(-need // CTX_STEP) * CTX_STEP)


def _reply_tail(reply: dict | None) -> dict:
    """What came back when it could not be parsed: Ollama's stop reason and the ends."""
    if not isinstance(reply, dict):
        return {}
    msg = reply.get("message") or {}
    content = msg.get("content") or ""
    return {"doneReason": reply.get("done_reason"), "contentChars": len(content),
            "thinkingChars": len(msg.get("thinking") or ""),
            "contentHead": content[:200], "contentTail": content[-200:],
            "outputTokens": reply.get("eval_count"), "promptTokens": reply.get("prompt_eval_count")}


def call_json(model: str, messages: list[dict], *, schema: dict, check, transport=None,
              timeout: int = 600, repairs: int = 1, think: bool | None = False,
              output_budget: int = 1024, normalize=None) -> dict:
    """One schema-constrained call, checked by `check(parsed) -> [violations]`.

    The context is sized for the prompt plus `output_budget`, and the answer is capped at
    that budget (num_predict) so a runaway answer ends instead of filling the window. An
    answer cut by a length limit is retried once with twice the room. Violations are sent
    back `repairs` times. `normalize(parsed)` may fix harmless spelling first. The result
    says accepted, repaired, violating or failed, with Ollama's stop reason.
    """
    from .adaptive_judge_v2 import _send_local
    history, attempts, last, violations = list(messages), 0, None, []
    budget, grown, repaired = output_budget, False, False
    ctx = max(panel._CTX_BY_MODEL.get(model, 0), context_for(history, budget))
    while True:
        attempts += 1
        options = {"temperature": 0, "num_ctx": ctx, "num_predict": budget}
        body = panel.with_think({"model": model, "stream": False, "format": schema,
                                 "options": options, "keep_alive": "30m",
                                 "messages": history}, think)
        reply = None
        try:
            reply = _send_local(body, timeout=timeout, transport=transport)
            text = reply["message"]["content"]
            parsed = json.loads(text)
        except (RuntimeError, ValueError, KeyError, TypeError) as error:
            if think is not None and panel.think_unsupported(error) and attempts <= 3:
                think = None
                continue
            bigger = panel._context_needed(error)
            if bigger and bigger > ctx and attempts <= 3:
                ctx = bigger
                continue
            tail = _reply_tail(reply)
            if tail.get("doneReason") == "length" and not grown and attempts <= 3:
                budget, grown = budget * 2, True          # cut off: once, with more room
                ctx = max(ctx, context_for(history, budget))
                continue
            return {"status": "failed", "model": model, "options": options, "think": think,
                    "attempts": attempts, "failure": type(error).__name__,
                    "failureDetail": str(error)[:300], "reply": tail, "result": last,
                    "violations": violations}
        panel._CTX_BY_MODEL[model] = max(panel._CTX_BY_MODEL.get(model, 0), ctx)
        if normalize:
            parsed = normalize(parsed)
        last, violations = parsed, check(parsed)
        usage = {**panel.usage(reply), "doneReason": reply.get("done_reason")}
        if not violations:
            return {"status": "repaired" if repaired else "accepted", "model": model,
                    "options": options, "think": think, "usage": usage,
                    "attempts": attempts, "result": parsed, "violations": []}
        if repairs <= 0:
            return {"status": "violating", "model": model, "options": options,
                    "think": think, "usage": usage, "attempts": attempts,
                    "result": parsed, "violations": violations}
        repairs -= 1
        repaired = True
        history = history + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": "Your answer breaks these rules:\n- " +
             "\n- ".join(violations[:30]) + "\nReturn the full answer again with only "
             "these problems fixed. Do not add claims that are not in the evidence."}]
        ctx = max(ctx, context_for(history, budget))


# ---------------------------------------------------------------- describe (VLM)

CAMERA = ("steady", "moving_smoothly", "shaky", "unclear")


def describe_schema(n_frames: int = 8) -> dict:
    """Observations name a frame NUMBER from a closed list, so a valid time is guaranteed by
    the output grammar; the harness converts it to seconds (with_times)."""
    yn = {"type": "string", "enum": ["yes", "no", "unclear"]}
    return {"type": "object", "additionalProperties": False,
            "required": ["observations", "summary", "subjects", "setting", "cameraMotion",
                         "filming"],
            "properties": {
                "observations": {"type": "array", "minItems": 1, "maxItems": 8, "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["frame", "text"],
                    "properties": {"frame": {"type": "integer",
                                             "enum": list(range(1, max(1, n_frames) + 1))},
                                   "text": {"type": "string", "maxLength": 200}}}},
                "summary": {"type": "string", "maxLength": 300},
                "subjects": {"type": "array", "maxItems": 8,
                             "items": {"type": "string", "maxLength": 40}},
                "setting": {"type": "string", "maxLength": 120},
                "cameraMotion": {"type": "string", "enum": list(CAMERA)},
                "filming": {"type": "object", "additionalProperties": False,
                            "required": ["inFocus", "exposureOk", "lensClear"],
                            "properties": {"inFocus": yn, "exposureOk": yn, "lensClear": yn}},
            }}


def describe_prompt(sport: str, stamps_ms: list[int], duration_ms: int) -> str:
    frames = ", ".join(f"frame {i} at {t / 1000:.1f}s" for i, t in enumerate(stamps_ms, 1))
    return (
        f"These are {len(stamps_ms)} frames, in time order, from one "
        f"{duration_ms / 1000:.0f}-second clip of a first-person action camera during "
        f"{sport}: {frames}.\n"
        "Describe only what is visible. For each observation give the number of the frame "
        f"(1 to {len(stamps_ms)}) in which it is visible. Do not guess what happens between "
        "frames, do not invent animals, people or tricks, and do not say whether the clip is "
        "good. Keep each observation to one short sentence. List the subjects you can see, "
        "the setting, how the camera moves, and whether the frames are in focus, well "
        "exposed and taken through a clear lens.")


def check_description(d: dict, duration_ms: int, stamps_ms: list[int]) -> list[str]:
    out = []
    n = len(stamps_ms)
    allowed = {round(t / 1000, 1) for t in stamps_ms}
    for i, o in enumerate(d.get("observations", [])):
        if "frame" in o:
            if not isinstance(o["frame"], int) or not 1 <= o["frame"] <= n:
                out.append(f"observation {i} names frame {o['frame']}; frames are 1 to {n}")
        else:   # descriptions written before frame numbers
            at = o.get("atSeconds")
            if not isinstance(at, (int, float)) or not 0 <= at <= duration_ms / 1000 + 0.5:
                out.append(f"observation {i} is at {at}s, outside the clip (0-"
                           f"{duration_ms / 1000:.1f}s)")
            elif min(abs(at - a) for a in allowed) > 1.0:
                out.append(f"observation {i} is at {at}s, which is not one of the frame times")
        if not o.get("text", "").strip():
            out.append(f"observation {i} is empty")
    if not d.get("summary", "").strip():
        out.append("summary is empty")
    return out


def with_times(d: dict, stamps_ms: list[int]) -> dict:
    """A description whose observations also carry atSeconds, from their frame numbers."""
    obs = []
    for o in d.get("observations", []):
        if "frame" in o and isinstance(o["frame"], int) and 1 <= o["frame"] <= len(stamps_ms):
            o = {**o, "atSeconds": round(stamps_ms[o["frame"] - 1] / 1000, 3)}
        obs.append(o)
    return {**d, "observations": obs}


# ---------------------------------------------------------------- packet

_DJI = re.compile(r"DJI_(\d{14})")


def capture_start(name: str) -> datetime | None:
    m = _DJI.search(name or "")
    try:
        return datetime.strptime(m.group(1), "%Y%m%d%H%M%S") if m else None
    except ValueError:
        return None


def part_of_day(hour: int) -> str:
    return "morning" if 5 <= hour < 12 else "afternoon" if hour < 17 else (
        "evening" if hour < 21 else "night")


def build_packet(outing: str, sport: str, scenes: list[dict], *, footage_ms: int,
                 examples: list[dict] | None = None) -> dict:
    """What the storyteller may know about one outing. Scenes get ids S01.. in clock order.

    Each scene dict: file, sourceSha256, startMs, endMs, capture (datetime | None),
    quality, facts (panel facts or None), description (or None), signals.
    """
    def clock(s):
        return (s["capture"] is None, s["capture"] or datetime.min, s["file"], s["startMs"])

    ordered = sorted(scenes, key=clock)
    out = []
    for i, s in enumerate(ordered, 1):
        facts = s.get("facts") or {}
        start = (s["capture"].timestamp() + s["startMs"] / 1000) if s["capture"] else None
        d = s.get("description") or {}
        out.append({
            "id": f"S{i:02d}", "file": s["file"], "sourceSha256": s["sourceSha256"],
            "startMs": s["startMs"], "endMs": s["endMs"],
            "durationSeconds": round((s["endMs"] - s["startMs"]) / 1000, 1),
            "clockStart": datetime.fromtimestamp(start).strftime("%H:%M:%S") if start else None,
            "partOfDay": part_of_day(datetime.fromtimestamp(start).hour) if start else None,
            "quality": {"verdict": s["quality"]["verdict"], "flags": s["quality"]["flags"]},
            "factsYes": sorted(k for k, v in facts.items()
                               if v["outcome"] in ("agreed_yes", "resolved_yes")),
            "factsNo": sorted(k for k, v in facts.items()
                              if v["outcome"] in ("agreed_no", "resolved_no")),
            "factsDisputed": sorted(k for k, v in facts.items() if v["outcome"] == "disputed"),
            # Settled by owner-calibrated judge reliability, not by agreement: kept visible.
            "factsResolved": sorted(k for k, v in facts.items()
                                    if v["outcome"] in ("resolved_yes", "resolved_no")),
            "summary": d.get("summary"), "subjects": d.get("subjects", []),
            "setting": d.get("setting"), "cameraMotion": d.get("cameraMotion"),
            "observations": [f'{o["atSeconds"]:.1f}s: {o["text"]}'
                             for o in d.get("observations", [])],
            "signals": s.get("signals", {}),
        })
    reviewed = sum(s["endMs"] - s["startMs"] for s in ordered)
    return {"version": VERSION, "outing": outing, "sport": sport, "scenes": out,
            "reviewedSeconds": round(reviewed / 1000), "footageSeconds": round(footage_ms / 1000),
            "examples": examples or []}


def packet_digest(packet: dict) -> str:
    return sha256(json.dumps(packet, sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------- story (LLM)

def evidence_refs(packet: dict) -> list[str]:
    """Every citation a story may use, across the outing's scenes."""
    refs = set()
    for s in packet["scenes"]:
        refs |= {f"fact:{k}" for k in s["factsYes"]}
        refs |= {f"obs:{i}" for i in range(1, len(s["observations"]) + 1)}
        refs |= {f"subject:{x}" for x in s["subjects"]}
        refs |= {f"signal:{k}" for k in s["signals"]}
        refs |= {f"quality:{f}" for f in s["quality"]["flags"]}
    return sorted(refs)


_FACT_FORMS = (re.compile(r"^(?:yes|fact|facts)\s*:\s*([a-z_]+)$"),
               re.compile(r"^([a-z_]+)\s*:\s*yes$"))
_OBS_FORM = re.compile(r"^(?:obs|observation)\s*[:#\s]\s*<?\s*(\d+)\s*>?$")


def normalize_ref(ref: str, scene: dict | None) -> str | None:
    """A citation in its canonical form, or None when it cites nothing (a bare scene id)."""
    r = (ref or "").strip()
    low = r.lower()
    if re.fullmatch(r"s\d+", low):
        return None
    for form in _FACT_FORMS:
        m = form.match(low)
        if m:
            return f"fact:{m.group(1)}"
    m = _OBS_FORM.match(low)
    if m:
        return f"obs:{int(m.group(1))}"
    if scene and low in scene.get("factsYes", []):
        return f"fact:{low}"
    return r


def normalize_story(story: dict, packet: dict) -> dict:
    scenes = {s["id"]: s for s in packet["scenes"]}
    out = json.loads(json.dumps(story))
    for m in out.get("moments", []):
        refs = []
        for ref in m.get("evidence", []):
            n = normalize_ref(ref, scenes.get(m.get("scene")))
            if n and n not in refs:
                refs.append(n)
        m["evidence"] = refs
    return out


def story_schema(scene_ids: list[str], refs: list[str] | None = None) -> dict:
    sid = {"type": "string", "enum": scene_ids}
    ref_item = ({"type": "string", "enum": refs} if refs
                else {"type": "string", "maxLength": 60})
    return {"type": "object", "additionalProperties": False,
            "required": ["beats", "moments", "shortlist", "excluded"],
            "properties": {
                "beats": {"type": "array", "minItems": 1, "maxItems": 8, "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["text", "scenes"],
                    "properties": {"text": {"type": "string", "maxLength": 400},
                                   "scenes": {"type": "array", "minItems": 1, "items": sid}}}},
                "moments": {"type": "array", "maxItems": 30, "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["scene", "what", "whyItMatters", "evidence", "confidence"],
                    "properties": {
                        "scene": sid,
                        "what": {"type": "string", "maxLength": 240},
                        "whyItMatters": {"type": "string", "maxLength": 300},
                        "evidence": {"type": "array", "minItems": 1, "maxItems": 6,
                                     "items": ref_item},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]}}}},
                "shortlist": {"type": "array", "items": sid},
                "excluded": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["scene", "reason"],
                    "properties": {"scene": sid,
                                   "reason": {"type": "string", "maxLength": 200}}}},
            }}


def story_prompt(packet: dict, shortlist_max: int) -> str:
    lines = [
        f"Outing: {packet['outing']} ({packet['sport']}). You see {len(packet['scenes'])} "
        f"reviewed scenes covering {packet['reviewedSeconds']} s of "
        f"{packet['footageSeconds']} s of footage. Footage you were not shown may hold "
        "other events: never describe it.",
        "",
        "Write the outing as a short story in chronological beats, then list the moments "
        "worth keeping and why each matters to the person who filmed it, then a shortlist "
        f"of at most {shortlist_max} scenes, best first, and the scenes to exclude with "
        "the reason.",
        "Rules:",
        "- Use only the evidence below. Name an animal, person, trick or event only if it "
        "appears in that scene's facts, subjects, summary or observations.",
        "- Every moment cites evidence as fact:<key> (a 'yes' fact of that scene), "
        "obs:<n> (that scene's observation number, from 1), subject:<name>, "
        "signal:<name> or quality:<flag>.",
        "- Beats follow clock order. Say 'first' or 'last' only of the first or last scene "
        "listed; use morning/afternoon/evening only as the scene's part of day says.",
        "- Scenes marked unusable never go in the shortlist. A degraded scene may only be "
        "shortlisted if its moment cites quality:<flag> and says why it is still worth it.",
        "- Disputed facts are uncertain: do not state them as true.",
        "",
    ]
    if packet["examples"]:
        lines.append("How the person judged similar moments before (from other outings):")
        for e in packet["examples"][:8]:
            lines.append(f"- {e['decision']}: {e['text']}" +
                         (f" (reason: {e['reason']})" if e.get("reason") else ""))
        lines.append("")
    for s in packet["scenes"]:
        lines.append(
            f"{s['id']} | {s['file']} {s['startMs'] / 1000:.0f}-{s['endMs'] / 1000:.0f}s "
            f"({s['durationSeconds']} s) | clock {s['clockStart'] or 'unknown'} "
            f"({s['partOfDay'] or 'unknown part of day'}) | quality {s['quality']['verdict']}"
            + (f" ({', '.join(s['quality']['flags'])})" if s["quality"]["flags"] else ""))
        lines.append(f"  yes: {', '.join(s['factsYes']) or '-'} | no: "
                     f"{', '.join(s['factsNo']) or '-'} | disputed: "
                     f"{', '.join(s['factsDisputed']) or '-'}"
                     + (f" | settled by measured judge reliability: {', '.join(s['factsResolved'])}"
                        if s.get("factsResolved") else ""))
        if s["summary"]:
            lines.append(f"  summary: {s['summary']}")
        if s["subjects"]:
            lines.append(f"  subjects: {', '.join(s['subjects'])}")
        for n, o in enumerate(s["observations"], 1):
            lines.append(f"  obs {n}: {o}")
        if s["signals"]:
            lines.append("  signals: " + ", ".join(f"{k}={v}" for k, v in s["signals"].items()))
    return "\n".join(lines)


# Words that name something checkable. A story that uses one must have it in the evidence.
SALIENT = {
    "turtle": ["turtle"], "manta": ["manta"], "ray": ["ray", "stingray"],
    "shark": ["shark"], "dolphin": ["dolphin"], "whale": ["whale"], "octopus": ["octopus"],
    "eel": ["eel"], "fish": ["fish"], "coral": ["coral", "reef"],
    "jump": ["jump", "airborne", "takeoff", "landing"],
    "crash": ["crash", "fall", "fell", "wipeout"], "rail": ["rail", "box", "park", "feature"],
    "powder": ["powder"], "lift": ["lift", "chairlift", "gondola", "queue"],
    "person": ["friend", "person", "people", "skier", "diver", "swimmer", "rider", "group",
               "child", "face"],
    "sunset": ["sunset", "sunrise"], "waterfall": ["waterfall"], "kayak": ["kayak"],
    "boat": ["boat"], "beach": ["beach", "sand"],
}
_CLOCK = ("morning", "afternoon", "evening", "night")
_FIRST, _LAST = {"first", "opening"}, {"last", "final"}


def _words(text: str) -> set[str]:
    return {w[:-1] if len(w) > 3 and w.endswith("s") else w
            for w in re.findall(r"[a-z]+", (text or "").lower())}


_TERM = {w: k for k, ws in SALIENT.items() for w in _words(" ".join(ws))}
_CONCEPT_WORDS = {k: _words(" ".join(ws)) for k, ws in SALIENT.items()}


def scene_words(scene: dict, sport: str) -> set[str]:
    """Everything a scene's evidence says, as words: yes facts (and their questions),
    summary, subjects, setting and observations. No facts answered 'no' or disputed."""
    asked = {**panel.RUBRICS.get(sport, {}), **panel.SHARED}
    text = " ".join([*scene["factsYes"], *(asked.get(k, "") for k in scene["factsYes"]),
                     scene.get("summary") or "", " ".join(scene.get("subjects", [])),
                     scene.get("setting") or "", " ".join(scene.get("observations", []))])
    return _words(text.replace("_", " "))


def check_story(story: dict, packet: dict, shortlist_max: int,
                warnings: list[str] | None = None) -> list[str]:
    """Every violation, as a sentence the model can act on. Empty means grounded.
    'first'/'last' about scenes that are not first/last is only a warning (collected in
    `warnings`): it removed many plausible claims in the first owner run."""
    scenes = {s["id"]: s for s in packet["scenes"]}
    order = [s["id"] for s in packet["scenes"]]
    seen = {k: scene_words(v, packet["sport"]) for k, v in scenes.items()}
    out = []

    def grounded(text, ids, where):
        for concept in sorted({_TERM[w] for w in _words(text) if w in _TERM}):
            if not any(_CONCEPT_WORDS[concept] & seen[i] for i in ids if i in seen):
                out.append(f"{where} mentions {concept} but no cited scene shows it")

    def clock_words(text, ids, where):
        words = _words(text)
        parts = {scenes[i]["partOfDay"] for i in ids if i in scenes}
        for part in _CLOCK:
            if part in words and None not in parts and part not in parts:
                out.append(f"{where} says '{part}' but its scenes are in the "
                           f"{', '.join(sorted(parts))}")
        soft = warnings if warnings is not None else []
        if words & _FIRST and order and order[0] not in ids:
            soft.append(f"{where} says 'first' but does not include the first scene {order[0]}")
        if words & _LAST and order and order[-1] not in ids:
            soft.append(f"{where} says 'last' but does not include the last scene {order[-1]}")

    last_pos = -1
    for b, beat in enumerate(story.get("beats", []), 1):
        ids = beat.get("scenes", [])
        bad = [i for i in ids if i not in scenes]
        if bad:
            out.append(f"beat {b} cites unknown scenes {bad}")
            continue
        pos = [order.index(i) for i in ids]
        if pos != sorted(pos) or min(pos) < last_pos:
            out.append(f"beat {b} is out of clock order")
        last_pos = max(last_pos, max(pos))
        grounded(beat.get("text", ""), ids, f"beat {b}")
        clock_words(beat.get("text", ""), ids, f"beat {b}")

    moments_by_scene: dict[str, dict] = {}
    for m_i, m in enumerate(story.get("moments", []), 1):
        sid, where = m.get("scene"), f"moment {m_i} ({m.get('scene')})"
        if sid not in scenes:
            out.append(f"{where} cites an unknown scene")
            continue
        s = scenes[sid]
        moments_by_scene.setdefault(sid, m)
        for ref in m.get("evidence", []):
            kind, _, value = ref.partition(":")
            ok = (kind == "fact" and value in s["factsYes"]
                  or kind == "obs" and value.isdigit()
                  and 1 <= int(value) <= len(s["observations"])
                  or kind == "subject" and value.lower() in [x.lower() for x in s["subjects"]]
                  or kind == "signal" and value in s["signals"]
                  or kind == "quality" and value in s["quality"]["flags"])
            if kind == "fact" and value in s["factsDisputed"]:
                out.append(f"{where} treats disputed fact {value} as true")
            elif not ok:
                out.append(f"{where} cites '{ref}', which is not in {sid}'s evidence")
        text = m.get("what", "") + " " + m.get("whyItMatters", "")
        grounded(text, [sid], where)
        clock_words(text, [sid], where)

    short = story.get("shortlist", [])
    if len(short) != len(set(short)):
        out.append("shortlist repeats a scene")
    if len(short) > shortlist_max:
        out.append(f"shortlist has {len(short)} scenes; at most {shortlist_max}")
    for sid in short:
        if sid not in scenes:
            out.append(f"shortlist cites unknown scene {sid}")
            continue
        verdict = scenes[sid]["quality"]["verdict"]
        if verdict == "unusable":
            out.append(f"shortlist includes {sid}, which is unusable "
                       f"({', '.join(scenes[sid]['quality']['flags'])})")
        elif sid not in moments_by_scene:
            out.append(f"shortlist includes {sid} without a moment explaining it")
        elif verdict == "degraded" and not any(
                r.startswith("quality:") for r in moments_by_scene[sid]["evidence"]):
            out.append(f"shortlist includes degraded {sid} without citing its quality flag")
    return out


def prune(story: dict, packet: dict, shortlist_max: int) -> tuple[dict, list[str]]:
    """Remove what still violates after repair. Returns the kept story and what was removed."""
    removed: list[str] = []

    def clean(part):
        return not check_story(part, packet, shortlist_max)

    beats = []
    for b in story.get("beats", []):
        trial = {"beats": [*beats, b], "moments": [], "shortlist": [], "excluded": []}
        (beats.append(b) if clean(trial) else removed.append(f"beat: {b.get('text', '')[:80]}"))
    moments = []
    for m in story.get("moments", []):
        if clean({"beats": [], "moments": [m], "shortlist": [], "excluded": []}):
            moments.append(m)
        else:
            removed.append(f"moment {m.get('scene')}: {m.get('what', '')[:80]}")
    shortlist = []
    for sid in story.get("shortlist", []):
        trial = {"beats": [], "moments": moments, "shortlist": [*shortlist, sid], "excluded": []}
        (shortlist.append(sid) if clean(trial) else removed.append(f"shortlist: {sid}"))
    ids = {s["id"] for s in packet["scenes"]}
    excluded = [e for e in story.get("excluded", []) if e.get("scene") in ids]
    # Every unusable scene is listed as excluded, by the harness if the model did not.
    named = {e["scene"] for e in excluded}
    for s in packet["scenes"]:
        if s["quality"]["verdict"] == "unusable" and s["id"] not in named:
            excluded.append({"scene": s["id"], "reason": "harness: unusable (" +
                             ", ".join(s["quality"]["flags"]) + ")"})
    return {"beats": beats, "moments": moments, "shortlist": shortlist,
            "excluded": excluded}, removed


def tell(packet: dict, model: str, *, transport=None, timeout: int = 600,
         shortlist_max: int | None = None, think: bool | None = False) -> dict:
    """Story for one outing, validated, repaired once, then pruned. Always returns a record."""
    ids = [s["id"] for s in packet["scenes"]]
    if not ids:
        raise ValueError("An outing needs at least one reviewed scene")
    limit = shortlist_max or max(3, round(0.3 * len(ids)))
    answer = call_json(
        model,
        [{"role": "system", "content": "You write grounded, factual recaps of outdoor "
          "action-camera footage. You never invent what the evidence does not show."},
         {"role": "user", "content": story_prompt(packet, limit)}],
        schema=story_schema(ids, evidence_refs(packet)), transport=transport,
        timeout=timeout, check=lambda s: check_story(s, packet, limit), think=think,
        output_budget=STORY_OUTPUT_TOKENS, normalize=lambda s: normalize_story(s, packet))
    record = {"version": VERSION, "outing": packet["outing"], "sport": packet["sport"],
              "packetSha256": packet_digest(packet), "model": model,
              "options": answer["options"], "think": answer.get("think"),
              "usage": answer.get("usage"), "harnessStatus": answer["status"],
              "attempts": answer["attempts"], "shortlistMax": limit,
              "violationsBeforePruning": answer["violations"],
              "isModelProposalNotOwnerJudgment": True}
    if answer["result"] is None:
        return {**record, "story": None, "removedByHarness": [],
                "failureDetail": answer.get("failureDetail"), "reply": answer.get("reply")}
    warned: list[str] = []
    check_story(answer["result"], packet, limit, warnings=warned)
    record["warnings"] = warned
    kept, removed = prune(answer["result"], packet, limit) if answer["violations"] else (
        answer["result"], [])
    if not answer["violations"]:
        kept = prune(kept, packet, limit)[0]  # adds harness exclusions only
    return {**record, "story": kept, "removedByHarness": removed}


def markdown(record: dict, packet: dict) -> str:
    """The page the owner reads, edits and rewrites."""
    by_id = {s["id"]: s for s in packet["scenes"]}
    lines = [f"# {packet['outing']} ({packet['sport']})", "",
             f"Model proposal ({record['model']}, harness {record['harnessStatus']}). "
             f"{len(packet['scenes'])} scenes, {packet['reviewedSeconds']} s reviewed of "
             f"{packet['footageSeconds']} s.", ""]
    story = record.get("story")
    if not story:
        return "\n".join(lines + [f"No story: {record.get('failureDetail')}"])
    lines.append("## Story")
    for b in story["beats"]:
        lines.append(f"- {b['text']} ({', '.join(b['scenes'])})")
    lines += ["", "## Moments (accept / wrong / rewrite)"]
    for m in story["moments"]:
        s = by_id[m["scene"]]
        lines.append(f"- **{m['scene']}** {s['file']} {s['startMs'] / 1000:.0f}-"
                     f"{s['endMs'] / 1000:.0f}s @ {s['clockStart'] or '?'} "
                     f"[{s['quality']['verdict']}]: {m['what']}. *Why:* {m['whyItMatters']} "
                     f"(evidence: {', '.join(m['evidence'])}; {m['confidence']})")
    lines += ["", "## Shortlist, best first"]
    lines += [f"{i}. {sid} {by_id[sid]['file']} {by_id[sid]['startMs'] / 1000:.0f}-"
              f"{by_id[sid]['endMs'] / 1000:.0f}s" for i, sid in enumerate(story["shortlist"], 1)]
    lines += ["", "## Excluded"]
    lines += [f"- {e['scene']}: {e['reason']}" for e in story["excluded"]]
    if record.get("warnings"):
        lines += ["", "## Worth a look (kept, but the wording may overreach)"]
        lines += [f"- {w}" for w in record["warnings"]]
    if record["removedByHarness"]:
        lines += ["", "## Removed by the harness (claims the evidence did not support)"]
        lines += [f"- {r}" for r in record["removedByHarness"]]
    return "\n".join(lines) + "\n"

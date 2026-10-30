"""Evidence-linked activity and incident review for first-person adventure footage.

No physical speed, forces, fall, injury, athlete posture, skill or biological
condition is inferred from optical flow / image-text cosine similarity. We
retain uncertainties so false positives can be corrected with real DJI labels.
"""
from __future__ import annotations

from statistics import median
import math

VERSION = "review-signals-v1"

STATE_PAIRS = {
    "ski": [
        ("chairlift_candidate", "ski_lift", "ski_downhill"),
        ("flat_traverse_candidate", "ski_flat", "ski_downhill"),
        ("stationary_candidate", "ski_stopped", "ski_downhill"),
    ],
    "bike": [
        ("stationary_candidate", "bike_stopped", "bike_riding"),
        ("easy_traverse_candidate", "bike_flat", "bike_riding"),
    ],
    "snorkeling": [
        ("surface_candidate", "at_surface", "submerged"),
    ],
}
MANUAL_INCIDENTS = {
    "crash", "fall", "near_miss", "unstable_landing", "wipeout",
    "loss_of_control", "unexpected_stop",
}
MANUAL_NEGATIVES = {
    "ski_lift", "chairlift", "lift", "flat_traverse", "slow_flat_skiing",
    "easy_ski", "boring", "non_action", "waiting", "rest", "stopped",
    "walking", "camera_adjustment", "bike_stopped", "surface_break",
    "surface_transition", "out_of_water", "breathing_break",
}


def _frame_time(frame: dict) -> int | None:
    t = frame.get("decodedPtsMs")
    if not isinstance(t, int) or isinstance(t, bool) or t < 0:
        t = frame.get("tMs")
    return t if isinstance(t, int) and not isinstance(t, bool) and t >= 0 else None


def _scene_margin(frame: dict, positive: str, reference: str) -> float | None:
    scores = frame.get("sceneScores")
    if not isinstance(scores, dict) or scores.get("method") != "siglip2_image_text_cosine":
        return None
    similarities = scores.get("similarities", {})
    p, n = similarities.get(positive), similarities.get(reference)
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool)
               and math.isfinite(v) and -1.01 <= v <= 1.01 for v in (p, n)):
        return None
    return float(p - n)


def _state_runs(frame_values: list[dict], duration_ms: int, name: str,
                *, margin: float, min_samples: int, max_gap_ms: int) -> list[dict]:
    # Include only strongly indicated frames. Neutral/contradicting observations
    # break runs rather than being used as evidence of continuity.
    frames = sorted(frame_values, key=lambda x: x["tMs"])
    runs: list[list[dict]] = []
    for x in frames:
        if x["margin"] < margin:
            continue
        if not runs or x["tMs"] - runs[-1][-1]["tMs"] > max_gap_ms or \
                any(y["margin"] < margin for y in frames
                    if runs[-1][-1]["tMs"] < y["tMs"] < x["tMs"]):
            runs.append([x])
        elif x["tMs"] > runs[-1][-1]["tMs"]:
            runs[-1].append(x)
    intervals = []
    for run in runs:
        if len(run) < min_samples:
            continue
        deltas = [run[i]["tMs"] - run[i-1]["tMs"] for i in range(1, len(run))]
        half = min(2500, round(median(deltas) / 2)) if deltas else 0
        start = max(0, run[0]["tMs"] - half)
        end = min(duration_ms, run[-1]["tMs"] + half)
        if end - start < 4000:
            continue
        intervals.append({
            "startMs": start, "endMs": end, "label": name,
            "status": "unverified_scene_hypothesis",
            "evidence": {
                "method": "sampled_siglip2_relative_similarity",
                "sampleCount": len(run),
                "medianMargin": round(median(v["margin"] for v in run), 5),
                "threshold": margin,
                "caveat": "Similarity is not an action, speed or skill probability",
            },
        })
    return intervals


def context_intervals(frames: list[dict], activity: str, duration_ms: int,
                      *, margin: float = .035, min_samples: int = 3,
                      max_gap_ms: int = 6500) -> list[dict]:
    if duration_ms < 0 or not 0 < margin < 1 or min_samples < 2 or max_gap_ms <= 0:
        raise ValueError("Invalid context parameters")
    out = []
    for label, foreground, background in STATE_PAIRS.get(activity, ()):
        samples = []
        for frame in frames:
            t = _frame_time(frame)
            if t is None or t >= duration_ms:
                continue
            diff = _scene_margin(frame, foreground, background)
            if diff is not None:
                samples.append({"tMs": t, "margin": diff})
        out.extend(_state_runs(samples, duration_ms, label, margin=margin,
                               min_samples=min_samples, max_gap_ms=max_gap_ms))
    return sorted(out, key=lambda x: (x["startMs"], x["label"]))


def disturbance_proposals(series: list[dict], duration_ms: int, *,
                          min_separation_ms: int = 5000) -> list[dict]:
    """Strong camera jolt followed by visual quiet: review, NEVER assert crash.

    Requires observable movement before the jolt, a large frame change and a
    lull after it. Source video jump cuts, rapid camera pointing and filming
    stops can all have the same signature. Don't score biomechanics from this.
    """
    rows = sorted((x for x in series if isinstance(x.get("t_ms"), int)
                   and 0 <= x["t_ms"] < duration_ms
                   and isinstance(x.get("change"), (float, int))
                   and math.isfinite(x["change"])), key=lambda x: x["t_ms"])
    if len(rows) < 14:
        return []
    candidates = []
    last = -min_separation_ms
    for i in range(7, len(rows)-6):
        item = rows[i]
        t = item["t_ms"]
        if t - last < min_separation_ms:
            continue
        before = rows[i-7:i-1]
        after = rows[i+2:i+6]
        if not before or not after:
            continue
        step = median(rows[j+1]["t_ms"]-rows[j]["t_ms"]
                      for j in range(max(0, i-6), i+5))
        if step <= 0 or step > 1500 or \
           rows[i+5]["t_ms"]-t > step * 8:
            continue
        baseline = median(max(0, float(x["change"])) for x in before)
        post = median(max(0, float(x["change"])) for x in after)
        current = max(0, float(item["change"]))
        if not (.025 <= baseline <= .28 and current >= max(.28, baseline * 3)
                and post < min(.10, baseline * .55)):
            continue
        candidates.append({
            "startMs": max(0, t - 2500),
            "endMs": min(duration_ms, t + 4500),
            "label": "abrupt_camera_disturbance",
            "status": "requires_review",
            "evidence": {
                "method": "optical_flow_change_and_post_stillness",
                "joltTimeMs": t, "frameChange": round(current, 5),
                "preChangeMedian": round(baseline, 5),
                "postChangeMedian": round(post, 5),
                "caveat": "Could be a pan, edit, camera adjustment, landing or fall",
            },
            "questions": [
                "Was there a fall, hard landing, sudden stop or camera adjustment?",
                "If a maneuver is visible, inspect its approach and recovery.",
            ],
        })
        last = t
    return candidates


def manual_incident_reviews(events: list[dict], duration_ms: int) -> list[dict]:
    out = []
    for event in events:
        if event.get("origin") != "manual" or \
                str(event.get("kind", "")).lower() not in MANUAL_INCIDENTS:
            continue
        start = int(event["start_ms"])
        end = int(event["end_ms"])
        out.append({
            "startMs": max(0, start-2500), "endMs": min(duration_ms, end+4500),
            "label": str(event["kind"]).lower(),
            "status": "human_labeled",
            "eventIds": [event["id"]],
            "evidence": {"method": "manual_review", "score": float(event["score"])},
            "questions": [
                "Review the full approach, interruption, and recovery.",
                "Note observable equipment or line-choice changes, if visible.",
            ],
        })
    return out


def review_cues(activity: str, states: list[dict], disturbances: list[dict],
                incidents: list[dict]) -> list[dict]:
    """Observational feedback, not performance diagnosis or injury evaluation."""
    cues = []
    for state in states:
        title = {
            "chairlift_candidate": "Possible lift ride",
            "flat_traverse_candidate": "Possible flat traverse",
            "stationary_candidate": "Possible stationary section",
            "easy_traverse_candidate": "Possible low-action trail",
            "surface_candidate": "Possible surface interval",
        }.get(state["label"], "Scene context to review")
        cues.append({
            "startMs": state["startMs"], "endMs": state["endMs"],
            "title": title, "category": "editorial_filter",
            "message": "Consider excluding this section from action highlights; verify the scene first.",
            "status": "unverified", "evidence": state["evidence"],
            "overlayVisibility": "private_review",
        })
    for event in disturbances:
        cues.append({
            "startMs": event["startMs"], "endMs": event["endMs"],
            "title": ("Camera jolt with acoustic change" if
                      event["evidence"].get("acousticTransientNearJolt")
                      else "Camera jolt followed by a pause"),
            "category": "incident_review",
            "message": "Inspect whether this was a maneuver, fall, camera pan or ordinary filming interruption.",
            "status": "unverified", "evidence": event["evidence"],
            "overlayVisibility": "private_review",
        })
    for event in incidents:
        cues.append({
            "startMs": event["startMs"], "endMs": event["endMs"],
            "title": "Reviewed " + event["label"].replace("_", " "),
            "category": "technique_review",
            "message": "Inspect the approach, timing, body/equipment visibility and recovery; do not infer cause from POV alone.",
            "status": "human_labeled", "evidence": event["evidence"],
            "overlayVisibility": "private_review",
        })
    return sorted(cues, key=lambda x: (x["startMs"], x["category"]))


def diagnostic_summary(asset: dict, frames: list[dict],
                       series: list[dict], events: list[dict],
                       acoustic_transients: list[dict] | None = None) -> dict:
    activity = asset["activity"]
    duration_ms = int((asset["duration"] or 0) * 1000)
    states = context_intervals(frames, activity, duration_ms)
    disturbances = disturbance_proposals(series, duration_ms)
    acoustic_transients = acoustic_transients or []
    for item in disturbances:
        jolt = item["evidence"]["joltTimeMs"]
        coincident = [
            e for e in acoustic_transients
            if abs(int(e["startMs"])-jolt) <= 1750
        ]
        item["evidence"]["acousticTransientNearJolt"] = bool(coincident)
        item["evidence"]["acousticTransientCount"] = len(coincident)
        item["evidence"]["acousticCaveat"] = (
            "Possible impact sound, movement, environment or microphone noise; "
            "never a confirmed crash"
        )
    incidents = manual_incident_reviews(events, duration_ms)
    return {
        "schemaVersion": 1, "algorithm": VERSION,
        "assetId": asset["id"], "activity": activity,
        "durationMs": duration_ms, "contextStates": states,
        "cameraDisturbances": disturbances,
        "reviewedIncidents": incidents,
        "reviewCues": review_cues(activity, states, disturbances, incidents),
        "limitations": [
            "Optical-flow shocks are not confirmed crashes or injuries",
            "Zero-shot scene similarities are not calibrated activity labels",
            "First-person footage alone cannot measure athlete biomechanics or skill",
            "Review content remains private and separate from public reel captions",
        ],
    }

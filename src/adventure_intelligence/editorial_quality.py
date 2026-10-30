"""Conservative evidence-aware editorial planning from existing Klip artifacts.

Do not equate high camera motion with skiing, swimming, or quality. Negative
manual labels are deterministic hard excludes. Zero-shot context is a *proposal*
unless an explicitly chosen automatic policy is enabled.
"""
from __future__ import annotations

from collections import Counter
from statistics import median
import math

from .artifacts import read_artifact
from .semantic_jobs import completed_document
from .acoustics import AUDIO_VERSION, acoustic_changes
from .submersion import submersion_samples, continuous_underwater_intervals
from .review_signals import context_intervals, disturbance_proposals

VERSION = "editorial-context-v1"
NEGATIVE_LABELS = {
    "ski": {"ski_lift", "chairlift", "lift", "rest", "stopped", "non_action",
            "camera_adjustment", "walking", "waiting", "flat_traverse",
            "slow_flat_skiing", "easy_ski", "boring"},
    "bike": {"rest", "stopped", "non_action", "camera_adjustment", "waiting",
             "easy_traverse", "slow_flat_riding", "boring"},
    "snorkeling": {"surface_break", "surface_transition", "out_of_water",
                   "breathing_break", "camera_adjustment", "non_action"},
}
POSITIVE_LABELS = {
    "ski": {"skiing", "active_ski", "jump", "carve", "turn", "tree_weave"},
    "bike": {"riding", "active_bike", "jump", "berm", "rock_garden"},
    "snorkeling": {"submerged", "underwater_stable", "underwater_encounter",
                   "animal_presence"},
}
NEGATIVE_SCENE = {"ski": "ski_lift", "snorkeling": "at_surface", "bike": "bike_stopped"}
POSITIVE_SCENE = {"ski": "ski_downhill", "snorkeling": "submerged", "bike": "bike_riding"}


def _overlap(a: int, b: int, c: int, d: int) -> int:
    return max(0, min(b, d) - max(a, c))


def manual_exclusions(events: list[dict], activity: str) -> list[tuple[int, int, str]]:
    out = []
    for event in events:
        if event.get("origin") != "manual":
            continue
        kind = str(event.get("kind", "")).strip().lower()
        if kind in NEGATIVE_LABELS.get(activity, set()):
            out.append((max(0, int(event["start_ms"]) - 300),
                        int(event["end_ms"]) + 300, kind))
    return sorted(out)


def subtract_exclusions(start: int, end: int, excluded: list[tuple[int, int, str]],
                        *, minimum_ms: int = 3000) -> list[tuple[int, int]]:
    pieces = [(start, end)]
    for left, right, _ in excluded:
        next_pieces = []
        for a, b in pieces:
            if left >= b or right <= a:
                next_pieces.append((a, b))
            else:
                if left > a:
                    next_pieces.append((a, left))
                if right < b:
                    next_pieces.append((right, b))
        pieces = next_pieces
    return [(a, b) for a, b in pieces if b - a >= minimum_ms]


def bounded_windows(start: int, end: int, *, window_ms: int = 18000,
                    stride_ms: int = 12000, min_ms: int = 3500) -> list[tuple[int, int]]:
    if end - start < min_ms:
        return []
    if end - start <= window_ms:
        return [(start, end)]
    points = list(range(start, max(start + 1, end - window_ms + 1), stride_ms))
    points.append(end - window_ms)
    return list(dict.fromkeys((t, min(end, t + window_ms)) for t in points))


def _scene_frames(db, workspace, asset_id: str) -> list[dict]:
    """Read only active, checksum-verified zero-shot context observations."""
    sql = ("""SELECT j.* FROM semantic_jobs j JOIN semantic_active a
              ON a.asset_id=j.asset_id AND a.config_id=j.config_id
              WHERE j.asset_id=? AND j.status='completed' AND a.provider='context'""")
    frames = []
    for row in db.execute(sql, (asset_id,)):
        doc = completed_document(workspace, row)
        if doc is not None:
            frames.extend(f for f in doc.get("frames", [])
                          if isinstance(f.get("sceneScores"), dict))
    return frames


def scene_evidence(frames: list[dict], start: int, end: int, activity: str) -> dict:
    """Summarize relative zero-shot evidence, *not* calibrated probabilities."""
    negative = NEGATIVE_SCENE.get(activity)
    positive = POSITIVE_SCENE.get(activity)
    samples = []
    for f in frames:
        t = f.get("decodedPtsMs")
        if not isinstance(t, int) or t < 0:
            t = f.get("tMs", -1)
        if not isinstance(t, int) or not start <= t < end:
            continue
        score = f["sceneScores"]
        if score.get("method") != "siglip2_image_text_cosine":
            continue
        similarities = score.get("similarities", {})
        if negative not in similarities or positive not in similarities:
            continue
        n = similarities[negative]
        p = similarities[positive]
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (n, p)):
            continue
        samples.append({"tMs": t, "negative": float(n), "positive": float(p),
                        "margin": round(float(n - p), 5)})
    negative_votes = sum(x["margin"] >= .02 for x in samples)
    positive_votes = sum(x["margin"] <= -.02 for x in samples)
    # Require multiple independent temporal samples. Highly correlated frames
    # are not multiplied into a falsely precise posterior probability.
    flagged = len(samples) >= 3 and negative_votes / len(samples) >= .70
    supported = len(samples) >= 3 and positive_votes / len(samples) >= .70
    return {"status": "unverified", "method": "relative_zero_shot_similarity",
            "observations": len(samples), "negativeLabel": negative,
            "positiveLabel": positive,
            "negativeVotes": negative_votes, "positiveVotes": positive_votes,
            "likelyNegativeContext": flagged, "likelyPositiveContext": supported,
            "medianMargin": round(median(x["margin"] for x in samples), 5) if samples else None}


def motion_evidence(series: list[dict], start: int, end: int) -> dict:
    selected = [x for x in series if start <= x.get("t_ms", -1) < end]
    if not selected:
        return {"samples": 0, "quality": None, "caveat": "No usable motion samples"}
    changes = [max(0.0, float(x.get("change", 0))) for x in selected]
    coherence = [max(0.0, min(1.0, float(x.get("flow_coherence", 0))))
                 for x in selected if "flow_coherence" in x]
    change_median = median(changes)
    # A smooth scene and motion evidence both contribute, without claiming that
    # either proves action. Quality is a heuristic and NOT a model confidence.
    active = min(1.0, change_median / .12)
    flow = median(coherence) if coherence else None
    quality = .6 * active + .4 * flow if flow is not None else .55 * active
    return {"samples": len(selected), "medianPixelChange": round(change_median, 5),
            "medianFlowCoherence": round(flow, 4) if flow is not None else None,
            "quality": round(quality, 4),
            "caveat": "Image-derived signal, not athletic motion or breathing"}


def raw_motion_for_asset(db, workspace, asset_id: str) -> list[dict]:
    for stage in ("flow", "motion"):
        analysis = db.execute("SELECT * FROM analyses WHERE asset_id=? AND stage=?"
                              " AND status='completed'", (asset_id, stage)).fetchone()
        if not analysis:
            continue
        doc = read_artifact(db, workspace, asset_id, analysis["version"].split("|")[0])
        if doc is not None:
            return doc["series"]
    return []


def editorial_candidates(db, workspace, episode_rows: list[dict], *,
                         policy: str = "suggest", max_window_ms: int = 18000) -> tuple[list[dict], list[dict]]:
    """Produce bounded, explainable selection units with explicit gate reasons."""
    if policy not in {"suggest", "automatic"}:
        raise ValueError("Context policy must be suggest or automatic")
    assets = {row["id"]: dict(row) for row in db.execute("SELECT * FROM assets")}
    events_by_asset = {}
    series_by_asset = {}
    scenes_by_asset = {}
    acoustic_by_asset = {}
    submerged_by_asset = {}
    contexts_by_asset = {}
    disturbances_by_asset = {}
    for aid in {ep["asset_id"] for ep in episode_rows}:
        events_by_asset[aid] = [dict(x) for x in db.execute(
            "SELECT * FROM events WHERE asset_id=?", (aid,))]
        series_by_asset[aid] = raw_motion_for_asset(db, workspace, aid)
        scenes_by_asset[aid] = _scene_frames(db, workspace, aid)
        wave = read_artifact(db, workspace, aid, AUDIO_VERSION)
        acoustic_by_asset[aid] = acoustic_changes(wave["series"]) if wave else []
        asset = assets.get(aid)
        if asset is not None:
            duration = int((asset["duration"] or 0) * 1000)
            contexts_by_asset[aid] = context_intervals(
                scenes_by_asset[aid], asset["activity"], duration)
            disturbances_by_asset[aid] = disturbance_proposals(
                series_by_asset[aid], duration)
        if asset is not None and asset["activity"] == "snorkeling":
            submerged_by_asset[aid] = continuous_underwater_intervals(
                submersion_samples(scenes_by_asset[aid]),
                duration_ms=int((asset["duration"] or 0) * 1000))
    candidates = []
    exclusions = []
    for ep in episode_rows:
        asset = assets.get(ep["asset_id"])
        if asset is None:
            continue
        activity = asset["activity"]
        aid = asset["id"]
        excluded = manual_exclusions(events_by_asset[aid], activity)
        start, end = int(ep["start_ms"]), int(ep["end_ms"])
        allowed = subtract_exclusions(start, end, excluded)
        continuous = submerged_by_asset.get(aid, [])
        # The explicit automatic policy can constrain to unverified but
        # visually sustained submerged intervals. Missing evidence is not
        # interpreted as a confirmed surface interruption.
        if activity == "snorkeling" and policy == "automatic" and continuous:
            allowed = [(max(a, interval["startMs"]), min(b, interval["endMs"]))
                       for a, b in allowed for interval in continuous
                       if min(b, interval["endMs"]) - max(a, interval["startMs"]) >= 3500]
        if not allowed:
            exclusions.append({"episodeId": ep["id"], "reason": "manual_negative_label"})
            continue
        is_action = ep["kind"] in {"jump_chain", "carving_flow", "tree_weaving",
                                   "technical_feature_chain", "wildlife_encounter"}
        # A complete verified/manual episode can remain intact up to 60 seconds.
        window = min(60000, max_window_ms * 3) if is_action else max_window_ms
        for a, b in allowed:
            for wstart, wend in bounded_windows(a, b, window_ms=window,
                                                stride_ms=max(2000, int(window * 2 / 3))):
                motion = motion_evidence(series_by_asset[aid], wstart, wend)
                scene = scene_evidence(scenes_by_asset[aid], wstart, wend, activity)
                overlap_ms = sum(_overlap(wstart, wend, part["startMs"], part["endMs"])
                                 for part in continuous)
                continuity_fraction = min(1, overlap_ms / max(1, wend - wstart))
                audio_events = [e for e in acoustic_by_asset.get(aid, [])
                                if _overlap(wstart, wend, e["startMs"], e["endMs"]) > 0]
                surface_samples = [x["tMs"] for x in submersion_samples(scenes_by_asset[aid])
                                   if x["state"] == "surface"] if activity == "snorkeling" else []
                coincident = sum(any(abs(e["startMs"] - t) < 2000
                                     for t in surface_samples) for e in audio_events)
                context_overlap = [
                    {"label": st["label"], "overlapMs": _overlap(
                        wstart, wend, st["startMs"], st["endMs"])}
                    for st in contexts_by_asset.get(aid, [])
                    if _overlap(wstart, wend, st["startMs"], st["endMs"]) > 0
                ]
                # A predicted lift/flat interval is only a ranking penalty;
                # human-reviewed exclusions remain the only hard default gate.
                low_value_fraction = min(1, sum(
                    x["overlapMs"] for x in context_overlap) / max(1, wend-wstart))
                camera_disturbances = [
                    {"label": d["label"], "startMs": d["startMs"],
                     "endMs": d["endMs"]}
                    for d in disturbances_by_asset.get(aid, [])
                    if _overlap(wstart, wend, d["startMs"], d["endMs"]) > 0
                ]
                sound = {"method": "rms_outlier", "status": "unverified",
                         "available": aid in acoustic_by_asset and
                                      bool(read_artifact(db, workspace, aid, AUDIO_VERSION)),
                         "transientCount": len(audio_events),
                         "surfaceCoincidentTransients": coincident,
                         "interpretation": "Potential camera/water disturbance, not breathing state"}
                overlapping_negatives = [x[2] for x in excluded
                                         if _overlap(start, end, x[0], x[1])]
                if policy == "automatic" and scene["likelyNegativeContext"]:
                    exclusions.append({"episodeId": ep["id"], "startMs": wstart,
                                       "endMs": wend, "reason": "unverified_scene_context_gate"})
                    continue
                if activity == "snorkeling" and policy == "automatic" and scene["observations"] >= 3 \
                        and not scene["likelyPositiveContext"]:
                    exclusions.append({"episodeId": ep["id"], "startMs": wstart,
                                       "endMs": wend, "reason": "submerged_scene_not_supported"})
                    continue
                # Low motion / weak generic proposals should not crowd out
                # a confirmed action, while a long span gets no length bonus.
                base = max(0.0, min(1.0, float(ep["score"])))
                strength = motion["quality"] if motion["quality"] is not None else .35
                score = .48 * base + .52 * strength
                if ep["kind"] in {"sustained_motion_candidate", "active_sequence"}:
                    score *= .78
                if ep["kind"] == "camera_rhythm_candidate":
                    score *= .88
                if scene["likelyNegativeContext"]:
                    score *= .30
                if scene["likelyPositiveContext"]:
                    score = min(1.0, score + .08)
                if activity == "snorkeling" and continuous:
                    score = min(1.0, score + .14 * continuity_fraction)
                    if continuity_fraction < .25:
                        score *= .65
                # Audio supports visible surface transitions but does not alone
                # trigger a breathing or interruption classification.
                if activity == "snorkeling" and coincident:
                    score *= .80
                if activity in {"ski", "bike"} and low_value_fraction:
                    score *= max(.2, 1 - .7 * low_value_fraction)
                # A possible crash is a dedicated private review finding.
                # Never score it as athletic quality, skill or injury.
                candidate = {
                    "episodeId": ep["id"], "sourceAssetId": aid, "kind": ep["kind"],
                    "startMs": wstart, "endMs": wend,
                    "duration": round((wend - wstart) / 1000, 3),
                    "score": round(score, 4), "originalEpisodeScore": base,
                    "motionEvidence": motion, "sceneEvidence": scene,
                    "acousticEvidence": sound,
                    "lowValueContext": {"status": "unverified",
                        "supportedFraction": round(low_value_fraction, 3),
                        "intervals": context_overlap},
                    "incidentReviewFlags": camera_disturbances,
                    "underwaterContinuity": {"status": "unverified",
                        "supportedFraction": round(continuity_fraction, 3),
                        "intervalCount": len(continuous),
                        "interpretation": "camera continuously submerged (candidate)"} if
                        activity == "snorkeling" else None,
                    "reasonCodes": (["manual_exclusion_trimmed"] if overlapping_negatives else [])
                                   + (["suspected_non_action_scene"] if scene["likelyNegativeContext"] else [])
                                   + (["likely_active_scene"] if scene["likelyPositiveContext"] else [])
                                   + (["continuous_submersion_candidate"]
                                      if activity == "snorkeling" and continuity_fraction >= .75 else [])
                                   + (["visual_surface_and_acoustic_disturbance"]
                                      if coincident else [])
                                   + (["low_value_scene_hypothesis"]
                                      if low_value_fraction > .25 else [])
                                   + (["camera_disturbance_requires_review"]
                                      if camera_disturbances else [])
                                   + (["unverified_camera_motion"] if not is_action else []),
                    "requiresReview": True,
                    "rankingVersion": VERSION,
                }
                candidates.append(candidate)
    return candidates, exclusions


def select_diverse(candidates: list[dict], *, max_seconds: int,
                   max_per_asset: int = 3) -> list[dict]:
    """Greedy diversity with non-overlap, duration budget and no length reward."""
    taken: list[dict] = []
    per_asset = Counter()
    seconds = 0.0
    for item in sorted(candidates, key=lambda x: (-x["score"], x["sourceAssetId"],
                                                   x["startMs"])):
        aid = item["sourceAssetId"]
        if per_asset[aid] >= max_per_asset or seconds + item["duration"] > max_seconds:
            continue
        if any(previous["sourceAssetId"] == aid and
               _overlap(item["startMs"], item["endMs"], previous["startMs"], previous["endMs"]) > 0
               for previous in taken):
            continue
        taken.append(item)
        per_asset[aid] += 1
        seconds += item["duration"]
    return taken

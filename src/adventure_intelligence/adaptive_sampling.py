"""Read-only, budgeted adaptive frame scheduling for egocentric adventure footage.

This planner consults ONLY checksum-verified existing local feature/semantic caches
and the local SQLite catalog. It never decodes, downloads models, uploads files, or
writes to source footage. Evidence-based priority is a heuristic, not classification.
All recordings keep a deterministic sparse coverage floor.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from statistics import median

from .analyze import MOTION_VERSION
from .artifacts import read_artifact
from .catalog import connect
from .examples import latest_examples
from .scene_atlas import frames_for_asset, similarity, unit_vector

VERSION = "adaptive-pov-schedule-v1"
ALLOWED_ACTIVITIES = {"ski", "bike", "snorkeling", "unknown"}
WILDLIFE_QUERIES = {
    "fish", "shark", "ray", "sea turtle", "coral", "underwater reef",
    "octopus", "jellyfish", "sea lion", "dolphin", "whale",
    "kelp", "seagrass", "nudibranch", "sea star",
}
ACTION_TYPES = {
    "carving_flow", "tree_weaving", "technical_feature_chain",
    "jump_chain", "wildlife_encounter", "sustained_camera_motion",
    "visual_activity", "active_sequence",
}


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _time(frame):
    raw = frame.get("decodedPtsMs")
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 0:
        raw = frame.get("tMs")
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _novelty_points(frames, model_key):
    """Only compare vectors produced by one verified and homogeneous model."""
    if model_key is None:
        return []
    result = []
    previous = None
    for frame in sorted(frames, key=lambda x: x.get("tMs", -1)):
        t = _time(frame)
        vector = unit_vector(frame.get("embedding"))
        if t is None or vector is None:
            continue
        if previous is not None:
            sim = similarity(previous, vector)
            if sim is not None and sim < .84:
                result.append((t, round(1 - sim, 4)))
        previous = vector
    return result


def _candidate_schedule(db, workspace: Path, asset, *, coverage_seconds: float,
                        interest_seconds: float, max_frames: int) -> dict:
    aid = asset["id"]
    duration = int(float(asset["duration"]) * 1000)
    if duration < 1:
        raise ValueError("Positive usable duration required")
    flank = round(interest_seconds * 1000)
    activity = asset["activity"] if asset["activity"] in ALLOWED_ACTIVITIES else "unknown"
    # Do not falsely promise a fixed 30s floor when the user sets a restrictive
    # cap on a long recording. Explicitly report the achievable interval.
    requested_ms = round(coverage_seconds * 1000)
    stride = max(requested_ms, math.ceil(duration / max_frames))
    times = list(range(0, duration, stride))
    reserved = set(times)
    entries = {t: {"tMs": t, "priority": 0, "reasons": {"coverage_probe"}} for t in times}
    cache_inputs = {"motion": False, "events": 0, "reviewedKeeps": 0,
                    "objectObservations": 0, "embeddingChanges": 0,
                    "lowValueContextSamples": 0}

    def add(t, value, reason, offsets=(0,)):
        for offset in offsets:
            at = max(0, min(duration - 1, int(t + offset)))
            # Keep timestamps bounded at 1-second precision for cache reuse
            # across plans. Deliberate endpoints 0 and duration-1 remain exact.
            at = (at // 1000) * 1000 if at < duration - 1 else at
            current = entries.setdefault(at, {"tMs": at, "priority": 0, "reasons": set()})
            current["priority"] = max(current["priority"], value)
            current["reasons"].add(reason)

    artifact = read_artifact(db, workspace, aid, MOTION_VERSION)
    if artifact is not None:
        series = artifact.get("series", [])
        changes = []
        for row in series:
            t, change = row.get("t_ms"), row.get("change")
            if (isinstance(t, (float, int)) and not isinstance(t, bool)
                    and isinstance(change, (float, int)) and not isinstance(change, bool)
                    and math.isfinite(t) and math.isfinite(change)
                    and 0 <= t < duration and 0 <= change <= 1):
                changes.append((int(t), float(change)))
        cache_inputs["motion"] = bool(changes)
        if changes:
            levels = sorted(x[1] for x in changes)
            baseline = median(levels)
            p85 = levels[int((len(levels) - 1) * .85)]
            by_bucket = {}
            for t, val in changes:
                bucket = (t // 6000) * 6000
                if val > by_bucket.get(bucket, 0):
                    by_bucket[bucket] = val
            # High pixel-change is only an inspection trigger; underwater water
            # turbulence and helmet shake can mimic meaningful action.
            threshold = max(.045, baseline * 1.7, p85 * .95)
            strong = [(v, t) for t, v in by_bucket.items() if v >= threshold]
            strong.sort(key=lambda x: (-x[0], x[1]))
            count = min(10 if activity != "snorkeling" else 5, max(2, max_frames // 10))
            spaced = []
            for strength, t in strong:
                if all(abs(t - prev) >= 12000 for prev in spaced):
                    spaced.append(t)
                    add(t + 3000, 45 + 10 * min(1, strength / .3),
                        "motion_change_unverified", (-flank, 0, flank))
                if len(spaced) >= count:
                    break

    event_rows = db.execute(
        "SELECT kind,start_ms,end_ms,score,origin FROM events WHERE asset_id=? "
        "AND start_ms<end_ms ORDER BY score DESC,start_ms LIMIT 32", (aid,)
    ).fetchall()
    for ev in event_rows:
        if ev["kind"] not in ACTION_TYPES:
            continue
        mid = (int(ev["start_ms"]) + int(ev["end_ms"])) // 2
        confidence = float(ev["score"])
        priority = (95 if ev["origin"] == "manual" else 50) + 12 * confidence
        add(mid, priority, "event_candidate_unverified", (-flank, 0, flank))
        cache_inputs["events"] += 1

    for ex in latest_examples(db, asset_id=aid):
        if ex["decision"] == "confirmed" and ex["highlight"] == "keep":
            mid = (int(ex["start_ms"]) + int(ex["end_ms"])) // 2
            add(mid, 110, "human_reviewed_keep",
                (-2 * flank, -flank, 0, flank, 2 * flank))
            cache_inputs["reviewedKeeps"] += 1
        # 'skip' does not erase coverage. A negative may still contain an
        # exceptional rare event the prior reviewer was not looking for.

    if activity == "snorkeling":
        # Reuse either marine-specific or standard cached detector results.
        # A matching proposal at the same timestamp across providers does
        # not add extra evidence or artificial event confidence.
        detected=set()
        for provider_name in ("ecology-owlv2","owlv2"):
            owl, _ = frames_for_asset(db, workspace, aid, provider_name)
            for frame in owl:
                t = _time(frame)
                if t is None:
                    continue
                labels = {obj.get("label", "").lower() for obj in frame.get("objects", [])
                          if isinstance(obj, dict) and isinstance(obj.get("label"),str)}
                if labels & WILDLIFE_QUERIES:
                    if t in detected:
                        continue
                    detected.add(t)
                    add(t, 85, "marine_object_query_unverified",
                        (-2 * flank, -flank, 0, flank, 2 * flank))
                    cache_inputs["objectObservations"] += 1

    frames, model_key = frames_for_asset(db, workspace, aid, "siglip2")
    for t, novelty in _novelty_points(frames, model_key):
        add(t, 62 + min(12, novelty * 20), "visual_novelty_unverified",
            (-flank, 0, flank))
        cache_inputs["embeddingChanges"] += 1

    # Weak context-model evidence may DEPRIORITIZE extra investigation, but can
    # never remove periodic coverage or a human-reviewed keep. These relative
    # similarities are explicitly not calibrated ski/trail classifiers.
    if activity in {"ski", "bike"}:
        context_frames, _ = frames_for_asset(db, workspace, aid, "context")
        negative = ("ski_lift", "ski_flat", "ski_stopped") if activity == "ski" else (
            "bike_flat", "bike_stopped")
        positive = "ski_downhill" if activity == "ski" else "bike_riding"
        possible_negative = []
        for frame in context_frames:
            t = _time(frame)
            scores = (frame.get("sceneScores") or {}).get("similarities") or {}
            if t is None or positive not in scores or not all(x in scores for x in negative):
                continue
            if not all(isinstance(v, (int, float)) and math.isfinite(v)
                       for v in [scores[positive], *(scores[x] for x in negative)]):
                continue
            if max(scores[k] for k in negative) - scores[positive] > .035:
                possible_negative.append(t)
        for item in entries.values():
            if item["tMs"] in reserved or "human_reviewed_keep" in item["reasons"]:
                continue
            supports = sum(abs(item["tMs"] - t) <= 20000 for t in possible_negative)
            if supports >= 2:
                item["priority"] = max(0, item["priority"] - 25)
                item["reasons"].add("low_value_context_unverified")
                cache_inputs["lowValueContextSamples"] += 1

    # Deterministic exploration samples preserve discovery pressure even for
    # quiet regions or clips without any feature caches.
    for n in range(min(8, max(2, max_frames // 24))):
        payload = f"{VERSION}:{aid}:{n}".encode("ascii")
        t = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") % duration
        add(t, 20, "deterministic_exploration_probe")

    additional = [v for t, v in entries.items() if t not in reserved]
    additional.sort(key=lambda item: (-item["priority"], item["tMs"]))
    allowance = max(0, max_frames - len(reserved))
    selected = [entries[t] for t in reserved] + additional[:allowance]
    selected.sort(key=lambda item: item["tMs"])
    frames_out = [
        {"tMs": int(item["tMs"]), "reasons": sorted(item["reasons"])}
        for item in selected
    ]
    gaps = [b["tMs"] - a["tMs"] for a, b in zip(frames_out, frames_out[1:])]
    tail_gap = max(0, duration - 1 - frames_out[-1]["tMs"])
    max_gap = max([tail_gap, *gaps], default=0)
    reason_counts = {}
    for frame in frames_out:
        for reason in frame["reasons"]:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {
        "assetId": aid, "activity": activity, "durationMs": duration,
        "frames": frames_out, "frameCount": len(frames_out),
        "effectiveCoverageIntervalMs": stride,
        "maximumScheduledGapMs": max_gap,
        "coverageFrames": len(reserved),
        "interestFrames": len(frames_out) - len(reserved),
        "cacheEvidence": cache_inputs, "reasonCounts": reason_counts,
        "samplingIsNotEventRecallGuarantee": True,
    }


def build_plan(workspace: Path, *, coverage_seconds=45, interest_seconds=2,
               max_frames=128, limit=None, activity="all",
               asset_ids=None) -> dict:
    if (not math.isfinite(coverage_seconds) or not 5 <= coverage_seconds <= 3600
            or not math.isfinite(interest_seconds) or not .25 <= interest_seconds <= 30
            or interest_seconds > coverage_seconds):
        raise ValueError("Invalid coverage or interest sample interval")
    if not isinstance(max_frames, int) or isinstance(max_frames, bool) or not 8 <= max_frames <= 512:
        raise ValueError("max_frames must be an integer in [8,512]")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0):
        raise ValueError("limit must be positive")
    if activity not in ALLOWED_ACTIVITIES | {"all"}:
        raise ValueError("Unknown activity")
    requested=None
    if asset_ids is not None:
        if (not isinstance(asset_ids,(set,list,tuple)) or
                not 1 <= len(asset_ids) <= 16 or
                any(not isinstance(a,str) or len(a)!=64
                    or any(c not in "0123456789abcdef" for c in a)
                    for a in asset_ids) or len(set(asset_ids))!=len(asset_ids)):
            raise ValueError("Explicit asset selection requires 1..16 unique full SHA-256 IDs")
        requested=set(asset_ids)
        if limit is not None and len(requested)>limit:
            raise ValueError("Explicit asset selection exceeds the plan limit")
    workspace = Path(workspace).expanduser().resolve()
    with connect(workspace) as db:
        # A UI activity override is optional in older catalogs. Unknown means
        # unknown, never infer a sport from filename or motion.
        has_org = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                             "AND name='asset_organization'").fetchone() is not None
        sql = ("SELECT a.id,a.duration,"
               "COALESCE(NULLIF(o.activity_override,''),a.activity) AS activity "
               "FROM assets a LEFT JOIN asset_organization o ON a.id=o.asset_id "
               "ORDER BY a.created_at,a.id" if has_org else
               "SELECT id,duration,activity FROM assets ORDER BY created_at,id")
        rows = [dict(r) for r in db.execute(sql)]
        rows = [r for r in rows if r["duration"] is not None and
                math.isfinite(float(r["duration"])) and r["duration"] > 0 and
                (activity == "all" or r["activity"] == activity) and
                (requested is None or r["id"] in requested)]
        if requested is not None and set(r["id"] for r in rows)!=requested:
            raise ValueError("One or more selected assets are missing, invalid-duration, or mismatched activity")
        if limit is not None:
            rows = rows[:limit]
        assets = [_candidate_schedule(db, workspace, a,
                    coverage_seconds=coverage_seconds,
                    interest_seconds=interest_seconds,
                    max_frames=max_frames) for a in rows]
    plan = {
        "schemaVersion": 1, "algorithm": VERSION, "sourcePolicy": "read_only",
        "evidencePolicy": "cached_only_unverified",
        "parameters": {"coverageSeconds": coverage_seconds,
                       "interestSeconds": interest_seconds,
                       "maxFramesPerAsset": max_frames,
                       "activityFilter": activity},
        "assets": assets,
        "totals": {
            "assets": len(assets),
            "scheduledFrames": sum(a["frameCount"] for a in assets),
            "coverageFrames": sum(a["coverageFrames"] for a in assets),
            "interestFrames": sum(a["interestFrames"] for a in assets),
            "missingMotionCaches": sum(not a["cacheEvidence"]["motion"] for a in assets),
            "activityCounts": {a: sum(v["activity"] == a for v in assets)
                               for a in sorted(ALLOWED_ACTIVITIES)},
        },
        "limitations": [
            "Motion and object proposals are not verified sport technique or species",
            "Sparse coverage cannot guarantee recall of brief events",
            "Source integrity is verified again before costly semantic extraction",
            "No automatic deletion or acceptance of clips",
        ],
    }
    if requested is not None:
        plan["parameters"]["selectedAssetIds"] = sorted(requested)
    plan["planId"] = hashlib.sha256(_encoded(plan).encode()).hexdigest()
    return plan


def validate_plan(plan: dict) -> dict[str, list[int]]:
    """Validate untrusted JSON before any model inference or catalog writes."""
    if not isinstance(plan, dict) or plan.get("schemaVersion") != 1 or plan.get("algorithm") != VERSION:
        raise ValueError("Unknown adaptive schedule format")
    original = {k: v for k, v in plan.items() if k != "planId"}
    if (not isinstance(plan.get("planId"), str)
            or hashlib.sha256(_encoded(original).encode()).hexdigest() != plan["planId"]):
        raise ValueError("Adaptive plan checksum mismatch")
    assets = plan.get("assets")
    if not isinstance(assets, list) or not 0 < len(assets) <= 100000:
        raise ValueError("Adaptive plan must contain indexed assets")
    result = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError("Malformed plan asset")
        aid, duration, frames = asset.get("assetId"), asset.get("durationMs"), asset.get("frames")
        if (not isinstance(aid, str) or len(aid) != 64
                or any(c not in "0123456789abcdef" for c in aid)
                or not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0
                or not isinstance(frames, list) or not 1 <= len(frames) <= 512
                or aid in result):
            raise ValueError("Invalid adaptive asset identity or budget")
        times = [f.get("tMs") if isinstance(f, dict) else None for f in frames]
        if any(not isinstance(t, int) or isinstance(t, bool) or t < 0 or t >= duration
               for t in times) or times != sorted(set(times)):
            raise ValueError("Invalid adaptive timestamps")
        result[aid] = times
    return result


def source_preflight(workspace: Path, asset_ids: set[str]) -> dict:
    """Cheap mount/path check before attempting expensive model execution.

    Path existence is NOT content verification: the semantic runner always
    verifies source SHA-256 before trusting decoded frames.
    """
    if not asset_ids:
        raise ValueError("Preflight requires indexed asset IDs")
    failures = []
    with connect(Path(workspace).expanduser().resolve()) as db:
        for aid in sorted(asset_ids):
            row = db.execute("SELECT stored_path FROM assets WHERE id=?", (aid,)).fetchone()
            if row is None:
                failures.append({"assetId": aid, "reason": "not_in_catalog"})
                continue
            paths = [row["stored_path"]]
            paths += [r[0] for r in db.execute(
                "SELECT original_path FROM imports WHERE asset_id=?", (aid,))]
            possible = False
            for source in dict.fromkeys(paths):
                try:
                    p = Path(source)
                    if p.is_file() and p.stat().st_size > 0:
                        possible = True
                        break
                except OSError:
                    pass
            if not possible:
                failures.append({"assetId": aid, "reason": "no_accessible_source_path"})
    return {"checked": len(asset_ids), "possiblyAvailable": len(asset_ids)-len(failures),
            "unavailable": failures,
            "note": "Presence only; full SHA-256 verification happens during extraction"}

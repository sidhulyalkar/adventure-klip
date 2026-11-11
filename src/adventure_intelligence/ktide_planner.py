"""Build balanced archive-wide K-TIDE training windows from local feature caches.

Requires user-verified trip IDs and explicit sub-sport/goal assignments.
Uniform temporal windows, including quiet footage, do not use existing
Klip event/highlight scores. Never opens/remaps original video files.
"""
from __future__ import annotations

from collections import Counter
import math

from .encoder_dataset import KEY, SHA
from .encoder_export import SELECTION_VERSION
from .scene_profiles import SUBSPORT_PRIORS
from .scene_relevance import GOAL_WEIGHTS

ASSIGNMENTS_VERSION = "klip-ktide-source-assignments-v1"


def validate_assignments(document: dict) -> list[dict]:
    if (not isinstance(document, dict)
            or set(document) != {"version", "sources"}
            or document["version"] != ASSIGNMENTS_VERSION):
        raise ValueError("Unsupported source assignments format")
    rows = document["sources"]
    if not isinstance(rows, list) or not 3 <= len(rows) <= 5000:
        raise ValueError("Need 3..5000 explicitly grouped source assignments")
    seen, groups = set(), set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
                "sourceSha256", "groupId", "goal", "subsport"}:
            raise ValueError("Invalid source assignment")
        sha, group = row["sourceSha256"], row["groupId"]
        if not isinstance(sha, str) or not SHA.fullmatch(sha) or sha in seen:
            raise ValueError("Invalid or duplicate source SHA")
        seen.add(sha)
        if not isinstance(group, str) or not KEY.fullmatch(group):
            raise ValueError("Provide a nonempty stable trip/session group")
        groups.add(group)
        if (not isinstance(row["goal"], str)
                or not isinstance(row["subsport"], str)
                or row["goal"] not in GOAL_WEIGHTS
                or row["subsport"] not in SUBSPORT_PRIORS):
            raise ValueError("Explicit goal and sub-sport required")
    if len(groups) < 3:
        raise ValueError("At least three independently assigned trips required")
    return rows


def plan_cached_windows(workspace, assignments: dict, *,
                        provider: str = "siglip2",
                        window_seconds: int = 30,
                        stride_seconds: int = 30,
                        min_cached_frames: int = 4,
                        max_per_source: int = 20) -> dict:
    if provider not in {"siglip2", "histogram"}:
        raise ValueError("Only completed frozen feature providers supported")
    if (type(window_seconds) is not int or not 5 <= window_seconds <= 120
            or type(stride_seconds) is not int or not 5 <= stride_seconds <= window_seconds
            or type(min_cached_frames) is not int or not 4 <= min_cached_frames <= 256
            or type(max_per_source) is not int or not 1 <= max_per_source <= 500):
        raise ValueError("Invalid temporal coverage parameters")
    selected = validate_assignments(assignments)
    from .catalog import connect
    from .scene_atlas import frames_for_asset, unit_vector
    root = workspace.resolve(strict=True)
    output = []
    source_groups = set()
    model_key = None
    with connect(root) as db:
        for entry in selected:
            aid = entry["sourceSha256"]
            asset = db.execute(
                "SELECT duration FROM assets WHERE id=?", (aid,)).fetchone()
            if asset is None:
                raise ValueError("Assigned source not in local catalog: " + aid[:12])
            duration = asset["duration"]
            if (not isinstance(duration, (int, float))
                    or isinstance(duration, bool)
                    or not math.isfinite(duration) or duration <= 0):
                raise ValueError("Assigned source has no supported duration")
            limit_ms = int(duration * 1000)
            frames, key = frames_for_asset(db, root, aid, provider)
            if not key or not frames:
                raise ValueError("Assigned source has no completed semantic features")
            if model_key is None:
                model_key = key
            if model_key != key:
                raise ValueError("Incompatible frozen feature revision across sources")
            times = sorted({
                f["tMs"] for f in frames if isinstance(f, dict)
                and type(f.get("tMs")) is int and 0 <= f["tMs"] < limit_ms
                and unit_vector(f.get("embedding")) is not None
            })
            candidate = []
            start = 0
            while start < limit_ms:
                end = min(start + window_seconds * 1000, limit_ms)
                if sum(start <= t < end for t in times) >= min_cached_frames:
                    candidate.append((start, end))
                start += stride_seconds * 1000
            if len(candidate) > max_per_source:
                if max_per_source == 1:
                    candidate = [candidate[len(candidate) // 2]]
                else:
                    indices = [int(i * (len(candidate)-1) / (max_per_source-1))
                               for i in range(max_per_source)]
                    candidate = [candidate[i] for i in indices]
            for start, end in candidate:
                output.append({
                    "sampleId": f"ktide-{aid[:12]}-{start:010d}-{end:010d}",
                    "sourceSha256": aid,
                    "groupId": entry["groupId"],
                    "goal": entry["goal"], "subsport": entry["subsport"],
                    "startMs": start, "endMs": end,
                })
                source_groups.add(entry["groupId"])
    if len(source_groups) < 3:
        raise ValueError("Not enough usable windows across three independent outings")
    if len(output) > 40000:
        raise ValueError("Planned dataset exceeds bounded training budget")
    # Exact format accepted by export_cached_training_dataset.
    return {"version": SELECTION_VERSION, "selections": output}

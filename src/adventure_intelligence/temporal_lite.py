"""Training-free, cache-only visual temporal graph for action-camera footage.

This is a *visual change* model, not a detector of actions, animals, skill,
intentions, narrative payoff, or verified event boundaries. tMs is always the
requested video-relative timestamp; decodedPtsMs is retained as separate
source-frame evidence and must never silently replace tMs.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

from .catalog import connect
from .scene_atlas import frames_for_asset, similarity, unit_vector

VERSION = "klip-temporal-lite-v1"
MAX_FRAMES = 4096
LIMITATIONS = [
    "Visual embedding changes are unverified hypotheses, not action or animal labels.",
    "Large or unsampled intervals are unknown, never evidence of an empty scene.",
    "Model cosine distance and change thresholds are not calibrated probabilities.",
    "Requested video-relative milliseconds are not decoded source PTS or exact cut frames.",
    "No video has been decoded; this report only reads completed checksum-verified caches.",
]


def _positive_int(value, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _unknown_intervals(segments: list[dict], duration_ms: int) -> list[dict]:
    gaps = []
    cursor = 0
    for node in segments:
        if node["startMs"] > cursor:
            gaps.append({"startMs": cursor, "endMs": node["startMs"],
                         "reason": "no_supported_visual_observation"})
        cursor = node["endMs"]
    if cursor < duration_ms:
        gaps.append({"startMs": cursor, "endMs": duration_ms,
                     "reason": "no_supported_visual_observation"})
    return gaps


def build_temporal_lite(
    frames: list[dict],
    *,
    duration_ms: int,
    model_key: str | None,
    change_threshold: float = 0.20,
    max_gap_ms: int = 7000,
    support_radius_ms: int = 3500,
) -> dict:
    """Produce bounded observation nodes and visual-change hypotheses.

    Works only with one frozen semantic model. Disjoint observations never
    become continuous motion merely because their embeddings look similar.
    No source-video file, database, model weights, or network is accessed here.
    """
    _positive_int(duration_ms, "duration_ms")
    _positive_int(max_gap_ms, "max_gap_ms")
    _positive_int(support_radius_ms, "support_radius_ms")
    if (not isinstance(change_threshold, (int, float))
            or isinstance(change_threshold, bool)
            or not math.isfinite(change_threshold)
            or not 0 < change_threshold <= 2):
        raise ValueError("change_threshold must be finite and in (0, 2]")
    if not isinstance(frames, list) or len(frames) > MAX_FRAMES:
        raise ValueError("frames must be a bounded list of at most 4096 samples")

    def report(nodes: list[dict], transitions: list[dict], edges: list[dict],
               *, status: str, reason: str | None, samples: int,
               rejected: int) -> dict:
        unknown = _unknown_intervals(nodes, duration_ms)
        unknown_ms = sum(x["endMs"] - x["startMs"] for x in unknown)
        return {
            "version": VERSION,
            "status": status,
            "abstentionReason": reason,
            "durationMs": duration_ms,
            "modelKey": model_key,
            "timestampBasis": "requested_video_relative_ms",
            "decodedPtsNormalization": "not_assumed",
            "sampling": {"usable": samples, "rejected": rejected,
                         "maxGapMs": max_gap_ms,
                         "supportRadiusMs": support_radius_ms},
            "policy": {"changeThreshold": float(change_threshold),
                       "scoring": "adjacent_cosine_distance",
                       "thresholdCalibrated": False},
            "segments": nodes,
            "visualTransitions": transitions,
            "edges": edges,
            "unknownIntervals": unknown,
            "coverage": {
                "observedSupportMs": duration_ms - unknown_ms,
                "unknownMs": unknown_ms,
                "observedSupportFraction": round(1 - unknown_ms / duration_ms, 6),
            },
            "sourceVideoRead": False,
            "inferencePerformed": False,
            "humanApprovalRequired": True,
            "limitations": LIMITATIONS,
        }

    if not isinstance(model_key, str) or not model_key.strip():
        return report([], [], [], status="abstained", reason="no_frozen_model_provenance",
                      samples=0, rejected=len(frames))

    valid = []
    rejected = 0
    dimensions = set()
    for row in frames:
        if not isinstance(row, dict):
            rejected += 1
            continue
        t = row.get("tMs")
        vector = unit_vector(row.get("embedding"))
        if type(t) is not int or not 0 <= t < duration_ms or vector is None:
            # Deliberately never substitute decodedPtsMs for missing tMs.
            rejected += 1
            continue
        dimensions.add(len(vector))
        pts = row.get("decodedPtsMs")
        valid.append({"t": t, "v": vector,
                      "pts": pts if type(pts) is int and pts >= 0 else None})

    if len(dimensions) > 1:
        return report([], [], [], status="abstained", reason="mixed_embedding_dimensions",
                      samples=0, rejected=len(frames))
    valid.sort(key=lambda row: row["t"])
    if any(a["t"] == b["t"] for a, b in zip(valid, valid[1:])):
        return report([], [], [], status="abstained", reason="duplicate_sample_timestamps",
                      samples=0, rejected=len(frames))
    if not valid:
        return report([], [], [], status="abstained", reason="no_valid_embedded_samples",
                      samples=0, rejected=rejected)

    transitions = []
    boundaries = set()
    for index in range(1, len(valid)):
        before, after = valid[index - 1], valid[index]
        gap = after["t"] - before["t"]
        if gap > max_gap_ms:
            boundaries.add(index)
            continue
        distance = 1 - similarity(before["v"], after["v"])
        if distance >= change_threshold:
            boundaries.add(index)
            transitions.append({
                "id": f"visual-change-{len(transitions) + 1:04d}",
                "leftSampleMs": before["t"],
                "rightSampleMs": after["t"],
                "candidateBoundaryMs": (before["t"] + after["t"]) // 2,
                "cosineDistance": round(distance, 6),
                "evidenceGapMs": gap,
                "kind": "visual_change_candidate",
                "status": "unverified",
                "actionIdentity": None,
                "confidence": None,
            })

    nodes = []
    for index, current in enumerate(valid):
        t = current["t"]
        start = max(0, t - support_radius_ms)
        end = min(duration_ms, t + support_radius_ms + 1)
        if index > 0 and t - valid[index - 1]["t"] <= max_gap_ms:
            start = max(start, (valid[index - 1]["t"] + t + 1) // 2)
        if index + 1 < len(valid) and valid[index + 1]["t"] - t <= max_gap_ms:
            end = min(end, (t + valid[index + 1]["t"] + 1) // 2)
        if end <= start:
            continue
        # Merge only truly contiguous observed support, and never across a
        # detected change or a sampling gap, irrespective of similar imagery.
        if (nodes and index not in boundaries
                and nodes[-1]["endMs"] == start):
            node = nodes[-1]
            node["endMs"] = end
            node["sampleTimesMs"].append(t)
            node["decodedPtsEvidenceMs"].append(current["pts"])
            node["sampleCount"] += 1
            node["status"] = "multi_sample_visual_hypothesis"
        else:
            nodes.append({
                "id": f"tseg-{len(nodes) + 1:04d}",
                "startMs": start,
                "endMs": end,
                "sampleTimesMs": [t],
                "decodedPtsEvidenceMs": [current["pts"]],
                "sampleCount": 1,
                "status": "single_sample_navigation_hint",
                "semanticLabel": None,
                "verifiedEvent": False,
            })

    edges = []
    for left, right in zip(nodes, nodes[1:]):
        edges.append({
            "from": left["id"], "to": right["id"],
            "relation": "chronologically_precedes",
            "gapMs": right["startMs"] - left["endMs"],
            "continuityVerified": False,
        })
    return report(nodes, transitions, edges,
                  status=("available" if len(valid) >= 2 else "insufficient_temporal_evidence"),
                  reason=(None if len(valid) >= 2 else "only_one_sample"),
                  samples=len(valid), rejected=rejected)


def build_cached_temporal_lite(
    workspace: Path, asset_id: str, *, provider: str = "siglip2",
    change_threshold: float = 0.20, max_gap_ms: int = 7000,
    support_radius_ms: int = 3500,
) -> dict:
    """Read cached embeddings and catalog duration; never resolve/open original."""
    if not isinstance(asset_id, str) or not re.fullmatch(r"[0-9a-f]{64}", asset_id):
        raise ValueError("Full lowercase SHA-256 asset ID required")
    if provider != "siglip2":
        raise ValueError("Only the frozen siglip2 provider is qualified in v1")
    with connect(Path(workspace)) as db:
        asset = db.execute("SELECT duration FROM assets WHERE id=?", (asset_id,)).fetchone()
        if asset is None or not isinstance(asset["duration"], (int, float)):
            raise ValueError("Indexed asset with known duration required")
        duration_ms = round(asset["duration"] * 1000)
        frames, model_key = frames_for_asset(db, workspace, asset_id, provider)
    output = build_temporal_lite(
        frames, duration_ms=duration_ms, model_key=model_key,
        change_threshold=change_threshold, max_gap_ms=max_gap_ms,
        support_radius_ms=support_radius_ms)
    output["assetId"] = asset_id
    output["provider"] = provider
    return output

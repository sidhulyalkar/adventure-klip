"""Read-only temporal observability audit of verified K-TIDE feature shards.

This is a source-free *measurement of feature timestamps*, not a detector
of skiing turns, wildlife, physical dynamics, or event importance. No
video decode, model inference, edit, network, or workspace write occurs.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from statistics import median

from .encoder_dataset import load_manifest, safe_training_workspace
from .ktide_train import _read_shard
from .encoder_dataset import _safe_file

VERSION = "klip-ktide-temporal-observability-v1"


def _segments(timestamps: list[int], max_gap_ms: int) -> tuple[list[int], list[int]]:
    if not timestamps:
        raise ValueError("Empty temporal sample")
    gaps = [b - a for a, b in zip(timestamps, timestamps[1:])]
    if any(gap <= 0 for gap in gaps):
        raise ValueError("Timestamps must be strictly increasing")
    lengths = [1]
    for gap in gaps:
        if gap <= max_gap_ms:
            lengths[-1] += 1
        else:
            lengths.append(1)
    return gaps, lengths


def temporal_observability(manifest_path: Path, *, workspace: Path,
                           max_gap_ms: int = 7000) -> dict:
    """Audit timestamp coverage without reading images or original videos."""
    if type(max_gap_ms) is not int or not 250 <= max_gap_ms <= 60000:
        raise ValueError("max_gap_ms outside the K-TIDE model's supported range")
    root = safe_training_workspace(workspace)
    reports = root / "reports"
    if reports.is_symlink() or not reports.is_dir():
        raise ValueError("Workspace reports directory unavailable")
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("Missing or symlinked K-TIDE manifest")
    path = manifest_path.resolve(strict=True)
    if (not path.parent.is_relative_to(reports)
            or path.parent == reports
            or path.name != "manifest.json"
            or any(p.is_symlink() for p in manifest_path.parents if
                   p.is_relative_to(reports))):
        raise ValueError("Manifest must be inside a private reports dataset directory")
    doc, digest = load_manifest(path)
    import numpy as np
    windows = []
    stats = Counter()
    gaps_all = []
    pts_delta_residuals = []
    groups = {}
    for row in doc["samples"]:
        # Validates the source-free shard digest, numeric types, data shapes,
        # strict increasing requested times, and exact window boundaries.
        _, t = _read_shard(path.parent, row, doc["embeddingDim"])
        shard = _safe_file(path.parent, row["shard"])
        with np.load(shard, allow_pickle=False) as z:
            pts = z["decoded_pts_ms"].copy()
        times = [int(v) for v in t]
        gaps, segment_lengths = _segments(times, max_gap_ms)
        connected = sum(0 < gap <= max_gap_ms for gap in gaps)
        isolated = sum(length == 1 for length in segment_lengths)
        known_pts = 0
        valid_pts_adjacent = 0
        bad_pts_order = 0
        for point in pts:
            known_pts += int(point >= 0)
        for i in range(1, len(pts)):
            if pts[i] >= 0 and pts[i-1] >= 0:
                valid_pts_adjacent += 1
                p_gap = int(pts[i] - pts[i-1])
                if p_gap <= 0:
                    bad_pts_order += 1
                # Compare *intervals*, not absolute timestamps, since
                # FFmpeg PTS may use a nonzero source origin.
                pts_delta_residuals.append(abs(p_gap - gaps[i-1]))
        longest = max(segment_lengths)
        eligible = longest >= 3
        summary = {
            "sampleId": row["sampleId"],
            "sourceSha256": row["sourceSha256"],
            "groupId": row["groupId"],
            "subsportMetadataNotGroundTruth": row["subsport"],
            "observedFrames": len(times),
            "firstRequestedMs": times[0],
            "lastRequestedMs": times[-1],
            "adjacentPairs": len(gaps),
            "attentionConnectedPairs": connected,
            "isolatedFrames": isolated,
            "attentionSegments": len(segment_lengths),
            "longestConnectedRunFrames": longest,
            "eligibleForThreeFrameLocalOrder": eligible,
            "decodedPtsAvailableFrames": known_pts,
            "adjacentPairsWithDecodedPts": valid_pts_adjacent,
            "nonIncreasingDecodedPtsPairs": bad_pts_order,
        }
        stats["windows"] += 1
        stats["observedFrames"] += len(times)
        stats["adjacentPairs"] += len(gaps)
        stats["connectedPairs"] += connected
        stats["isolatedFrames"] += isolated
        stats["eligibleOrderWindows"] += int(eligible)
        stats["ptsKnownFrames"] += known_pts
        stats["ptsAdjacentPairs"] += valid_pts_adjacent
        stats["nonIncreasingPtsPairs"] += bad_pts_order
        gaps_all.extend(gaps)
        groups.setdefault(row["groupId"], Counter()).update({
            "windows": 1,
            "connectedPairs": connected,
            "adjacentPairs": len(gaps),
            "eligibleOrderWindows": int(eligible),
        })
        windows.append(summary)
    pairs = stats["adjacentPairs"]
    total_frames = stats["observedFrames"]
    connected_fraction = (stats["connectedPairs"] / pairs) if pairs else 0.0
    return {
        "version": VERSION,
        "datasetManifestSha256": digest,
        "frozenTeacherModelKey": doc["featureModelKey"],
        "maxGapMs": max_gap_ms,
        "sampleWindows": stats["windows"],
        "sources": len({w["sourceSha256"] for w in windows}),
        "declaredGroups": len(groups),
        "provisionalGroupsPresent": any(g.startswith("provisional_") for g in groups),
        "totalObservedFrames": total_frames,
        "adjacentPairs": pairs,
        "attentionConnectedPairs": stats["connectedPairs"],
        "attentionConnectedPairFraction": round(connected_fraction, 6),
        "windowsWithThreeFrameLocalOrder": stats["eligibleOrderWindows"],
        "windowsWithoutThreeFrameLocalOrder": (
            stats["windows"] - stats["eligibleOrderWindows"]),
        "isolatedFrames": stats["isolatedFrames"],
        "medianAdjacentGapMs": float(median(gaps_all)) if gaps_all else None,
        "decodedPtsCoverage": round(stats["ptsKnownFrames"] / total_frames, 6),
        "adjacentPairsWithDecodedPts": stats["ptsAdjacentPairs"],
        "nonIncreasingDecodedPtsPairs": stats["nonIncreasingPtsPairs"],
        "medianAbsoluteRequestedVsDecodedGapDifferenceMs": (
            float(median(pts_delta_residuals)) if pts_delta_residuals else None),
        "absolutePTSOriginComparable": False,
        "temporalLearningSupportedByEveryWindow": (
            stats["eligibleOrderWindows"] == stats["windows"]),
        "genuineActionRecognitionDemonstrated": False,
        "independentTripsVerified": False,
        "riskFlags": [
            warning for condition, warning in (
                (connected_fraction < 0.5, "most_adjacent_frames_exceed_attention_gap"),
                (stats["eligibleOrderWindows"] < stats["windows"],
                 "some_windows_have_no_three_frame_contiguous_order"),
                (stats["nonIncreasingPtsPairs"] > 0,
                 "decoded_pts_not_monotonic_in_some_windows"),
                (stats["ptsKnownFrames"] < total_frames,
                 "decoded_pts_missing_for_some_observations"),
                (any(g.startswith("provisional_") for g in groups),
                 "capture_period_groups_not_verified_outings"),
            ) if condition
        ],
        "byDeclaredGroup": {
            group: {
                "windows": counts["windows"],
                "adjacentPairs": counts["adjacentPairs"],
                "connectedPairs": counts["connectedPairs"],
                "orderEligibleWindows": counts["eligibleOrderWindows"],
            } for group, counts in sorted(groups.items())
        },
        "windows": windows,
        "originalVideoRead": False,
        "modelInferencePerformed": False,
        "trainingPerformed": False,
        "workspaceModified": False,
    }

"""Conservative, deterministic temporal box-association hypotheses.

Track IDs represent *candidate visual continuity*, never animal identity.
Require same label/coordinate space, bounded elapsed time, mutual top IoU and
an unambiguous margin over alternative assignments.
"""
from __future__ import annotations

import math
from typing import Any


def _bbox(obj: dict) -> tuple[float, float, float, float] | None:
    box = obj.get("bbox")
    if not isinstance(box, dict):
        return None
    if obj.get("bboxFormat", "xyxy") != "xyxy":
        return None
    keys = (("xmin", "ymin", "xmax", "ymax"),
            ("x_min", "y_min", "x_max", "y_max"),
            ("left", "top", "right", "bottom"))
    for names in keys:
        if all(k in box for k in names):
            vals = tuple(box[k] for k in names)
            if (all(isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v) for v in vals)
                    and vals[2] > vals[0] and vals[3] > vals[1]):
                return vals
    return None


def _iou(a, b) -> float:
    left = max(a[0], b[0])
    top = max(a[1], b[1])
    width = max(0.0, min(a[2], b[2]) - left)
    height = max(0.0, min(a[3], b[3]) - top)
    intersection = width * height
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return intersection / (area_a + area_b - intersection) if intersection else 0.0


def associate_boxes(frames: list[dict], *, minimum_iou: float = 0.3,
                    maximum_gap_ms: int = 12000,
                    ambiguity_margin: float = 0.08) -> dict:
    """Annotate frame objects with provisional track IDs; return track summaries.

    The algorithm intentionally favors false negatives over identity switches.
    Each invocation is deterministic for identical timestamp-ordered input.
    """
    if not 0 < minimum_iou <= 1 or maximum_gap_ms <= 0 or ambiguity_margin < 0:
        raise ValueError("Invalid tracker thresholds")
    tracks: dict[str, dict[str, Any]] = {}
    count = 0

    # Input may include parallel descriptions at the same time; sorting makes
    # the ordering reproducible without modifying the caller's frame list.
    for frame in sorted(frames, key=lambda f: (f.get("tMs", 0), f.get("requestedMs", f.get("tMs", 0)))):
        t = frame.get("decodedPtsMs")
        if not isinstance(t, int) or t < 0:
            t = frame.get("tMs")
        if not isinstance(t, int) or t < 0:
            continue
        objects = frame.get("objects", [])
        detections = []
        for index, obj in enumerate(objects):
            if not isinstance(obj, dict) or not isinstance(obj.get("label"), str):
                continue
            box = _bbox(obj)
            if box is not None:
                detections.append((index, obj, box))

        candidates: list[tuple[float, int, str]] = []
        for index, obj, box in detections:
            for tid, track in tracks.items():
                if (track["label"] != obj["label"]
                    or track["coordinateSpace"] != obj.get("coordinateSpace", "unknown")
                    or not 0 < t - track["lastMs"] <= maximum_gap_ms):
                    continue
                similarity = _iou(box, track["box"])
                if similarity >= minimum_iou:
                    candidates.append((similarity, index, tid))

        # Mutual best matching. If either side has a near-tie, create a new
        # track instead; this is safer for overlapping or crossing animals.
        by_det: dict[int, list[tuple[float, str]]] = {}
        by_track: dict[str, list[tuple[float, int]]] = {}
        for score, index, tid in candidates:
            by_det.setdefault(index, []).append((score, tid))
            by_track.setdefault(tid, []).append((score, index))
        proposed: dict[int, str] = {}
        for index, options in by_det.items():
            options.sort(reverse=True)
            score, tid = options[0]
            rivals = sorted(by_track[tid], reverse=True)
            if (rivals[0][1] == index
                and (len(options) == 1 or score - options[1][0] > ambiguity_margin)
                and (len(rivals) == 1 or score - rivals[1][0] > ambiguity_margin)):
                proposed[index] = tid

        for index, obj, box in detections:
            tid = proposed.get(index)
            if tid is None:
                count += 1
                tid = f"track-{count:06d}"
                tracks[tid] = {
                    "id": tid, "label": obj["label"],
                    "coordinateSpace": obj.get("coordinateSpace", "unknown"),
                    "firstMs": t, "lastMs": t, "box": box,
                    "detectionCount": 0,
                }
            track = tracks[tid]
            track["box"] = box
            track["lastMs"] = t
            track["detectionCount"] += 1
            obj["trackId"] = tid
            obj["trackHypothesis"] = True
            obj["reviewed"] = False

    summaries = [{
        "id": track["id"], "label": track["label"],
        "coordinateSpace": track["coordinateSpace"],
        "startMs": track["firstMs"], "endMs": track["lastMs"],
        "detectionCount": track["detectionCount"],
        "reviewed": False,
    } for track in tracks.values()]
    return {
        "schemaVersion": 1,
        "method": "same_label_mutual_iou_unverified",
        "minimumIoU": minimum_iou,
        "maximumGapMs": maximum_gap_ms,
        "tracks": summaries,
        "identityVerified": False,
    }

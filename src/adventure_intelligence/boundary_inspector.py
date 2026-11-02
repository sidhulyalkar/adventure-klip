"""Read-only, bounded boundary evidence from cached local motion analysis.

A candidate cut is not a verified action boundary. These observations help a
reviewer navigate a clip but never edit or decode the source recording.
"""
from __future__ import annotations

import math
import re

from .catalog import connect
from .editorial_quality import raw_motion_for_asset

VERSION = "boundary-inspector-v1"
_SHA = re.compile(r"^[a-f0-9]{64}$")
MAX_WINDOW_MS = 180_000
MAX_BINS = 48
CONTEXT_MS = 5_000


def _valid_int(value, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer in milliseconds")
    return value


def summarize_motion(series: list[dict], *, start_ms: int, end_ms: int,
                     duration_ms: int, bins: int = MAX_BINS) -> dict:
    """Summarize only the requested window; no extrapolation over missing data."""
    start_ms = _valid_int(start_ms, "startMs")
    end_ms = _valid_int(end_ms, "endMs")
    duration_ms = _valid_int(duration_ms, "durationMs")
    if (duration_ms <= 0 or start_ms < 0 or start_ms >= end_ms or
            end_ms > duration_ms or end_ms - start_ms > MAX_WINDOW_MS):
        raise ValueError("Invalid or excessively long boundary window")
    if not isinstance(bins, int) or isinstance(bins, bool) or not 8 <= bins <= MAX_BINS:
        raise ValueError("Invalid evidence bin count")
    left = max(0, start_ms - CONTEXT_MS)
    right = min(duration_ms, end_ms + CONTEXT_MS)
    span = right - left
    values: list[list[float]] = [[] for _ in range(bins)]
    for sample in series:
        t = sample.get("t_ms")
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t):
            continue
        if not left <= t < right:
            continue
        # Pixel change and optical-flow magnitude are camera/image observations.
        # Never combine incomparable signals in the same request.
        reading = sample.get("change")
        if reading is None:
            reading = sample.get("flow_magnitude")
        if (isinstance(reading, bool) or not isinstance(reading, (int, float))
                or not math.isfinite(reading) or reading < 0):
            continue
        ix = min(bins-1, int((t-left)*bins/span))
        values[ix].append(float(reading))
    means = [sum(v)/len(v) if v else None for v in values]
    observed = sorted(v for v in means if v is not None)
    # Local amplitude only; per-video scaling cannot be compared across days.
    scale = observed[min(len(observed)-1, int(.90*(len(observed)-1)))] if observed else 0.0
    scale = max(scale, 1e-9)
    result = []
    for i, mean in enumerate(means):
        a = left + span*i//bins
        b = left + span*(i+1)//bins
        result.append({
            "startMs": a, "endMs": b,
            "samples": len(values[i]),
            "level": round(min(1.0, mean/scale),4) if mean is not None else None,
        })
    # A low-motion bin is merely a possible navigation marker.
    # Do not suggest anything if evidence is patchy or sparse.
    coverage = sum(v is not None for v in means) / bins
    enough = sum(len(v) for v in values) >= 16 and coverage >= .65
    hints = []
    if enough:
        for name, boundary in (("in", start_ms), ("out", end_ms)):
            nearby = []
            for i in range(1, bins-1):
                if means[i] is None or means[i-1] is None or means[i+1] is None:
                    continue
                midpoint = (result[i]["startMs"]+result[i]["endMs"])//2
                if abs(midpoint-boundary) > 2500:
                    continue
                level = result[i]["level"]
                if (level <= .55 and level <= result[i-1]["level"] and
                        level <= result[i+1]["level"]):
                    nearby.append((abs(midpoint-boundary), level, midpoint))
            if nearby:
                _, level, at = min(nearby)
                hints.append({"edge":name, "atMs":at, "level":level,
                              "description":"Nearby low image-motion sample; manually inspect"})
    return {
        "version": VERSION, "windowStartMs": left, "windowEndMs": right,
        "coreStartMs": start_ms, "coreEndMs": end_ms, "bins": result,
        "sampleCount": sum(len(v) for v in values),
        "coverageFraction": round(coverage, 3),
        "status": "observed" if enough else "insufficient_evidence",
        "hints": hints if enough else [],
        "source":"cached_motion_or_flow",
        "caveat":"Image/camera movement, not verified action boundaries or frame-exact timestamps.",
        "requiresReview":True,
    }


def boundary_evidence(workspace, asset_id: str, start_ms: int, end_ms: int) -> dict:
    if not isinstance(asset_id, str) or not _SHA.fullmatch(asset_id):
        raise ValueError("Invalid asset ID")
    with connect(workspace) as db:
        asset = db.execute("SELECT duration FROM assets WHERE id=?", (asset_id,)).fetchone()
        if asset is None:
            raise KeyError("Recording not found")
        duration = round(float(asset["duration"] or 0)*1000)
        if duration <= 0:
            raise ValueError("Recording duration unavailable")
        # Validate before reading potentially large cached artifacts.
        if (isinstance(start_ms,bool) or isinstance(end_ms,bool) or
                not isinstance(start_ms,int) or not isinstance(end_ms,int) or
                start_ms < 0 or end_ms > duration or end_ms <= start_ms or
                end_ms-start_ms > MAX_WINDOW_MS):
            raise ValueError("Invalid or excessively long boundary window")
        series = raw_motion_for_asset(db, workspace, asset_id)
    doc = summarize_motion(series, start_ms=start_ms, end_ms=end_ms, duration_ms=duration)
    doc["assetId"] = asset_id
    return doc

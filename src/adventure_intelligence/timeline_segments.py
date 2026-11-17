"""Variable-length scene segments over a complete source timeline.

Replaces "a fixed number of 20 s windows per recording" as the unit of
discovery. A dense change signal is cut at its strong change points, the
resulting segments tile the whole timeline with no holes, and the review
budget scales with duration. Segments are hypotheses about where one scene
ends and the next begins. They say nothing about quality or meaning.
"""
from __future__ import annotations

import math
from statistics import median

from .scene_atlas import unit_vector

VERSION = "klip-timeline-segments-v1"


def change_from_motion(series: list[dict]) -> list[tuple[int, float]]:
    """(t_ms, change) from media.motion_series rows; malformed rows are dropped."""
    rows = [(r["t_ms"], float(r["change"])) for r in series
            if isinstance(r, dict) and type(r.get("t_ms")) is int and r["t_ms"] >= 0
            and type(r.get("change")) in (int, float) and math.isfinite(r["change"])]
    return sorted(set(rows))


def _frames(frames: list[dict]) -> list[tuple[int, list[float]]]:
    out, seen = [], set()
    for f in frames:
        t = f.get("decodedPtsMs")
        if type(t) is not int or t < 0:
            t = f.get("tMs")
        v = unit_vector(f.get("embedding"))
        if type(t) is int and t >= 0 and t not in seen and v is not None:
            seen.add(t)
            out.append((t, v))
    out.sort(key=lambda p: p[0])
    if len({len(v) for _, v in out}) > 1:
        raise ValueError("Mixed embedding dimensions in one timeline")
    return out


def change_from_embeddings(frames: list[dict], *, context: int = 3) -> list[tuple[int, float]]:
    """Cosine distance between the mean embedding just before and just after each frame.

    Comparing short contexts instead of adjacent frames means a one-frame
    disturbance scores low and a lasting change of scene scores high.
    """
    rows = _frames(frames)
    out = []
    for i in range(1, len(rows)):
        before = _mean([v for _, v in rows[max(0, i - context):i]])
        after = _mean([v for _, v in rows[i:i + context]])
        if before is not None and after is not None:
            out.append((rows[i][0], 1.0 - sum(x * y for x, y in zip(before, after))))
    return out


def _peaks(change, sensitivity):
    values = [v for _, v in change]
    centre = median(values)
    spread = median(abs(v - centre) for v in values)
    threshold = centre + sensitivity * spread
    peaks = [(v, t) for (_, a), (t, v), (_, b) in zip(change, change[1:], change[2:])
             if v > threshold and v >= a and v >= b]
    return peaks, spread


def split_peaks(change: list[tuple[int, float]], *, min_segment_ms: int = 4000,
                sensitivity: float = 3.0, spikes_are_events: bool = True,
                context_ms: int = 8000, guard_ms: int = 1500,
                shift_ratio: float = 0.5) -> tuple[list[int], list[int]]:
    """Separate scene boundaries from events inside a scene.

    With a motion signal (spikes_are_events=True) a boundary is a lasting
    shift in level: the median just after differs from the median just before
    by at least shift_ratio of the larger one. Medians ignore short spikes, so
    a spike is an event within its scene (a jump, an impact) and a run with
    four jumps stays one scene. With spikes_are_events=False every strong
    peak is a boundary. Returns (boundary times, event times).
    """
    if (type(min_segment_ms) is not int or min_segment_ms < 1 or not sensitivity >= 0
            or not 0 < shift_ratio < 1):
        raise ValueError("Invalid segmentation parameters")
    if len(change) < 3:
        return [], []
    peaks, spread = _peaks(change, sensitivity)
    events: list[int] = []
    if spikes_are_events:
        shifts, local = [], {}
        for t, _ in change:
            before = [v for u, v in change if t - context_ms <= u < t]
            after = [v for u, v in change if t <= u < t + context_ms]
            if not before or not after:
                continue
            lo, hi = sorted((median(before), median(after)))
            local[t] = hi
            if hi - lo > sensitivity * spread and hi - lo >= shift_ratio * hi:
                # Medians decide whether a shift is real; they are flat across
                # the transition, so means place it at the step itself.
                step = abs(sum(after) / len(after) - sum(before) / len(before))
                shifts.append((step, t))
        for value, t in sorted(peaks, key=lambda p: (-p[0], p[1])):
            if (value >= 2 * local.get(t, 0.0)
                    and all(abs(t - other) >= guard_ms for other in events)):
                events.append(t)
        peaks = shifts
        # One transition passes the test for half a context either side.
        min_segment_ms = max(min_segment_ms, context_ms // 2 + 1)
    cuts: list[int] = []
    for _, t in sorted(peaks, key=lambda p: (-p[0], p[1])):  # strongest first
        if all(abs(t - other) >= min_segment_ms for other in cuts):
            cuts.append(t)
    return sorted(cuts), sorted(events)


def segment(change: list[tuple[int, float]], duration_ms: int, *,
            frames: list[dict] | None = None, min_segment_ms: int = 4000,
            sensitivity: float = 3.0, spikes_are_events: bool = True) -> list[dict]:
    """Tile [0, duration_ms] with segments. There is no maximum length.

    Pass spikes_are_events=False for a change_from_embeddings signal, whose
    peaks already mean a lasting change.
    """
    if type(duration_ms) is not int or duration_ms < 1:
        raise ValueError("Source duration must be a positive integer")
    found, events = split_peaks(change, min_segment_ms=min_segment_ms,
                                sensitivity=sensitivity,
                                spikes_are_events=spikes_are_events)
    cuts = [t for t in found if min_segment_ms <= t <= duration_ms - min_segment_ms]
    edges = [0, *cuts, duration_ms]
    vectors = _frames(frames) if frames else []
    whole = _mean([v for _, v in vectors])
    out = []
    for start, end in zip(edges, edges[1:]):
        inside = [v for t, v in change if start <= t < end]
        own = _mean([v for t, v in vectors if start <= t < end])
        out.append({
            "startMs": start, "endMs": end,
            "changeSamples": len(inside),
            # Unverified candidates for a jump, impact or entry. Not outcomes.
            "eventCandidatesMs": [t for t in events if start <= t < end],
            # Kept as separate evidence. Do not sum these into one "quality".
            "meanChange": round(sum(inside) / len(inside), 6) if inside else None,
            "peakChange": round(max(inside), 6) if inside else None,
            "distinctiveness": None if own is None or whole is None else
            round(1.0 - sum(x * y for x, y in zip(own, whole)), 6),
            "isSceneHypothesisOnly": True,
        })
    return out


def _mean(vectors: list[list[float]]) -> list[float] | None:
    if not vectors:
        return None
    return unit_vector([sum(col) / len(vectors) for col in zip(*vectors)])


def propose(segments: list[dict], *, budget_fraction: float = 0.25,
            controls: int = 2, rank_by: str = "peakChange") -> dict:
    """Pick whole segments for review until a duration-proportional budget is met.

    Unselected segments stay in the output, and a few are added as controls so
    the miss rate of the ranking can be measured later. A segment with no
    evidence is unknown, never a negative.
    """
    if not 0 < budget_fraction <= 1 or type(controls) is not int or controls < 0:
        raise ValueError("Invalid review budget")
    if rank_by not in ("peakChange", "meanChange", "distinctiveness"):
        raise ValueError("Unknown ranking signal")
    total = sum(s["endMs"] - s["startMs"] for s in segments)
    budget = budget_fraction * total
    order = sorted(range(len(segments)), key=lambda i: (
        segments[i][rank_by] is None, -(segments[i][rank_by] or 0), segments[i]["startMs"]))
    chosen, used = [], 0
    for i in order:
        if used >= budget:
            break
        chosen.append(i)
        used += segments[i]["endMs"] - segments[i]["startMs"]
    rest = sorted(set(range(len(segments))) - set(chosen))
    step = max(1, len(rest) // controls) if controls else 0
    control_ids = rest[step // 2::step][:controls] if controls else []
    roles = {**{i: "ranked_proposal" for i in chosen},
             **{i: "unranked_control" for i in control_ids}}
    return {
        "version": VERSION, "rankBy": rank_by,
        "segments": [{**s, "role": roles.get(i, "not_reviewed")}
                     for i, s in enumerate(segments)],
        "sourceDurationMs": total,
        "proposedMs": used,
        "proposedFraction": round(used / total, 4) if total else 0.0,
        "controlMs": sum(segments[i]["endMs"] - segments[i]["startMs"] for i in control_ids),
    }


def span_recall(intervals: list[tuple[int, int]], kept: list[tuple[int, int]],
                *, coverage: float = 0.8) -> dict:
    """Share of owner-kept spans mostly inside the proposed intervals.

    A kept span counts as recovered only when at least `coverage` of it is
    proposed, so half a run does not count. Also reports whether each span
    landed inside a single proposal, which is what an edit needs.
    """
    if not 0 < coverage <= 1:
        raise ValueError("coverage must be in (0, 1]")
    merged: list[list[int]] = []
    for a, b in sorted(intervals):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    recovered = intact = 0
    for a, b in kept:
        shared = sum(max(0, min(b, y) - max(a, x)) for x, y in merged)
        if shared >= coverage * (b - a):
            recovered += 1
        if any(max(0, min(b, y) - max(a, x)) >= coverage * (b - a) for x, y in intervals):
            intact += 1
    n = len(kept)
    return {
        "keptSpans": n, "recovered": recovered, "intactInOneProposal": intact,
        "recall": round(recovered / n, 4) if n else None,
        "intactRate": round(intact / n, 4) if n else None,
        "proposedMs": sum(b - a for a, b in merged),
    }


def compare_to_fixed_windows(series: list[dict], duration_ms: int,
                             kept: list[tuple[int, int]], *,
                             budget_fraction: float | None = None) -> dict:
    """Same motion series, same kept spans: 16 fixed windows vs. segments.

    By default the segmenter gets exactly the footage budget the fixed windows
    used, so a difference in recall is not just a difference in minutes shown.
    """
    from .full_archive_campaign import _proposals
    fixed = [(w["startMs"], w["endMs"]) for w in _proposals(series, duration_ms)]
    baseline = span_recall(fixed, kept)
    fraction = budget_fraction or min(1.0, max(0.01, baseline["proposedMs"] / duration_ms))
    plan = propose(segment(change_from_motion(series), duration_ms),
                   budget_fraction=fraction, controls=0)
    picked = [(s["startMs"], s["endMs"]) for s in plan["segments"]
              if s["role"] == "ranked_proposal"]
    return {"version": VERSION, "sourceDurationMs": duration_ms,
            "budgetFraction": round(fraction, 4),
            "fixedWindows": baseline, "segments": span_recall(picked, kept),
            "syntheticOrRealMustBeStatedByCaller": True}

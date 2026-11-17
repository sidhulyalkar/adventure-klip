"""Timeline segmentation on synthetic signals only. No media is read."""
from __future__ import annotations

import pytest

from adventure_intelligence import timeline_segments as ts


def series(duration_ms, bursts, base=0.01, peak=0.4, fps=2):
    """Flat motion with short bursts at the given times (ms)."""
    step = 1000 // fps
    return [{"t_ms": t, "change": peak if any(abs(t - b) <= step for b in bursts) else base}
            for t in range(step, duration_ms, step)]


def frames(duration_ms, switch_ms, step=4000):
    return [{"tMs": t, "embedding": [1.0, 0.0] if t < switch_ms else [0.0, 1.0]}
            for t in range(0, duration_ms, step)]


def test_segments_tile_the_whole_timeline_without_holes_or_length_cap():
    duration = 600_000
    out = ts.segment(ts.change_from_motion(series(duration, [100_000, 400_000])), duration)
    assert out[0]["startMs"] == 0 and out[-1]["endMs"] == duration
    assert all(a["endMs"] == b["startMs"] for a, b in zip(out, out[1:]))
    assert max(s["endMs"] - s["startMs"] for s in out) > 20_000


def test_steady_chop_does_not_fragment_but_a_lasting_shift_splits():
    duration = 120_000
    chop = lambda t: 0.2 + 0.01 * ((t // 500) % 3)
    noisy = [{"t_ms": t, "change": chop(t)} for t in range(500, duration, 500)]
    assert len(ts.segment(ts.change_from_motion(noisy), duration)) == 1
    stop = [{"t_ms": t, "change": chop(t) if t < 60_000 else 0.01}
            for t in range(500, duration, 500)]
    out = ts.segment(ts.change_from_motion(stop), duration)
    assert [(s["startMs"], s["endMs"]) for s in out] == [(0, 60_000), (60_000, duration)]


def test_boundaries_respect_minimum_segment_length():
    change = ts.change_from_motion(series(60_000, [20_000, 21_500, 40_000]))
    cuts, _ = ts.split_peaks(change, min_segment_ms=4000, spikes_are_events=False)
    assert len(cuts) == 2 and all(b - a >= 4000 for a, b in zip(cuts, cuts[1:]))


def test_embedding_change_marks_a_scene_switch_and_scores_distinctiveness():
    f = frames(120_000, 80_000)
    change = ts.change_from_embeddings(f)
    assert max(change, key=lambda p: p[1])[0] == 80_000
    out = ts.segment(change, 120_000, frames=f, sensitivity=0.0, spikes_are_events=False)
    by_start = {s["startMs"]: s for s in out}
    assert by_start[80_000]["distinctiveness"] > by_start[0]["distinctiveness"]


def test_mixed_embedding_dimensions_are_rejected():
    with pytest.raises(ValueError):
        ts.change_from_embeddings([{"tMs": 0, "embedding": [1.0, 0.0]},
                                   {"tMs": 4000, "embedding": [1.0, 0.0, 0.0]}])


def test_budget_scales_with_duration_and_keeps_controls_and_unknowns():
    duration = 1_800_000  # 30 minutes of alternating riding and standing
    rows = [{"t_ms": t, "change": 0.2 if (t // 60_000) % 2 else 0.01}
            for t in range(500, duration, 500)]
    plan = ts.propose(ts.segment(ts.change_from_motion(rows), duration),
                      budget_fraction=0.25, controls=2)
    assert plan["proposedMs"] >= 0.25 * duration  # fixed windows cap at 320 s
    roles = [s["role"] for s in plan["segments"]]
    assert roles.count("unranked_control") == 2 and "not_reviewed" in roles
    assert len(plan["segments"]) == len(roles)  # nothing is dropped


def test_recall_requires_most_of_a_span_and_reports_intactness():
    kept = [(5_000, 39_000)]
    halves = [(0, 20_000), (20_000, 40_000)]
    assert ts.span_recall(halves, kept)["recall"] == 1.0
    assert ts.span_recall(halves, kept)["intactRate"] == 0.0  # split across two proposals
    assert ts.span_recall([(0, 20_000)], kept)["recall"] == 0.0
    assert ts.span_recall([(0, 40_000)], kept)["intactRate"] == 1.0
    assert ts.span_recall([], [])["recall"] is None


def park_run(duration=48_128, jumps=(11_846, 17_960, 23_714, 37_639)):
    """Standing, then riding with four jump spikes, then standing again."""
    rows = []
    for t in range(500, duration, 500):
        riding = 5_500 <= t < 39_500
        spike = any(abs(t - j) <= 500 for j in jumps)
        rows.append({"t_ms": t, "change": 0.6 if spike else 0.12 if riding else 0.01})
    return rows, duration


def test_jumps_are_events_inside_one_run_not_scene_boundaries():
    rows, duration = park_run()
    out = ts.segment(ts.change_from_motion(rows), duration)
    run = [s for s in out if len(s["eventCandidatesMs"]) == 4]
    assert len(run) == 1 and run[0]["startMs"] <= 6_000 and run[0]["endMs"] >= 39_000
    assert all(abs(a - b) <= 500 for a, b in
               zip(run[0]["eventCandidatesMs"], (11_846, 17_960, 23_714, 37_639)))


def test_synthetic_park_run_stays_intact_where_fixed_windows_cannot_hold_it():
    # Shaped like the reviewed run (owner line 5.5-39.3 s). The signal is
    # synthetic, so this checks mechanics and says nothing about real footage.
    rows, duration = park_run()
    report = ts.compare_to_fixed_windows(rows, duration, [(5_558, 39_270)],
                                         budget_fraction=0.75)
    assert report["segments"]["intactInOneProposal"] == 1
    assert report["fixedWindows"]["intactInOneProposal"] == 0


@pytest.mark.parametrize("kwargs", [dict(budget_fraction=0), dict(budget_fraction=1.5),
                                    dict(controls=-1), dict(rank_by="vibes")])
def test_invalid_budget_is_rejected(kwargs):
    with pytest.raises(ValueError):
        ts.propose([{"startMs": 0, "endMs": 10, "peakChange": 1.0}], **kwargs)

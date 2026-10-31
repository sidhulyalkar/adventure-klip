"""Boundary inspector unit tests: read-only, sparse-aware, source-relative."""
from __future__ import annotations

import math

import pytest

from adventure_intelligence.boundary_inspector import summarize_motion, VERSION


def series(start=0,stop=30000,step=100):
    return [{"t_ms":t,"change":(.05 if (6500<=t<=7500 or
              14500<=t<=15500) else .25)} for t in range(start,stop,step)]


def test_cached_motion_is_bounded_normalized_and_source_relative():
    out=summarize_motion(series(),start_ms=7000,end_ms=15000,duration_ms=30000)
    assert out["version"]==VERSION
    assert out["windowStartMs"]==2000 and out["windowEndMs"]==20000
    assert out["coreStartMs"]==7000 and out["coreEndMs"]==15000
    assert len(out["bins"])==48
    assert out["status"]=="observed"
    assert out["sampleCount"]>100
    assert out["coverageFraction"]==1
    assert all(b["level"] is None or 0<=b["level"]<=1 for b in out["bins"])
    assert out["requiresReview"] is True
    assert "not verified" in out["caveat"]


def test_empty_or_sparse_evidence_has_no_fabricated_levels_or_hints():
    empty=summarize_motion([],start_ms=5000,end_ms=15000,duration_ms=20000)
    assert empty["status"]=="insufficient_evidence"
    assert empty["hints"]==[]
    assert all(b["level"] is None and b["samples"]==0 for b in empty["bins"])
    sparse=summarize_motion([{"t_ms":6000,"change":1.0}],
                            start_ms=5000,end_ms=15000,duration_ms=20000)
    assert sparse["status"]=="insufficient_evidence"
    assert sparse["coverageFraction"]<.65
    assert sparse["hints"]==[]


def test_frame_timing_and_invalid_sensor_samples_are_not_interpolated():
    items=[
        {"t_ms":0,"change":0.1},
        {"t_ms":2000,"change":float("nan")},
        {"t_ms":2500,"change":-10},
        {"t_ms":4000,"change":0.25},
        {"t_ms":999999,"change":.3},
        {"t_ms":True,"change":.2},
        {"t_ms":6000,"flow_magnitude":.4},
    ]
    doc=summarize_motion(items,start_ms=1000,end_ms=5000,duration_ms=7000)
    assert doc["sampleCount"]==3
    assert any(b["samples"]==0 and b["level"] is None for b in doc["bins"])
    assert doc["status"]=="insufficient_evidence"


@pytest.mark.parametrize("start,end,duration",[
    (-1,1000,10000),(1000,1000,10000),(8000,11000,10000),
    (0,181000,250000),(0,1000,0),(0,0,1000),
    (True,1000,10000),(0,1000,False),
])
def test_malformed_source_intervals_fail_closed(start,end,duration):
    with pytest.raises(ValueError):
        summarize_motion([],start_ms=start,end_ms=end,duration_ms=duration)


def test_hints_are_only_near_borders_with_coverage():
    values=series()
    doc=summarize_motion(values,start_ms=7000,end_ms=15000,duration_ms=30000)
    assert doc["status"]=="observed"
    assert len(doc["hints"])<=2
    for h in doc["hints"]:
        core=7000 if h["edge"]=="in" else 15000
        assert abs(h["atMs"]-core)<=2500
        assert "manually inspect" in h["description"]


def test_late_short_recording_is_clipped_to_actual_duration():
    data=summarize_motion([],start_ms=9000,end_ms=10000,duration_ms=10000)
    assert data["windowEndMs"]==10000
    assert data["windowStartMs"]==4000
    assert data["bins"][-1]["endMs"]==10000


def test_out_of_order_series_produce_identical_bins():
    items=series()
    a=summarize_motion(items,start_ms=7000,end_ms=15000,duration_ms=30000)
    b=summarize_motion(list(reversed(items)),start_ms=7000,end_ms=15000,duration_ms=30000)
    assert a==b

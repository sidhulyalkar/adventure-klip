"""Deterministic trim policy regression and API integration tests."""
from __future__ import annotations

import json

import pytest

from adventure_intelligence.clip_rules import suggest_trim, exclusion_spans, VERSION
from adventure_intelligence.catalog import connect
from adventure_intelligence.examples import review_example
from adventure_intelligence.curation_queue import review_queue


def propose(start, end, *, duration=120000, kind="jump_chain",
            activity="ski", excluded=(), incident=False):
    return suggest_trim(start_ms=start, end_ms=end, duration_ms=duration,
                        kind=kind, activity=activity, excluded=excluded,
                        incident_flags=incident)


def test_action_core_is_never_cut_to_match_cinematic_target():
    out=propose(40000, 56000)
    assert out["status"]=="suggested"
    assert out["version"]==VERSION
    assert 0 <= out["startMs"] <= 40000
    assert 56000 <= out["endMs"] <= 120000
    assert out["durationMs"] <= 24000
    assert out["requiresReview"] is True
    assert out["timestampBasis"]=="source_relative_ms"


def test_action_longer_than_target_requires_manual_splitting():
    out=propose(10000, 48000, kind="carving_flow")
    assert out["status"]=="manual_split"
    assert out["canApply"] is False
    assert (out["startMs"],out["endMs"])==(10000,48000)


def test_generic_unverified_motion_can_offer_centered_alternative():
    out=propose(10000, 30000, kind="sustained_motion_candidate")
    assert out["status"]=="suggested"
    assert (out["startMs"],out["endMs"])==(12000,28000)
    assert "generic motion" in out["reasons"][0].lower()
    assert out["coreStartMs"]==10000
    assert out["coreEndMs"]==30000


def test_source_edges_are_hard_bounds():
    a=propose(0, 8000, duration=10000)
    assert a["startMs"]==0
    assert a["endMs"]<=10000
    b=propose(8000, 10000, duration=10000)
    assert b["endMs"]==10000
    assert 0<=b["startMs"]<=8000


def test_human_exclusions_stop_added_context_on_both_sides():
    out=propose(40000, 50000, excluded=[(0,39000),(51000,56000)])
    assert out["status"]=="suggested"
    assert (out["startMs"],out["endMs"])==(39000,51000)
    assert out["canApply"] is True
    assert out["coreStartMs"]>=out["startMs"]
    assert out["coreEndMs"]<=out["endMs"]


def test_candidate_overlapping_manual_exclusion_is_blocked_not_shortened():
    for guards in ([(45000,47000)],[(25000,43000)],[(49000,80000)]):
        out=propose(40000, 50000, excluded=guards)
        assert out["status"]=="blocked"
        assert out["canApply"] is False
        assert (out["startMs"],out["endMs"])==(40000,50000)


def test_guards_unsorted_merge_and_are_deterministic():
    guards=[(51000,53000),(0,39000),(53000,62000),(1000,3000)]
    result=propose(40000,50000,excluded=guards)
    assert result==propose(40000,50000,excluded=list(reversed(guards)))
    assert 39000<=result["startMs"]<=40000
    assert 50000<=result["endMs"]<=51000


def test_snorkeling_is_context_rich_without_inferring_surface_or_animals():
    out=propose(25000, 39000, activity="snorkeling", kind="wildlife_encounter")
    assert out["startMs"]<=25000 and out["endMs"]>=39000
    assert any("visual confirmation" in text for text in out["warnings"])
    assert out["durationMs"]<=35000


def test_incident_remains_unverified_and_long_episode_is_not_cut():
    out=propose(11000, 30000, kind="incident_review")
    assert any("not a verified" in text for text in out["warnings"])
    assert any("not a highlight" in text for text in out["reasons"])
    huge=propose(5000, 51000, kind="camera_rhythm_candidate")
    assert huge["status"]=="manual_split"
    assert huge["canApply"] is False


def test_short_clip_without_safe_context_needs_human_decision():
    out=propose(0, 900, duration=900)
    assert out["status"]=="manual_review"
    assert out["canApply"] is False


@pytest.mark.parametrize("args", [
    {"start_ms":-1,"end_ms":1000,"duration_ms":5000},
    {"start_ms":1000,"end_ms":1000,"duration_ms":5000},
    {"start_ms":0,"end_ms":5001,"duration_ms":5000},
    {"start_ms":0,"end_ms":1000,"duration_ms":0},
    {"start_ms":True,"end_ms":1000,"duration_ms":5000},
    {"start_ms":0.5,"end_ms":1000,"duration_ms":5000},
])
def test_invalid_source_time_rejected(args):
    with pytest.raises(ValueError):
        suggest_trim(kind="jump_chain",activity="ski",**args)


@pytest.mark.parametrize("guards", [[(-1,100)],[(100,100)],[(50,10)],
                                   [(1.5,10)],["danger"],[(0,10,30)]])
def test_malformed_human_guard_rejected(guards):
    with pytest.raises(ValueError):
        propose(500,1000,excluded=guards)


def test_all_proposed_intervals_stay_bounded_and_preserve_long_action_core():
    # Exhaustive small-grid invariant sweep; deterministic and no media access.
    for source in (1000,10000,30000):
        for start in range(0,source,1999):
            for length in (200,2000,5000,20000):
                end=min(source,start+length)
                if end==start:
                    continue
                out=propose(start,end,duration=source)
                assert 0<=out["startMs"]<out["endMs"]<=source
                assert out["coreStartMs"]==start and out["coreEndMs"]==end
                if out["status"] not in {"manual_split","blocked","manual_review"}:
                    assert out["startMs"]<=start and out["endMs"]>=end


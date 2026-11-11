"""Synthetic, offline tests of configurable KSRE goal x sub-sport profiles."""
import math
import pytest

from adventure_intelligence.scene_relevance import Evidence, GOAL_WEIGHTS
from adventure_intelligence.scene_profiles import (
    VERSION, DIMENSIONS, EXTRA_DIMENSIONS, SUBSPORT_PRIORS,
    resolve_profile, profiled_scene_envelope, temporal_span_metrics,
)


def observe(value, strength=1.0, source="adjudicated_observation"):
    return Evidence(value, strength, source, ("synthetic:window:1",))


@pytest.mark.parametrize("goal", list(GOAL_WEIGHTS))
@pytest.mark.parametrize("subsport", list(SUBSPORT_PRIORS))
def test_all_90_goal_x_subsport_profiles_are_valid(goal, subsport):
    p = resolve_profile(goal, subsport)
    assert p["version"] == VERSION
    assert abs(sum(p["weights"].values()) - 1) < 1e-10
    assert all(0 < x <= 1 for x in p["weights"].values())
    assert set(p["weights"]) <= set(DIMENSIONS)


def test_extended_dimensions_are_unique_and_specialized():
    assert len(DIMENSIONS) == len(set(DIMENSIONS))
    assert set(EXTRA_DIMENSIONS) <= set(DIMENSIONS)
    base = resolve_profile("sport_action", "general_adventure")
    park = resolve_profile("sport_action", "skiing_terrain_park")
    assert "takeoff_landing_visibility" not in base["weights"]
    assert park["weights"]["takeoff_landing_visibility"] > 0
    ray = resolve_profile("nature_encounter", "snorkeling_ray_shark")
    assert ray["weights"]["subject_proximity_readability"] > 0


def test_preset_goals_change_weights_but_not_observations():
    obs = {
        "takeoff_landing_visibility": observe(1),
        "water_visibility": observe(0),
    }
    ski = profiled_scene_envelope("sport_action", "skiing_terrain_park", obs)
    water = profiled_scene_envelope("nature_encounter", "snorkeling_reef", obs)
    assert ski["lower"] > water["lower"]
    assert ski["goal"] == "sport_action"
    assert water["subsport"] == "snorkeling_reef"


def test_missing_hypotheses_unknown_and_not_negative_evidence():
    unknown = profiled_scene_envelope("nature_encounter", "snorkeling_ray_shark", {})
    speculative = profiled_scene_envelope("nature_encounter", "snorkeling_ray_shark", {
        "wildlife_behavior_distinctiveness": observe(1, 1, "model_hypothesis")
    })
    assert (unknown["lower"], unknown["upper"]) == (0, 100)
    assert (speculative["lower"], speculative["upper"]) == (0, 100)
    assert speculative["suggestedQueue"] == "investigate_missing_evidence"
    assert speculative["dimensions"]["wildlife_behavior_distinctiveness"]["status"] == "hypothesis_only"
    assert not speculative["autoKeepOrDelete"]


def test_custom_overrides_multipliers_and_parameter_tuning():
    p = resolve_profile("sport_action", "skiing_trees", {
        "weightOverrides": {"terrain_complexity": 1.5, "aesthetic_composition": 0},
        "weightMultipliers": {"control_precision": 2, "terrain_complexity": 2},
        "parameterOverrides": {"contextBeforeSeconds": 12,
                               "reviewIfEvidenceCoverageBelow": .8},
    })
    assert p["customized"]
    assert p["weights"]["terrain_complexity"] > p["weights"]["control_precision"]
    assert "aesthetic_composition" not in p["weights"]
    assert p["parameters"]["contextBeforeSeconds"] == 12
    assert p["parameters"]["reviewIfEvidenceCoverageBelow"] == .8


def test_custom_new_dimension_reweighted_even_when_not_in_preset():
    p = resolve_profile("personal_memory", "kayaking_family", {
        "weightOverrides": {"external_telemetry_alignment": .4}
    })
    assert p["weights"]["external_telemetry_alignment"] > 0


@pytest.mark.parametrize("custom", [
    {"weightOverrides": {"made_up_dimension": 1}},
    {"weightOverrides": {"event_importance": -1}},
    {"weightOverrides": {"event_importance": math.inf}},
    {"weightOverrides": {"event_importance": True}},
    {"weightMultipliers": {"event_importance": math.nan}},
    {"parameterOverrides": {"contextBeforeSeconds": -1}},
    {"parameterOverrides": {"targetMaxShotSeconds": 0}},
    {"parameterOverrides": {"unrecognized_parameter": 1}},
    {"unexpected": 1},
    {"weightOverrides": []},
])
def test_bad_custom_configuration_fails_closed(custom):
    with pytest.raises(ValueError):
        resolve_profile("sport_action", "skiing_speed", custom)


def test_all_zero_weights_rejected():
    baseline = resolve_profile("sport_action", "general_adventure")
    with pytest.raises(ValueError):
        resolve_profile("sport_action", "general_adventure", {
            "weightOverrides": {k: 0 for k in baseline["weights"]}
        })


def test_evidence_strength_shrinks_range_without_becoming_probability():
    a = profiled_scene_envelope("sport_action", "skiing_speed", {
        "action_intensity": observe(.8, .3)
    })
    b = profiled_scene_envelope("sport_action", "skiing_speed", {
        "action_intensity": observe(.8, .9)
    })
    assert b["lower"] > a["lower"]
    assert b["upper"] < a["upper"]
    assert b["uncertaintyWidth"] < a["uncertaintyWidth"]
    assert b["intervalMeaning"].startswith("heuristic_")


def test_reject_unknown_evidence_dimensions_and_broken_types():
    with pytest.raises(ValueError):
        profiled_scene_envelope("sport_action", "skiing_speed", {"fake": observe(.2)})
    with pytest.raises(ValueError):
        profiled_scene_envelope("sport_action", "skiing_speed", {"action_intensity": .2})


def test_temporal_span_coverage_interval_not_averaged_over_missing_as_zero():
    summary = temporal_span_metrics(10000, [
        {"startMs": 1000, "endMs": 3000, "value": .8, "evidenceId": "a"},
        {"startMs": 6000, "endMs": 9000, "value": .4, "evidenceId": "b"},
    ])
    assert summary["coverageFraction"] == .5
    assert summary["observedDurationMs"] == 5000
    assert summary["largestUnobservedGapMs"] == 3000
    assert summary["meanOverObserved"] == .56
    assert summary["lower"] == .28
    assert summary["upper"] == .78
    assert summary["unobservedIsUnknown"]


def test_empty_and_complete_temporal_coverage():
    empty = temporal_span_metrics(4000, [])
    assert empty["meanOverObserved"] is None
    assert (empty["lower"], empty["upper"]) == (0, 1)
    complete = temporal_span_metrics(4000, [
        {"startMs": 0, "endMs": 4000, "value": .4, "evidenceId": "a"}
    ])
    assert complete["coverageFraction"] == 1
    assert complete["lower"] == complete["upper"] == .4


@pytest.mark.parametrize("spans", [
    [{"startMs": 0, "endMs": 10001, "value": .8, "evidenceId": "a"}],
    [{"startMs": 500, "endMs": 500, "value": .8, "evidenceId": "a"}],
    [{"startMs": 0, "endMs": 2000, "value": 1, "evidenceId": "a"},
     {"startMs": 1000, "endMs": 3000, "value": 0, "evidenceId": "b"}],
    [{"startMs": 0, "endMs": 2000, "value": float("nan"), "evidenceId": "a"}],
    [{"startMs": 0, "endMs": 2000, "value": .5, "evidenceId": ""}],
])
def test_bad_spans_rejected(spans):
    with pytest.raises(ValueError):
        temporal_span_metrics(10000, spans)

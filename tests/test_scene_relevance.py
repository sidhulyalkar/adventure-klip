"""Offline synthetic tests for the proposed Klip scene relevance envelope."""
import math

import pytest

from adventure_intelligence.scene_relevance import (
    Evidence, GOAL_WEIGHTS, DIMENSIONS, scene_envelope,
    phase_completeness, pairwise_duplicate_penalty,
)


def observation(value, strength=1., source="human_report"):
    return Evidence(value, strength, source, ("synthetic-review-001",))


def test_all_goal_weights_sum_to_one():
    assert len(DIMENSIONS) == len(set(DIMENSIONS))
    for goal, w in GOAL_WEIGHTS.items():
        assert abs(sum(w.values()) - 1.) < 1e-10, goal
        assert set(w).issubset(set(DIMENSIONS))
        assert all(value >= 0 for value in w.values())


def test_missing_is_not_zero():
    unknown = scene_envelope("nature_encounter", {})
    zero = scene_envelope("nature_encounter", {
        "event_importance": observation(0.),
    })
    assert unknown["lower"] == 0
    assert unknown["upper"] == 100
    assert unknown["uncertaintyWidth"] == 100
    assert zero["upper"] < unknown["upper"]
    assert zero["lower"] == unknown["lower"]
    assert zero["dimensions"]["event_importance"]["status"] == "provided_unadjudicated"


def test_observation_strength_narrows_utility_envelope_monotonically():
    weak = scene_envelope("sport_action", {
        "event_importance": observation(.8, .5),
    })
    strong = scene_envelope("sport_action", {
        "event_importance": observation(.8, .9),
    })
    assert strong["lower"] > weak["lower"]
    assert strong["upper"] < weak["upper"]
    assert strong["uncertaintyWidth"] < weak["uncertaintyWidth"]
    assert strong["evidenceCoverage"] > weak["evidenceCoverage"]


def test_model_hypothesis_cannot_promote_unobserved_wildlife():
    guess = scene_envelope("nature_encounter", {
        "relational_interaction": observation(1., 1., "model_hypothesis")
    })
    assert guess["lower"] == 0
    assert guess["upper"] == 100
    assert guess["dimensions"]["relational_interaction"]["status"] == "hypothesis_only"
    assert guess["eventExistenceVerifiedByMetric"] is False
    assert guess["automaticPublicationAllowed"] is False


def test_goal_changes_interpretation_not_observation():
    same = {
        "personal_meaning": observation(1.),
        "event_importance": observation(0.),
        "arc_completion": observation(0.),
        "subject_readability": observation(0.),
        "aesthetic_composition": observation(0.),
        "technical_clarity": observation(0.),
        "situational_context": observation(0.),
        "within_archive_distinctiveness": observation(0.),
        "learning_potential": observation(0.),
        "continuous_trajectory": observation(0.),
        "relational_interaction": observation(0.),
    }
    personal = scene_envelope("personal_memory", same)
    sports = scene_envelope("sport_action", same)
    assert personal["lower"] > 45
    assert sports["lower"] == 0
    assert personal["upper"] == personal["lower"]
    assert sports["upper"] == sports["lower"]


def test_unobserved_personal_significance_is_not_negligible():
    supplied = {
        k: observation(0.) for k in GOAL_WEIGHTS["personal_memory"]
        if k != "personal_meaning"
    }
    result = scene_envelope("personal_memory", supplied)
    assert result["lower"] == 0
    assert result["upper"] == 49
    assert result["dimensions"]["personal_meaning"]["status"] == "missing"


@pytest.mark.parametrize("invalid", [-.01, 1.01, math.nan, math.inf, "1", True])
def test_reject_invalid_value_or_strength(invalid):
    with pytest.raises(ValueError):
        observation(invalid)
    with pytest.raises(ValueError):
        observation(.5, invalid)


def test_supplied_observation_requires_evidence_identity():
    with pytest.raises(ValueError, match="evidence IDs"):
        Evidence(1., 1., "human_report", ())
    with pytest.raises(ValueError, match="source"):
        Evidence(1., 1., "untrusted_network", ("a",))


def test_unknown_dimension_and_non_evidence_rejected():
    with pytest.raises(ValueError, match="Unrecognized"):
        scene_envelope("sport_action", {"invented_action": observation(1.)})
    with pytest.raises(ValueError, match="Evidence"):
        scene_envelope("sport_action", {"event_importance": .9})
    with pytest.raises(ValueError, match="Unknown editorial goal"):
        scene_envelope("generic", {})


def test_json_evidence_contract_validation():
    parsed = Evidence.from_dict({
        "value": .75, "strength": .8,
        "source": "adjudicated_observation", "evidenceIds": ["packet:a:4000"],
    })
    assert parsed.bounds()[0] == pytest.approx(.6)
    with pytest.raises(ValueError):
        Evidence.from_dict({"value": .7})


def test_phase_completeness_separates_absence_and_unknown():
    complete = phase_completeness({
        "setup": True, "onset": True, "peak": True, "resolution": True,
    })
    uncertain = phase_completeness({"setup": True, "peak": True})
    incomplete = phase_completeness({
        "setup": True, "onset": True, "peak": True, "resolution": False,
    })
    assert complete["lower"] == complete["upper"] == 1.
    assert complete["completeEventVerified"] is True
    assert uncertain["upper"] > uncertain["lower"]
    assert incomplete["upper"] == incomplete["lower"] == .75
    assert incomplete["completeEventVerified"] is False
    with pytest.raises(ValueError):
        phase_completeness({"peak": "true"})


def test_duplicate_penalty_requires_explicit_pairwise_measurements():
    assert pairwise_duplicate_penalty(["a", "b"], {}) == 0
    assert pairwise_duplicate_pen(["a", "b"], {("a", "b"): .8}) == 12
    assert pairwise_duplicate_penalty(["b", "a"], {("a", "b"): .8}) == 12
    with pytest.raises(ValueError):
        pairwise_duplicate_penalty(["a", "a"], {})
    with pytest.raises(ValueError):
        pairwise_duplicate_penalty(
            ["a", "b"], {("a", "b"): .8, ("b", "a"): .8})
    with pytest.raises(ValueError):
        pairwise_duplicate_penalty(["a", "b"], {("a", "b"): 1.3})

"""Klip Scene Relevance Envelope (KSRE), a transparent *research* metric.

This file does not inspect video, infer activity, train, rank unverified media
as facts, modify originals, or approve publication. It composes provided,
source-linked annotations into goal-specific, bounded score intervals.
Evidence strength is a sensitivity parameter, NOT calibrated probability.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping

VERSION = "klip-scene-relevance-envelope-v0"
DIMENSIONS = (
    "event_importance", "arc_completion", "subject_readability",
    "aesthetic_composition", "technical_clarity", "situational_context",
    "within_archive_distinctiveness", "personal_meaning",
    "learning_potential", "continuous_trajectory", "relational_interaction",
)

# Hypothesized goal weights, NOT learned or qualified.
# Sum to 1 per goal, preserving the same dimension names across goals.
GOAL_WEIGHTS = {
    "sport_action": {
        "event_importance": .28, "arc_completion": .23,
        "subject_readability": .10, "aesthetic_composition": .05,
        "technical_clarity": .04, "situational_context": .09,
        "within_archive_distinctiveness": .08,
        "learning_potential": .09, "continuous_trajectory": .04,
    },
    "nature_encounter": {
        "event_importance": .14, "arc_completion": .11,
        "subject_readability": .22, "aesthetic_composition": .08,
        "technical_clarity": .08, "situational_context": .07,
        "within_archive_distinctiveness": .13,
        "relational_interaction": .15, "continuous_trajectory": .02,
    },
    "scenic_transition": {
        "event_importance": .02, "arc_completion": .04,
        "subject_readability": .06, "aesthetic_composition": .33,
        "technical_clarity": .23, "situational_context": .17,
        "within_archive_distinctiveness": .10,
        "continuous_trajectory": .05,
    },
    "personal_memory": {
        "event_importance": .08, "arc_completion": .05,
        "subject_readability": .07, "aesthetic_composition": .05,
        "technical_clarity": .03, "situational_context": .16,
        "within_archive_distinctiveness": .04,
        "personal_meaning": .49, "continuous_trajectory": .03,
    },
    "technique_analysis": {
        "event_importance": .12, "arc_completion": .24,
        "subject_readability": .12, "technical_clarity": .05,
        "situational_context": .09, "within_archive_distinctiveness": .02,
        "learning_potential": .30, "continuous_trajectory": .06,
    },
}

# "model_hypothesis" is never treated as an independently observed event.
# Evidence categories document *who/what* made an assertion, not event truth.
SOURCES = {
    "human_report", "adjudicated_observation", "verified_instrument",
    "model_hypothesis",
}


def _unit(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{name} must be a finite number between 0 and 1")
    v = float(value)
    if not isfinite(v) or not 0 <= v <= 1:
        raise ValueError(f"{name} must be a finite number between 0 and 1")
    return v


@dataclass(frozen=True)
class Evidence:
    """One dimension's declared value, strength and reproducible provenance.

    Missing observations are represented by None, not by zero. Strength is
    *uncalibrated* unless independently evaluated, and the interval made
    from it is an epistemic sensitivity envelope, not a credible interval.
    """

    value: float | None
    strength: float
    source: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError("Unknown evidence source")
        _unit(self.strength, "strength")
        if (not isinstance(self.evidence_ids, tuple)
                or any(not isinstance(k, str) or not k.strip()
                       or len(k) > 200 for k in self.evidence_ids)):
            raise ValueError("Invalid evidence IDs")
        if self.value is not None:
            _unit(self.value, "value")
            if not self.evidence_ids:
                raise ValueError("Observed values require evidence IDs")

    @classmethod
    def from_dict(cls, row: Mapping[str, object]) -> "Evidence":
        if not isinstance(row, Mapping):
            raise ValueError("Evidence should be an object")
        if set(row) != {"value", "strength", "source", "evidenceIds"}:
            raise ValueError("Expected value, strength, source, evidenceIds")
        ids = row["evidenceIds"]
        if not isinstance(ids, list):
            raise ValueError("evidenceIds must be a list")
        return cls(row["value"], row["strength"], row["source"], tuple(ids))

    def bounds(self) -> tuple[float, float]:
        # Not enough evidence to promote a model proposal into event truth.
        if self.value is None or self.source == "model_hypothesis":
            return (0.0, 1.0)
        q, r = _unit(self.value, "value"), _unit(self.strength, "strength")
        # A sensitivity envelope, NOT a statistical confidence interval.
        return (r * q, r * q + 1.0 - r)


def scene_envelope(
    goal: str,
    evidence: Mapping[str, Evidence],
) -> dict:
    """Compute a 0..100 utility *range* under one transparent goal rubric.

    All unobserved dimensions retain full uncertainty. Reviewed human reports
    can contribute but never establish verified wildlife/action claims.
    Nothing here authorizes publishing or irreversible media operations.
    """
    if goal not in GOAL_WEIGHTS:
        raise ValueError(f"Unknown editorial goal: {goal!r}")
    if not isinstance(evidence, Mapping):
        raise ValueError("Evidence must be a mapping")
    unknown = set(evidence).difference(DIMENSIONS)
    if unknown:
        raise ValueError("Unrecognized evidence dimensions: " + ", ".join(sorted(unknown)))
    weights = GOAL_WEIGHTS[goal]
    lower = upper = coverage = 0.0
    details = {}
    for name, weight in weights.items():
        item = evidence.get(name)
        if item is None:
            lo, hi = (0.0, 1.0)
            status = "missing"
            refs = []
        else:
            if not isinstance(item, Evidence):
                raise ValueError(f"{name} must be Evidence")
            lo, hi = item.bounds()
            status = ("hypothesis_only" if item.source == "model_hypothesis"
                      else "missing" if item.value is None
                      else "provided_unadjudicated" if item.source == "human_report"
                      else "evidence_present")
            refs = list(item.evidence_ids)
        lower += weight * lo
        upper += weight * hi
        coverage += weight * (1.0 - (hi - lo))
        details[name] = {
            "weight": weight, "lower": round(lo, 5),
            "upper": round(hi, 5), "status": status,
            "evidenceIds": refs,
        }
    return {
        "version": VERSION, "goal": goal,
        "lower": round(100 * lower, 3),
        "upper": round(100 * upper, 3),
        "midpointForInspectionOnly": round(50 * (lower + upper), 3),
        "evidenceCoverage": round(coverage, 5),
        "uncertaintyWidth": round(100 * (upper - lower), 3),
        "dimensions": details,
        "intervalMeaning": "heuristic_sensitivity_bounds_not_statistical_confidence",
        "automaticPublicationAllowed": False,
        "eventExistenceVerifiedByMetric": False,
        "trainedOnUserReviews": False,
    }


PHASE_WEIGHTS = {
    "setup": .20, "onset": .20, "peak": .35, "resolution": .25
}


def phase_completeness(phases: Mapping[str, bool | None]) -> dict:
    """Evidence-gated narrative coverage of four event phases.

    True = independently reviewed visible phase; False = reviewed absent
    within the candidate; None = unobserved/uncertain. This is not an
    automatic jump/dive/animal-interaction detector. Resolution might be
    inapplicable for some event types: define a relevant phase rubric first.
    """
    if not isinstance(phases, Mapping) or set(phases) - set(PHASE_WEIGHTS):
        raise ValueError("Unsupported phase labels")
    lo = hi = 0.0
    for p, w in PHASE_WEIGHTS.items():
        v = phases.get(p)
        if v is None:
            hi += w
        elif type(v) is bool:
            if v:
                lo += w
                hi += w
        else:
            raise ValueError(f"Phase {p} must be bool or unknown")
    return {
        "lower": round(lo, 5), "upper": round(hi, 5),
        "completeEventVerified": lo == 1.0,
        "uncertaintyRetained": hi > lo,
    }


def pairwise_duplicate_penalty(
    selected_ids: list[str],
    similarities: Mapping[tuple[str, str], float],
    *,
    penalty_per_pair: float = 15.0,
) -> float:
    """Optional collection penalty for independently measured similarity.

    Each pair is penalized once. Missing similarity is UNKNOWN: no fabricated
    similarity or penalty. Similarity must already be normalized [0,1];
    producer, scope and observation provenance belong in the caller ledger.
    """
    if not isinstance(penalty_per_pair, (int, float)) or isinstance(
            penalty_per_pair, bool) or not isfinite(penalty_per_pair) or penalty_per_pair < 0:
        raise ValueError("Invalid diversity penalty")
    if len(selected_ids) != len(set(selected_ids)):
        raise ValueError("Repeated selected ID")
    penalty = 0.0
    for i, a in enumerate(selected_ids):
        for b in selected_ids[i + 1:]:
            pair = (a, b)
            reverse = (b, a)
            if pair in similarities and reverse in similarities:
                raise ValueError("Duplicate similarity pair orientations")
            if pair in similarities or reverse in similarities:
                value = similarities[pair if pair in similarities else reverse]
                penalty += penalty_per_pair * _unit(value, "similarity")
    return round(penalty, 3)

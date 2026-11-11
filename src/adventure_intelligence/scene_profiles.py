"""KSRE V1: editable goal x sub-sport profiles and transparent evidence metrics.

Pure, offline research utilities. A score consumes *already validated*
normalized observations; it never processes video, infers a species or trick,
estimates real-world speed from optical flow, or approves publication.
"""
from __future__ import annotations

from collections import defaultdict
from math import isfinite
from typing import Mapping

from .scene_relevance import Evidence, GOAL_WEIGHTS, DIMENSIONS as CORE_DIMENSIONS

VERSION = "klip-scene-profile-metrics-v1"

# All utility dimensions point in the same direction: higher means greater
# usefulness under a specified rubric, not more dangerous/intense.
EXTRA_DIMENSIONS = (
    "maneuver_difficulty", "terrain_complexity", "control_precision",
    "action_intensity", "flow_rhythm", "takeoff_landing_visibility",
    "obstacle_readability", "wildlife_behavior_distinctiveness",
    "multi_subject_dynamics", "marine_habitat_distinctiveness",
    "water_visibility", "subject_proximity_readability",
    "camera_stability", "occlusion_control", "submersion_continuity",
    "shot_recoverability", "audio_event_alignment", "route_progression",
    "social_context", "external_telemetry_alignment",
    "narrative_transition_fit", "safety_context_readability",
    "framing_quality", "run_duration_value",
)
DIMENSIONS = (*CORE_DIMENSIONS, *EXTRA_DIMENSIONS)

# Hypothesized specialist priorities. These are additive *raw* weights,
# normalized together with goal weights. No number below is a learned value.
# A user may replace/add individual dimension weights or multiply priorities.
SUBSPORT_PRIORS = {
    "general_adventure": {},
    "skiing_cruise": {"flow_rhythm": .13, "framing_quality": .10,
                        "action_intensity": .06, "route_progression": .07},
    "skiing_speed": {"action_intensity": .21, "control_precision": .17,
                      "flow_rhythm": .10, "run_duration_value": .08,
                      "external_telemetry_alignment": .05},
    "skiing_trees": {"terrain_complexity": .18, "obstacle_readability": .19,
                      "control_precision": .15, "flow_rhythm": .08,
                      "safety_context_readability": .08},
    "skiing_moguls": {"terrain_complexity": .13, "control_precision": .23,
                       "flow_rhythm": .13, "run_duration_value": .08,
                       "safety_context_readability": .05},
    "skiing_terrain_park": {"maneuver_difficulty": .19,
                             "takeoff_landing_visibility": .25,
                             "control_precision": .14, "flow_rhythm": .07,
                             "safety_context_readability": .07},
    "skiing_halfpipe": {"maneuver_difficulty": .19,
                         "takeoff_landing_visibility": .20,
                         "continuous_trajectory": .12,
                         "control_precision": .14, "flow_rhythm": .09},
    "skiing_crash_analysis": {"safety_context_readability": .16,
                               "obstacle_readability": .12,
                               "control_precision": .10,
                               "takeoff_landing_visibility": .10,
                               "run_duration_value": .11},
    "mountain_biking_trail": {"terrain_complexity": .13,
                               "control_precision": .12,
                               "flow_rhythm": .18,
                               "obstacle_readability": .14,
                               "run_duration_value": .06},
    "mountain_biking_downhill": {"action_intensity": .16,
                                  "flow_rhythm": .14,
                                  "control_precision": .12,
                                  "obstacle_readability": .15,
                                  "terrain_complexity": .13},
    "mountain_biking_jumps": {"maneuver_difficulty": .18,
                               "takeoff_landing_visibility": .22,
                               "control_precision": .15,
                               "safety_context_readability": .07},
    "dirt_biking_beach": {"action_intensity": .18,
                           "route_progression": .10,
                           "camera_stability": .08,
                           "framing_quality": .10},
    "snorkeling_reef": {"marine_habitat_distinctiveness": .20,
                         "water_visibility": .14,
                         "submersion_continuity": .14,
                         "camera_stability": .09,
                         "framing_quality": .09},
    "snorkeling_ray_shark": {"wildlife_behavior_distinctiveness": .19,
                             "subject_proximity_readability": .20,
                             "multi_subject_dynamics": .10,
                             "water_visibility": .12,
                             "submersion_continuity": .08},
    "snorkeling_fish_school": {"multi_subject_dynamics": .21,
                               "wildlife_behavior_distinctiveness": .13,
                               "framing_quality": .12,
                               "water_visibility": .10,
                               "submersion_continuity": .08},
    "snorkeling_shallow_coral": {"marine_habitat_distinctiveness": .15,
                                 "submersion_continuity": .19,
                                 "occlusion_control": .13,
                                 "safety_context_readability": .10,
                                 "water_visibility": .10},
    "kayaking_scenic": {"framing_quality": .18,
                        "camera_stability": .10,
                        "route_progression": .11,
                        "narrative_transition_fit": .16},
    "kayaking_family": {"social_context": .20,
                        "framing_quality": .10,
                        "narrative_transition_fit": .12,
                        "route_progression": .08},
}

# All defaults are advisory future editing *inspection* parameters.
# They are not applied to user media by this evaluator.
DEFAULT_PARAMETERS = {
    "contextBeforeSeconds": 4.0, "contextAfterSeconds": 4.0,
    "minContinuousActionSeconds": 3.0, "targetMaxShotSeconds": 30.0,
    "reviewIfUncertaintyWidthAbove": 35.0,
    "reviewIfEvidenceCoverageBelow": .60,
}
PARAMETER_RANGES = {
    "contextBeforeSeconds": (0.0, 120.0),
    "contextAfterSeconds": (0.0, 120.0),
    "minContinuousActionSeconds": (0.0, 120.0),
    "targetMaxShotSeconds": (.1, 600.0),
    "reviewIfUncertaintyWidthAbove": (0.0, 100.0),
    "reviewIfEvidenceCoverageBelow": (0.0, 1.0),
}
SUBSPORT_PARAMETERS = {
    "skiing_speed": {"contextBeforeSeconds": 6, "contextAfterSeconds": 5,
                     "minContinuousActionSeconds": 8, "targetMaxShotSeconds": 50},
    "skiing_terrain_park": {"contextBeforeSeconds": 6, "contextAfterSeconds": 6,
                           "minContinuousActionSeconds": 4, "targetMaxShotSeconds": 55},
    "skiing_halfpipe": {"contextBeforeSeconds": 8, "contextAfterSeconds": 6,
                       "minContinuousActionSeconds": 8, "targetMaxShotSeconds": 75},
    "skiing_crash_analysis": {"contextBeforeSeconds": 12, "contextAfterSeconds": 8,
                              "targetMaxShotSeconds": 85},
    "snorkeling_ray_shark": {"contextBeforeSeconds": 5, "contextAfterSeconds": 5,
                            "minContinuousActionSeconds": 3, "targetMaxShotSeconds": 45},
    "snorkeling_reef": {"contextBeforeSeconds": 3, "contextAfterSeconds": 2,
                        "targetMaxShotSeconds": 24},
    "kayaking_scenic": {"contextBeforeSeconds": 2, "contextAfterSeconds": 2,
                        "targetMaxShotSeconds": 15},
}


def _nonnegative(value: object, field: str, maximum: float = 100.0) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not isfinite(value) or not 0 <= value <= maximum):
        raise ValueError(f"{field} must be finite, nonnegative, <= {maximum}")
    return float(value)


def resolve_profile(goal: str, subsport: str,
                    customization: Mapping | None = None) -> dict:
    """Compose baseline goal and sub-sport priorities, then custom tuning.

    Custom shape: {"weightOverrides": {"dimension": value},
      "weightMultipliers": {"dimension": value},
      "parameterOverrides": {"parameter": value}}.
    Overrides replace *raw* weights before final normalization;
    multipliers then scale them. No media is inspected.
    """
    if goal not in GOAL_WEIGHTS:
        raise ValueError("Unknown goal: " + str(goal))
    if subsport not in SUBSPORT_PRIORS:
        raise ValueError("Unknown subsport: " + str(subsport))
    custom = {} if customization is None else customization
    if not isinstance(custom, Mapping) or set(custom) - {
            "weightOverrides", "weightMultipliers", "parameterOverrides"}:
        raise ValueError("Unexpected customization fields")
    opts = {}
    for name in ("weightOverrides", "weightMultipliers", "parameterOverrides"):
        value = custom.get(name, {})
        if not isinstance(value, Mapping):
            raise ValueError(name + " must be an object")
        opts[name] = value
    weight = defaultdict(float)
    for dim, value in GOAL_WEIGHTS[goal].items():
        weight[dim] += float(value)
    for dim, value in SUBSPORT_PRIORS[subsport].items():
        if dim not in DIMENSIONS:
            raise ValueError("Invalid preset dimension")
        weight[dim] += _nonnegative(value, "preset weight")
    for dim, value in opts["weightOverrides"].items():
        if dim not in DIMENSIONS:
            raise ValueError("Unknown weight override dimension: " + str(dim))
        weight[dim] = _nonnegative(value, "weight override")
    for dim, value in opts["weightMultipliers"].items():
        if dim not in DIMENSIONS:
            raise ValueError("Unknown weight multiplier dimension: " + str(dim))
        weight[dim] = weight[dim] * _nonnegative(value, "weight multiplier")
    total = sum(weight.values())
    if total <= 0 or not isfinite(total):
        raise ValueError("At least one nonzero dimension weight required")
    normalized = {k: v / total for k, v in sorted(weight.items()) if v > 0}
    params = {**DEFAULT_PARAMETERS, **SUBSPORT_PARAMETERS.get(subsport, {})}
    for key, val in opts["parameterOverrides"].items():
        if key not in PARAMETER_RANGES:
            raise ValueError("Unknown profile parameter: " + str(key))
        low, high = PARAMETER_RANGES[key]
        v = _nonnegative(val, key, high)
        if v < low:
            raise ValueError(key + " is below minimum")
        params[key] = v
    return {
        "version": VERSION, "goal": goal, "subsport": subsport,
        "weights": normalized, "parameters": params,
        "customized": any(bool(x) for x in opts.values()),
        "weightsLearned": False,
        "profileState": "human_editable_research_defaults",
    }


def profiled_scene_envelope(goal: str, subsport: str,
                            evidence: Mapping[str, Evidence],
                            customization: Mapping | None = None) -> dict:
    """Goal/sub-sport sensitivity envelope, not a probability or trained rank."""
    profile = resolve_profile(goal, subsport, customization)
    if not isinstance(evidence, Mapping) or set(evidence) - set(DIMENSIONS):
        raise ValueError("Invalid or unknown scene evidence dimensions")
    lower = upper = coverage = 0.0
    detail = {}
    for dim, w in profile["weights"].items():
        item = evidence.get(dim)
        if item is not None and not isinstance(item, Evidence):
            raise ValueError(dim + " must be Evidence")
        lo, hi = (0.0, 1.0) if item is None else item.bounds()
        lower += w * lo
        upper += w * hi
        coverage += w * (1.0 - (hi - lo))
        detail[dim] = {
            "weight": round(w, 6), "lower": round(lo, 5),
            "upper": round(hi, 5),
            "status": "missing" if item is None or item.value is None else (
                "hypothesis_only" if item.source == "model_hypothesis" else
                "human_report" if item.source == "human_report" else "evidence_present"),
            "evidenceIds": [] if item is None else list(item.evidence_ids),
        }
    width = 100 * (upper - lower)
    review = (width > profile["parameters"]["reviewIfUncertaintyWidthAbove"]
              or coverage < profile["parameters"]["reviewIfEvidenceCoverageBelow"])
    return {
        "version": VERSION, "goal": goal, "subsport": subsport,
        "profile": profile,
        "lower": round(100 * lower, 3), "upper": round(100 * upper, 3),
        "uncertaintyWidth": round(width, 3),
        "midpointForInspectionOnly": round(50 * (lower + upper), 3),
        "evidenceCoverage": round(coverage, 5), "dimensions": detail,
        "suggestedQueue": "investigate_missing_evidence" if review
                          else "human_editorial_inspection",
        "autoKeepOrDelete": False, "publishApproved": False,
        "modelInferencePerformed": False,
        "intervalMeaning": "heuristic_sensitivity_envelope_not_statistical_confidence",
    }


def temporal_span_metrics(duration_ms: int, spans: list[dict]) -> dict:
    """Quantify independently annotated [startMs,endMs) scalar evidence.

    Spans must be disjoint, ordered, clipped to source-relative duration and
    have source-linked evidence IDs. Missing time is unknown, never low-value.
    Outputs coverage, observed time-weighted mean and interval bounds.
    No speed units or causal event interpretation can be inferred here.
    """
    if type(duration_ms) is not int or not 1 <= duration_ms <= 86_400_000:
        raise ValueError("Invalid durationMs")
    if not isinstance(spans, list) or len(spans) > 100_000:
        raise ValueError("Invalid/beyond-budget spans")
    observed = weighted = 0.0
    prior_end = 0
    largest_gap = 0
    for i, span in enumerate(spans):
        if not isinstance(span, dict) or set(span) != {
                "startMs", "endMs", "value", "evidenceId"}:
            raise ValueError("Bad observed span format")
        start, end = span["startMs"], span["endMs"]
        ident = span["evidenceId"]
        if (type(start) is not int or type(end) is not int
                or not prior_end <= start < end <= duration_ms
                or not isinstance(ident, str) or not ident.strip()
                or len(ident) > 200):
            raise ValueError("Unsorted, overlapping or ungrounded span")
        value = _nonnegative(span["value"], f"span {i} normalized value", 1.0)
        largest_gap = max(largest_gap, start - prior_end)
        weighted += (end - start) * value
        observed += end - start
        prior_end = end
    largest_gap = max(largest_gap, duration_ms - prior_end)
    fraction = observed / duration_ms
    return {
        "version": VERSION, "sourceDurationMs": duration_ms,
        "observedDurationMs": int(observed),
        "coverageFraction": round(fraction, 6),
        "meanOverObserved": round(weighted / observed, 6) if observed else None,
        "lower": round(weighted / duration_ms, 6),
        "upper": round((weighted + duration_ms - observed) / duration_ms, 6),
        "largestUnobservedGapMs": largest_gap,
        "unobservedIsUnknown": True,
    }

"""Bounded structured Ollama scene output and evidence-preserving partial recovery.

A small image-language model may output a useful factual-looking scene
*proposal* alongside invalid structured nested evidence. Keep only independently
schema-valid fields and record every discarded category. Nothing becomes
ground truth; no inferred place, species, body mechanics or personal intent.
"""
from __future__ import annotations

import math

from .scene_profiles import SUBSPORT_PRIORS

# Dimensions that can plausibly be supported by sparse images. Do not ask
# a VLM to guess personal significance, archive rarity, muscle activation,
# event outcome or calibrated physical performance from four stills.
VISUAL_DIMENSIONS = (
    "subject_readability", "aesthetic_composition", "technical_clarity",
    "situational_context", "terrain_complexity", "water_visibility",
    "camera_stability", "occlusion_control", "framing_quality",
    "subject_proximity_readability", "obstacle_readability",
)
CONTEXTS = (
    "snow_mountain", "underwater_reef", "underwater_open_water",
    "forest_trail", "coastal_outdoor", "general_outdoor", "indoor", "unknown",
)
CAMERA_QUALITIES = ("clear", "mixed", "obscured", "unknown")
SCHEMA_VERSION = "klip-ollama-visual-proposal-schema-v2"
ERROR_CODES = {
    "Incorrect vision judge JSON schema": "top_level_schema",
    "Unsupported sub-sport suggestion": "unsupported_subsport",
    "Malformed local judge description": "invalid_description_or_text",
    "Unsupported scene context proposal": "unsupported_scene_context",
    "Unknown camera quality judgment": "unsupported_camera_quality",
    "Invalid model certainty": "invalid_confidence",
    "Invalid scene tags/unknowns": "invalid_tag_or_uncertainty_array",
    "Invalid per-frame observations": "invalid_frame_observations_array",
    "Observation references unknown sampled still": "invalid_frame_observation_reference",
    "Invalid scene feature candidate count": "invalid_feature_array",
    "Unsupported scene dimension proposal": "unsupported_feature_dimension",
    "Feature cites invalid sampled frames": "invalid_feature_frame_reference",
    "Invalid event proposals": "invalid_event_array",
    "Invalid event hint": "invalid_event_item",
    "Event outside observed frame indices": "invalid_event_frame_reference",
    "Unrecoverable core scene description": "unrecoverable_scene_summary",
}


def reason_code(error: Exception) -> str:
    """Static, content-free error diagnostic. Never echo user/model text."""
    return ERROR_CODES.get(str(error), "schema_validation_unknown")


def _string(maxlen: int) -> dict:
    return {"type": "string", "minLength": 1, "maxLength": maxlen}


def ollama_schema(frame_count: int) -> dict:
    """JSON Schema for Ollama's format field, not just generic JSON mode."""
    if type(frame_count) is not int or not 2 <= frame_count <= 8:
        raise ValueError("Invalid schema frame count")
    index = {"type": "integer", "minimum": 0, "maximum": frame_count-1}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "subsport", "sceneSummary", "sceneContext", "tags",
            "frameObservations", "eventHints", "featureCandidates",
            "uncertainties", "cameraQuality", "confidence",
        ],
        "properties": {
            "subsport": {"type": "string", "enum": sorted(SUBSPORT_PRIORS)},
            "sceneSummary": _string(300),
            "sceneContext": {"type": "string", "enum": list(CONTEXTS)},
            "tags": {
                "type": "array", "maxItems": 10, "items": _string(50),
            },
            "frameObservations": {
                "type": "array", "maxItems": frame_count,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["frameIndex", "observation"],
                    "properties": {
                        "frameIndex": index, "observation": _string(240),
                    },
                },
            },
            "eventHints": {
                "type": "array", "maxItems": 8,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["kind", "firstFrameIndex", "lastFrameIndex"],
                    "properties": {
                        "kind": _string(90),
                        "firstFrameIndex": index,
                        "lastFrameIndex": index,
                    },
                },
            },
            "featureCandidates": {
                "type": "array", "maxItems": 8,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["dimension", "frameIndices", "observation"],
                    "properties": {
                        "dimension": {
                            "type": "string", "enum": list(VISUAL_DIMENSIONS),
                        },
                        "frameIndices": {
                            "type": "array", "minItems": 1,
                            "maxItems": frame_count, "items": index,
                        },
                        "observation": _string(240),
                    },
                },
            },
            "uncertainties": {
                "type": "array", "maxItems": 6, "items": _string(150),
            },
            "cameraQuality": {
                "type": "string", "enum": list(CAMERA_QUALITIES),
            },
            "confidence": {
                "type": "number", "minimum": 0, "maximum": 1,
            },
        },
    }


def _short(value: object, maximum: int) -> bool:
    return (isinstance(value, str) and bool(value.strip())
            and len(value) <= maximum)


def _idx(index: object, n: int) -> bool:
    return type(index) is int and 0 <= index < n


def _reduce_records(raw: object, cap: int, check, fields: tuple[str, ...]) -> tuple[list, bool]:
    """Only retain whole valid evidence records, never synthesize or renumber."""
    if not isinstance(raw, list):
        return [], raw is not None
    kept = []
    dropped = False
    for item in raw:
        if (len(kept) >= cap or not isinstance(item, dict)
                or set(item) != set(fields) or not check(item)):
            dropped = True
            continue
        kept.append({k: item[k] for k in fields})
    return kept, dropped


def normalize_model_hypothesis(raw: object, frame_count: int) -> tuple[dict, str, list[str]]:
    """Strict first, then conservative partial recovery of visible evidence.

    No raw response is saved. We retain a core visual summary only when a
    model supplied a bounded, non-empty summary string. All optional invalid
    evidence items are discarded entirely, not repaired into fictional frame
    references. Unknown subsports and contexts are *not* promoted to labels.
    Returns (legacy-compatible validated proposal, status, diagnostic codes).
    """
    from .ktide_local_judge import validate_judge_proposal
    try:
        accepted = validate_judge_proposal(raw, frame_count)
        reasons = []
        selected = [f for f in accepted["featureCandidates"]
                    if f["dimension"] in VISUAL_DIMENSIONS]
        if len(selected) != len(accepted["featureCandidates"]):
            accepted["featureCandidates"] = selected
            reasons.append("discarded_unobservable_feature_hypotheses")
        return accepted, ("partial" if reasons else "strict"), reasons
    except ValueError as original_error:
        original_reason = reason_code(original_error)

    if not isinstance(raw, dict) or not _short(raw.get("sceneSummary"), 300):
        raise ValueError("Unrecoverable core scene description")
    reasons = [original_reason]
    candidate = {}
    candidate["sceneSummary"] = raw["sceneSummary"].strip()
    subsport = raw.get("subsport")
    if not isinstance(subsport, str) or subsport not in SUBSPORT_PRIORS:
        # A real model may output "skiing" instead of a specialist sub-sport.
        # Preserve the description but explicitly label the sub-sport unknown.
        subsport = "general_adventure"
        reasons.append("unrecognized_subsport_not_verified")
    candidate["subsport"] = subsport
    context = raw.get("sceneContext")
    if not isinstance(context, str) or context not in CONTEXTS:
        context = "unknown"
        reasons.append("scene_context_unknown")
    candidate["sceneContext"] = context
    quality = raw.get("cameraQuality")
    if not isinstance(quality, str) or quality not in CAMERA_QUALITIES:
        quality = "unknown"
        reasons.append("camera_quality_unknown")
    candidate["cameraQuality"] = quality
    value = raw.get("confidence")
    if (type(value) not in (int, float)
            or not math.isfinite(value) or not 0 <= value <= 1):
        value = 0.0  # conservative unknown placeholder, never calibrated
        reasons.append("model_confidence_unavailable")
    candidate["confidence"] = value

    for name, limit, maximum in (
        ("tags", 10, 50), ("uncertainties", 6, 150),
    ):
        values = raw.get(name)
        kept = []
        if isinstance(values, list):
            for value in values:
                if _short(value, maximum) and len(kept) < limit:
                    kept.append(value.strip())
                else:
                    reasons.append("discarded_invalid_" + name)
        elif values is not None:
            reasons.append("discarded_invalid_" + name)
        candidate[name] = kept

    def observation(item):
        return (_idx(item["frameIndex"], frame_count)
                and _short(item["observation"], 240))
    def hint(item):
        a, b = item["firstFrameIndex"], item["lastFrameIndex"]
        return (_idx(a, frame_count) and _idx(b, frame_count)
                and a <= b and _short(item["kind"], 90))
    def feature(item):
        inds = item["frameIndices"]
        return (isinstance(item["dimension"], str)
                and item["dimension"] in VISUAL_DIMENSIONS
                and isinstance(inds, list)
                and 1 <= len(inds) <= frame_count
                and all(_idx(i, frame_count) for i in inds)
                and len(set(inds)) == len(inds)
                and _short(item["observation"], 240))
    for name, limit, check, keys in (
        ("frameObservations", frame_count, observation,
         ("frameIndex", "observation")),
        ("eventHints", 8, hint,
         ("kind", "firstFrameIndex", "lastFrameIndex")),
        ("featureCandidates", 8, feature,
         ("dimension", "frameIndices", "observation")),
    ):
        accepted, discarded = _reduce_records(raw.get(name), limit, check, keys)
        candidate[name] = accepted
        if discarded:
            reasons.append("discarded_invalid_" + name)
    # Retain strictly validated model evidence, and never turn a missing
    # frame reference into an apparently measured observation.
    accepted = validate_judge_proposal(candidate, frame_count)
    return accepted, "partial", sorted(set(reasons))

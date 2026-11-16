"""Owner scene interpretation shared across activity-specific review surfaces.

A model may describe objects, but only the owner can attest why a personal
scene was recorded and how it should contribute to a story. Store this as
source-linked *human* evidence. Do not parse free text into model truth or use
its sentiment to automatically rank without an evaluated training pipeline.
"""
from __future__ import annotations

NARRATIVE_FIELDS = frozenset({
    "sceneDescription", "whyKeep", "sceneType", "storyRole",
    "filmingIntent", "personalImportance", "sceneInterest",
})
MAX_TEXT_LENGTH = {"sceneDescription": 2400, "whyKeep": 1800}
SCENE_TYPES = frozenset((
    "wildlife_encounter", "reef_exploration", "scenic_underwater",
    "movement_action", "social_personal", "discovery", "other", "uncertain",
))
STORY_ROLES = frozenset((
    "key_moment", "supporting_sequence", "establishing_scene",
    "transition", "atmosphere", "personal_memory", "unassigned",
))
FILMING_INTENTS = frozenset(("intentional", "incidental", "unknown"))
PERSONAL_IMPORTANCE = frozenset(("high", "medium", "low", "unsure"))


def empty_narrative() -> dict:
    return {
        "sceneDescription": "", "whyKeep": "",
        "sceneType": "uncertain", "storyRole": "unassigned",
        "filmingIntent": "unknown", "personalImportance": "unsure",
        "sceneInterest": None,
    }


def validate_narrative(fields: dict) -> dict:
    """Validate and preserve owner's words; never invent a description."""
    if not isinstance(fields, dict) or set(fields) != NARRATIVE_FIELDS:
        raise ValueError("Expected the complete scene narrative field set")
    result = {}
    for name, limit in MAX_TEXT_LENGTH.items():
        value=fields[name]
        if not isinstance(value,str) or len(value)>limit or "\x00" in value:
            raise ValueError(f"{name} must be plain text, at most {limit} characters")
        result[name] = value
    for name, allowed in (
        ("sceneType", SCENE_TYPES),
        ("storyRole", STORY_ROLES),
        ("filmingIntent", FILMING_INTENTS),
        ("personalImportance", PERSONAL_IMPORTANCE),
    ):
        if type(fields[name]) is not str or fields[name] not in allowed:
            raise ValueError(f"Invalid {name}")
        result[name] = fields[name]
    interest=fields["sceneInterest"]
    if interest is not None and (type(interest) is not int or not 1<=interest<=5):
        raise ValueError("sceneInterest must be a human 1–5 rating or null")
    result["sceneInterest"]=interest
    return result


def extract_narrative(row: dict) -> dict | None:
    """Legacy v1 reviews have no scene notes. They remain valid as-is."""
    present=NARRATIVE_FIELDS & set(row)
    if not present:
        return None
    if present!=NARRATIVE_FIELDS:
        raise ValueError("Scene narrative fields are incomplete")
    return validate_narrative({k:row[k] for k in NARRATIVE_FIELDS})

"""Conservative, deterministic trim suggestions for human-reviewed source intervals.

This module NEVER decodes media, edits a source or claims frame-accurate boundaries.
Candidate intervals are provisional; suggestion status is not a quality verdict.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


VERSION = "trim-policy-v1"
PRESERVE_COMPLETE = frozenset({
    "jump_chain", "carving_flow", "tree_weaving",
    "technical_feature_chain", "wildlife_encounter",
})
REVIEW_ONLY = frozenset({"incident_review", "camera_rhythm_candidate"})

# Milliseconds. These are *starting editorial heuristics*, not measured event latencies.
# Long confirmed-by-human sequences should not be forcibly cut to a cinematic target.
@dataclass(frozen=True)
class TrimProfile:
    before_ms: int
    after_ms: int
    max_ms: int
    min_ms: int


PROFILES = {
    "ski": TrimProfile(2000, 2800, 24000, 3500),
    "bike": TrimProfile(2200, 2800, 24000, 3500),
    "snorkeling": TrimProfile(3000, 4000, 35000, 5000),
    "unknown": TrimProfile(1500, 2000, 18000, 3000),
}
GENERIC = TrimProfile(1100, 1500, 16000, 3000)
INCIDENT = TrimProfile(5000, 8000, 35000, 5000)


def _integer(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(field + " must be integer milliseconds")
    return value


def _normalize_guards(spans: Iterable[tuple[int, int]], duration_ms: int) -> list[tuple[int, int]]:
    """Clip, sort, and merge explicit human exclusions; reject malformed inputs."""
    clean = []
    for span in spans:
        if not isinstance(span, (tuple, list)) or len(span) != 2:
            raise ValueError("Exclusions must be (startMs, endMs) pairs")
        a = _integer(span[0], "excluded start")
        b = _integer(span[1], "excluded end")
        if a < 0 or b <= a:
            raise ValueError("Invalid exclusion interval")
        a, b = min(a, duration_ms), min(b, duration_ms)
        if b > a:
            clean.append((a, b))
    result: list[tuple[int, int]] = []
    for a, b in sorted(clean):
        if result and a <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], b))
        else:
            result.append((a, b))
    return result


def suggest_trim(*, start_ms: int, end_ms: int, duration_ms: int,
                 kind: str, activity: str = "unknown",
                 excluded: Iterable[tuple[int, int]] = (),
                 incident_flags: bool = False) -> dict:
    """Offer explicit source-relative cut bounds without replacing the source candidate.

    Never cross human exclusions. A long action sequence is preserved intact
    and requires human splitting instead of having its beginning/end lopped off.
    Manual review is required for all suggestions; callers must opt in to use them.
    """
    start = _integer(start_ms, "start")
    end = _integer(end_ms, "end")
    duration = _integer(duration_ms, "duration")
    if duration <= 0 or start < 0 or end <= start or end > duration:
        raise ValueError("Candidate outside valid source duration")
    if not isinstance(kind, str) or not isinstance(activity, str):
        raise ValueError("kind and activity must be strings")
    guards = _normalize_guards(excluded, duration)
    reasons: list[str] = []
    warnings = ["Review the first and final frames; source-relative milliseconds are not frame-aligned."]
    preserve = kind in PRESERVE_COMPLETE
    incident = incident_flags or kind in REVIEW_ONLY
    profile = INCIDENT if incident else (PROFILES.get(activity, PROFILES["unknown"]) if preserve
                                        else GENERIC)

    # Human exclusions override every statistical or cinematic preference.
    if any(left < end and right > start for left, right in guards):
        return {
            "version": VERSION, "status": "blocked", "canApply": False,
            "startMs": start, "endMs": end, "coreStartMs": start, "coreEndMs": end,
            "durationMs": end - start, "reasons": ["A human-excluded section overlaps this candidate."],
            "warnings": warnings, "requiresReview": True, "timestampBasis": "source_relative_ms",
        }

    left_limit, right_limit = 0, duration
    for left, right in guards:
        if right <= start:
            left_limit = max(left_limit, right)
        if left >= end:
            right_limit = min(right_limit, left)
            break

    core_length = end - start
    if core_length > profile.max_ms and (preserve or incident):
        return {
            "version": VERSION, "status": "manual_split", "canApply": False,
            "startMs": start, "endMs": end, "coreStartMs": start, "coreEndMs": end,
            "durationMs": core_length,
            "reasons": ["Preserve the complete possible sequence; split only after watching."],
            "warnings": warnings, "requiresReview": True, "timestampBasis": "source_relative_ms",
        }

    if core_length > profile.max_ms:
        # Generic motion proposals have no confirmed action core. Offer a shorter
        # centered *alternative*, never silently replace the original window.
        start_new = start + (core_length - profile.max_ms) // 2
        end_new = start_new + profile.max_ms
        reasons.append("Long generic motion proposal shortened around its midpoint; action may be missed.")
    else:
        budget = profile.max_ms - core_length
        wanted_before, wanted_after = profile.before_ms, profile.after_ms
        if wanted_before + wanted_after > budget:
            # Distribute the remaining context budget without cutting the core.
            before = budget * wanted_before // (wanted_before + wanted_after)
            after = budget - before
        else:
            before, after = wanted_before, wanted_after
        start_new = max(left_limit, start - before)
        end_new = min(right_limit, end + after)
        if start_new > start or end_new < end:
            raise AssertionError("Context bounds must preserve the core")
        if start_new < start or end_new > end:
            reasons.append("Added source context before/after the candidate within safe bounds.")

    if start_new == left_limit or end_new == right_limit:
        reasons.append("Source edge or explicitly excluded footage limits context.")
    if incident:
        warnings.append("Possible camera disturbance, not a verified fall, crash or injury.")
    if activity == "snorkeling":
        warnings.append("Surface transitions and animal identity require visual confirmation.")
    if core_length < profile.min_ms and end_new - start_new < profile.min_ms:
        return {
            "version": VERSION, "status": "manual_review", "canApply": False,
            "startMs": start, "endMs": end, "coreStartMs": start, "coreEndMs": end,
            "durationMs": core_length, "reasons": ["Too little safe footage for a suggested cut."],
            "warnings": warnings, "requiresReview": True, "timestampBasis": "source_relative_ms",
        }
    if preserve:
        reasons.append("Kept the entire proposed multi-frame action or encounter interval.")
    if incident:
        reasons.append("Inspect the event manually; this is not a highlight-quality claim.")
    if not reasons:
        reasons.append("Existing interval is already within the policy's context and duration limits.")
    status = "suggested" if (start_new, end_new) != (start, end) else "unchanged"
    return {
        "version": VERSION, "status": status, "canApply": status == "suggested",
        "startMs": start_new, "endMs": end_new, "coreStartMs": start, "coreEndMs": end,
        "durationMs": end_new - start_new, "reasons": reasons, "warnings": warnings,
        "requiresReview": True, "timestampBasis": "source_relative_ms",
    }


def exclusion_spans(db, asset_id: str, activity: str, reviewed: list[dict]) -> list[tuple[int, int]]:
    """Only human exclusions: manually labeled non-action events + explicit skips.

    This is a read-only catalog query and does not trigger decoding or inference.
    """
    from .editorial_quality import manual_exclusions
    events = [dict(r) for r in db.execute(
        "SELECT origin,kind,start_ms,end_ms FROM events WHERE asset_id=?", (asset_id,))]
    spans = [(a, b) for a, b, _ in manual_exclusions(events, activity)]
    spans.extend((max(0, int(r["start_ms"]) - 300), int(r["end_ms"]) + 300)
                 for r in reviewed
                 if r["asset_id"] == asset_id and r["highlight"] == "skip")
    return spans

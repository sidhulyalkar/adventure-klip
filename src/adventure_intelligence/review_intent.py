"""Offline, manifest-aware analysis of human review *intent*, not video truth.

This standalone bridge runs on main without the experimental packet/reel
stack. Keep/Skip are derivative-specific; no footage is decoded, removed or
published. Text-based editorial routing is provisional and auditable.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import re

from .review_utility_v2 import editorial_route

VERSION = "klip-offline-review-intent-v1"
REVIEW_VERSION = "klip-highlight-curation-review-export-v1"
SHA = re.compile(r"^[0-9a-f]{64}$")
IDENT = re.compile(r"^[0-9a-f]{24}$")
CHOICES = {"keep", "skip", "neither", "cannot_tell"}
GOALS = {"athletic_skill", "story_payoff", "visual_quality", "humor",
         "personal_memory", "nature_encounter", "technical_documentation", "other"}
ROLES = {"athlete", "expert_coach", "casual_spectator", "filmmaker",
         "editor", "nature_specialist", "safety_reviewer", "participant", "other"}
OUTCOMES = {"completed", "failed", "partial", "not_applicable", "unknown"}
VISIBLE = {"yes", "no", "ambiguous"}
FIELDS = {"recordId", "annotatorId", "role", "goal", "choice",
          "outcome", "outcomeVisible", "technicalQuality",
          "aestheticQuality", "eventReadability", "eventPeakMs",
          "resolutionMs", "rationale"}
REQUIRED = FIELDS - {"eventPeakMs", "resolutionMs"}


def _record_window(manifest_index: dict | None, rid: str) -> tuple[int, int] | None:
    if manifest_index is None:
        return None
    if rid not in manifest_index:
        raise ValueError("Human recordId not present in frozen manifest")
    event = manifest_index[rid].get("event")
    if not isinstance(event, dict):
        raise ValueError("Frozen event missing")
    start, end = event.get("startMs"), event.get("endMs")
    if (type(start) is not int or type(end) is not int
            or not 0 <= start < end):
        raise ValueError("Invalid frozen event timestamps")
    return start, end


def _check(row: dict, window: tuple[int, int] | None) -> dict:
    if not isinstance(row, dict) or not REQUIRED <= set(row) or set(row) - FIELDS:
        raise ValueError("Review schema invalid")
    rid, annotator = row["recordId"], row["annotatorId"]
    if (not isinstance(rid, str) or not IDENT.fullmatch(rid)
            or not isinstance(annotator, str)
            or not 4 <= len(annotator) <= 100):
        raise ValueError("Review identifier invalid")
    for key, domain in (("goal", GOALS), ("choice", CHOICES),
                        ("role", ROLES), ("outcome", OUTCOMES),
                        ("outcomeVisible", VISIBLE)):
        if not isinstance(row[key], str) or row[key] not in domain:
            raise ValueError("Unsupported " + key)
    if row["outcome"] in ("completed", "failed") and row["outcomeVisible"] != "yes":
        raise ValueError("Outcome requires visible evidence")
    for key in ("technicalQuality", "aestheticQuality", "eventReadability"):
        x = row[key]
        if x is not None and (type(x) is not int or not 1 <= x <= 5):
            raise ValueError("Invalid rated quality " + key)
    for key in ("eventPeakMs", "resolutionMs"):
        x = row.get(key)
        if x is not None and (type(x) is not int or x < 0
                              or window is not None
                              and not window[0] <= x < window[1]):
            raise ValueError("Invalid source-relative event marker")
    peak, resolution = row.get("eventPeakMs"), row.get("resolutionMs")
    if peak is not None and resolution is not None and resolution < peak:
        raise ValueError("Resolution precedes event peak")
    rationale = row["rationale"]
    if not isinstance(rationale, str) or len(rationale) > 1000:
        raise ValueError("Invalid rationale")
    if row["choice"] in ("keep", "skip") and len(rationale.strip()) < 10:
        raise ValueError("Keep/Skip need a review rationale")
    return {
        "preference": row["choice"],
        "highlightGoal": row["goal"],
        "observedOutcome": row["outcome"],
        "rationale": rationale,
        "eventPeakMs": peak, "resolutionMs": resolution,
    }


def analyze_export(export: dict, manifest_bytes: bytes | None = None) -> dict:
    """Return private read-only routing and aggregate diagnostics.

    With manifest_bytes, independently check its exact file digest,
    record roster and time bounds. Never claim preview SHA or video truth:
    source media and preview receipts are deliberately not accessed here.
    """
    if (not isinstance(export, dict) or set(export) != {
            "version", "manifestSha256", "reviews"}
            or export["version"] != REVIEW_VERSION
            or not isinstance(export["manifestSha256"], str)
            or not SHA.fullmatch(export["manifestSha256"])):
        raise ValueError("Unsupported review export")
    manifest = None
    if manifest_bytes is not None:
        if (not isinstance(manifest_bytes, bytes)
                or len(manifest_bytes) > 12_000_000
                or sha256(manifest_bytes).hexdigest() != export["manifestSha256"]):
            raise ValueError("Frozen review manifest digest mismatch")
        try:
            manifest = json.loads(manifest_bytes)
        except (UnicodeError, ValueError) as exc:
            raise ValueError("Invalid frozen manifest JSON") from exc
        if not isinstance(manifest, dict):
            raise ValueError("Invalid manifest")
    manifest_index = None
    if manifest is not None:
        records = manifest.get("records")
        if not isinstance(records, list) or len(records) > 100000:
            raise ValueError("Unsupported manifest record roster")
        manifest_index = {}
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("Invalid manifest record")
            rid = record.get("recordId")
            if (not isinstance(rid, str) or not IDENT.fullmatch(rid)
                    or rid in manifest_index):
                raise ValueError("Duplicate/invalid manifest recordId")
            manifest_index[rid] = record
    rows = export["reviews"]
    if not isinstance(rows, list) or len(rows) > 3000:
        raise ValueError("Invalid/excessive reviews")
    seen = set()
    output = []
    destinations = Counter()
    edits = Counter()
    choices = Counter()
    ratings = Counter()
    for review in rows:
        window = None
        if isinstance(review, dict):
            rid = review.get("recordId")
            if not isinstance(rid, str) or not IDENT.fullmatch(rid) or rid in seen:
                raise ValueError("Duplicate or malformed review ID")
            seen.add(rid)
            window = _record_window(manifest_index, rid)
        normalized = _check(review, window)
        route = editorial_route(normalized)
        choices[normalized["preference"]] += 1
        destinations[route["destination"]] += 1
        for tag in route["editActions"]:
            edits[tag] += 1
        for key in ("technicalQuality", "aestheticQuality", "eventReadability"):
            if review[key] is not None:
                ratings[key] += 1
        output.append({
            "recordId": review["recordId"], "choice": normalized["preference"],
            "declaredGoal": normalized["highlightGoal"],
            "intent": route,
            "previewActuallyInspectedByThisTool": False,
            "sourceEventsIndependentlyVerified": False,
        })
    return {
        "version": VERSION,
        "manifestSha256": export["manifestSha256"],
        "manifestDigestVerified": manifest is not None,
        "reviewCount": len(output),
        "choices": dict(sorted(choices.items())),
        "destinations": dict(sorted(destinations.items())),
        "editActionCounts": dict(sorted(edits.items())),
        "ratedFieldCounts": dict(sorted(ratings.items())),
        "records": output,
        "sourceVideoReads": 0,
        "previewVerificationPerformed": False,
        "weightsFitted": False,
        "trainingPerformed": False,
        "autoSourceDeleteOrPublication": False,
        "interpretation": "reviewed_text_intent_only_not_scene_evidence",
    }

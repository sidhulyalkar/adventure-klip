"""Pure diagnostics for contradictory Auto-Scout category proposals.

This module does not make classifications authoritative, choose edit windows,
or modify the source Auto-Scout experiment. Re-inference can differ from the
original failure because the failed model response was deliberately not saved.
"""
from __future__ import annotations

from collections import Counter
from html import escape
import json

from .auto_scout_v2 import ACTIVITIES, POLICY, json_schema, validate_proposal
from .moment_edit_v2 import TAGS
from . import adaptive_judge_v2 as ollama
from . import temporal_second_look as temporal

VERSION = "klip-autoscout-disagreement-probe-v1"
KNOWN_TAGS = frozenset().union(*TAGS.values())


def inspect_response(raw: object) -> dict:
    """Inspect consistency without silently promoting any model hypotheses."""
    if not isinstance(raw, dict):
        return {
            "status": "invalid_response_structure", "activityProposal": "unavailable",
            "validCategoryHints": [], "crossSportTags": [],
            "duplicateTags": [], "unknownTags": [],
            "errorClass": "response_not_object", "modelPredictionsHumanVerified": False,
        }
    activity = raw.get("activity")
    shown_activity = activity if isinstance(activity, str) and activity in ACTIVITIES else "invalid"
    categories = raw.get("categories")
    candidates = categories if isinstance(categories, list) and len(categories) <= 12 else []
    known, unknown = [], []
    for cat in candidates:
        tag = cat.get("tag") if isinstance(cat, dict) else None
        if not isinstance(tag, str) or tag not in KNOWN_TAGS:
            unknown.append("unknown_or_ill_typed")
            continue
        known.append(tag)
    dupes = sorted(tag for tag, count in Counter(known).items() if count > 1)
    incompatible = sorted({tag for tag in known if tag not in TAGS.get(shown_activity, ())})
    in_sport = sorted({tag for tag in known if tag in TAGS.get(shown_activity, ())})
    try:
        validate_proposal(raw)
    except (ValueError, TypeError, KeyError):
        valid = False
    else:
        valid = True
    if valid:
        error = "none_new_response_valid"
    elif dupes and incompatible:
        error = "duplicate_and_cross_sport"
    elif incompatible:
        error = "cross_sport"
    elif dupes:
        error = "duplicate"
    elif unknown:
        error = "unknown_tag"
    else:
        error = "other_schema_problem"
    return {
        "status": "valid_on_repeat" if valid else "still_invalid_on_repeat",
        "activityProposal": shown_activity,
        "validCategoryHints": in_sport,
        "crossSportTags": incompatible,
        "duplicateTags": dupes,
        "unknownTags": sorted(set(unknown)),
        "errorClass": error,
        "modelPredictionsHumanVerified": False,
        "hintsAuthorizedForReelRanking": False,
        "automaticEditingAuthorized": False,
    }


def rerun_failed_preview(video, *, model: str, transport=None) -> dict:
    """Repeat exactly the old seven-still request and retain only safe diagnostics."""
    from .studio_preview_judge import valid_local_model
    if not valid_local_model(model):
        raise ValueError("Invalid local model name")
    images, stamps = temporal._samples(video)
    if len(images) != 7 or len(stamps) != 7:
        raise ValueError("Seven sampled images required")
    request = {
        "model": model, "stream": False, "format": json_schema(),
        "options": {"temperature": 0},
        "messages": [{"role": "user", "content": POLICY, "images": images}],
    }
    response = ollama._send_local(request, transport=transport)
    try:
        raw = json.loads(response["message"]["content"])
    except (ValueError, TypeError, KeyError):
        raw = None
    inspected = inspect_response(raw)
    inspected["sampledProxyTimeMs"] = stamps
    inspected["originalModelResponseAvailable"] = False
    inspected["repeatMayDifferFromOriginalFailure"] = True
    return inspected


def summary_report(records: list[dict], *, failed_population: int,
                   model: str, source_plan_sha: str, completed_source_receipts: int) -> dict:
    counts = Counter(x["diagnosis"]["errorClass"] for x in records if x["status"] == "done")
    activities = Counter(x["diagnosis"]["activityProposal"] for x in records if x["status"] == "done")
    errors = Counter(x.get("stage", "unknown") for x in records if x["status"] == "failed")
    return {
        "version": VERSION, "model": model, "sourcePlanSha256": source_plan_sha,
        "originalSuccessfulReceiptsPreserved": completed_source_receipts,
        "originalFailedPopulation": failed_population,
        "attemptedReruns": len(records),
        "rerunsCompleted": sum(x["status"] == "done" for x in records),
        "rerunsFailed": sum(x["status"] == "failed" for x in records),
        "remaining": max(0, failed_population - len(records)),
        "responseConsistency": dict(sorted(counts.items())),
        "activityProposalsOnRerun": dict(sorted(activities.items())),
        "rerunErrorStages": dict(sorted(errors.items())),
        "modelResponseDiffersPossibly": True,
        "noOriginalSourceMediaOpened": True,
        "priorCheckpointReceiptsUnmodified": True,
        "promotedToHumanLabels": 0,
        "autoEditedClips": 0,
        "recoveryRecords": sorted(records, key=lambda x: x["recordId"]),
        "interpretation": (
            "These are repeat observations of previously failed previews. "
            "Original raw failed model responses were not retained; this cannot "
            "prove which conflicting tags appeared in the original generation."
        ),
    }


def offline_html(report: dict) -> str:
    """Review-only local gallery; no scripts, external requests or auto-label actions."""
    cards = []
    for record in report["recoveryRecords"]:
        rid = record["recordId"]
        d = record.get("diagnosis") or {}
        tags = ", ".join(d.get("validCategoryHints", [])) or "None"
        cross = ", ".join(d.get("crossSportTags", [])) or "None"
        dup = ", ".join(d.get("duplicateTags", [])) or "None"
        cards.append(
            "<article><video controls preload='metadata' playsinline src='media/"
            + escape(rid) + ".mp4'></video><h2>"
            + escape(str(d.get("activityProposal", "not available"))) + "</h2><p>"
            + "Rerun status: " + escape(str(d.get("status", record["status"])))
            + "</p><p>In-sport hints: " + escape(tags)
            + "</p><p>Contradictory tags: " + escape(cross)
            + "</p><p>Duplicates: " + escape(dup)
            + "</p><p>Cause on rerun: " + escape(str(d.get("errorClass", record.get("stage"))))
            + "</p><small>Source " + escape(record["sourceSha256"][:12])
            + "</small></article>"
        )
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; media-src 'self' file:; style-src 'unsafe-inline'; connect-src 'none'; script-src 'none'; img-src 'none'">
<title>Klip: Model Disagreement Review</title>
<style>
:root{color-scheme:dark;background:#111920;color:#e9eff3;font:14px/1.5 system-ui}
body{max-width:1200px;margin:auto;padding:28px}.note{padding:14px;border-left:3px solid #cbb78b;background:#202d35}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:14px}
article{padding:12px;background:#1c2831;border:1px solid #3e5460;border-radius:10px}
video{width:100%;aspect-ratio:16/9;background:#050a0e}h2{font-size:16px}
p{font-size:13px;margin:7px 0}small{color:#a8bac6}
</style></head><body><h1>Klip / Category Disagreement Lab</h1>
<p class="note">Replay-only model hypotheses. These are NOT the original raw rejected
responses, and no category is verified, approved, or authorized for final
highlight ranking. Review the actual clip before assigning labels.</p>
<div class="grid">""" + "\n".join(cards) + "</div></body></html>"

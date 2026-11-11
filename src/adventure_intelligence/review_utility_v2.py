"""Goal-conditioned editorial routing for independently validated human reviews.

No source video is accessed or interpreted here. Rationale text supplies
auditable heuristics, not event ground truth, automatic cuts or permissions.
Do not commit private review exports or raw recordings.
"""
from __future__ import annotations

import re
from collections import Counter

VERSION = "klip-human-review-utility-v2"


def _has(text: str, *patterns: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def editorial_route(review: dict) -> dict:
    """Return advisory editorial routing for a previously validated review.

    Expects the normalized result from highlight_curation_review._review.
    Its choice, manifest binding and source times MUST be validated upstream.
    """
    choice = review.get("preference")
    goal = review.get("highlightGoal")
    outcome = review.get("observedOutcome")
    note = review.get("rationale") or ""
    if not isinstance(note, str):
        note = ""
    text = re.sub(r"\s+", " ", note.casefold()).strip()
    edits = set()

    if _has(text, r"\btrim\b", r"\bshorten(?:ed|ing)?\b",
            r"\bclip (?:it|this|can be|more|view|the)",
            r"\bfirst \d+ seconds?\b", r"\bend (?:the )?clip\b",
            r"\bstop (?:the clip|once|when)\b"):
        edits.add("retrim_with_verified_source_times")
    if _has(text, r"\bnext \d+[-–]\d+ seconds?\b",
            r"\bentire jump (?:run|line|sequence)\b",
            r"\bwith the clip after\b", r"\bclip leading up\b",
            r"\bsequence leading\b"):
        edits.add("inspect_adjacent_source_context")
    if _has(text, r"\bin and out of (?:the )?water\b",
            r"\bout of (?:the )?water\b", r"\bcamera leaves the water\b",
            r"\bonly (?:include )?the portions? (?:of me )?underwater\b"):
        edits.add("isolate_verified_underwater_spans")
    if _has(text, r"\bbest (?:top )?\d+\b", r"\btop \d+ (?:moments|clips)\b",
            r"\bmuch better clips\b", r"\bother better\b",
            r"\brepeat(?:ed)? clip\b", r"\bmany moments like this\b",
            r"\bmay be a repeat\b", r"\bduplicates?\b"):
        edits.add("compare_alternates_before_final_selection")
    if _has(text, r"\bpost\b", r"\bwater drops?\b", r"\breframe\b",
            r"\bpoorly filmed\b", r"\bfilm(?:ed|ing) (?:this )?poorly\b",
            r"\bneeds? (?:a bit of )?editing\b"):
        edits.add("review_nondestructive_postprocessing")
    if _has(text, r"\bshould be reversed\b", r"\bneed to be reversed\b",
            r"\breverse(?:d)? (?:the )?video\b"):
        edits.add("verify_orientation_before_reversing")
    if _has(text, r"\b2x\b", r"\bdouble speed\b"):
        edits.add("verify_speed_and_source_timing")
    if _has(text, r"\bspeed overlay\b",
            r"\boverlay(?:ed)? (?:with )?(?:my )?speed\b",
            r"\bslopes track(?:ing)?\b", r"\bspeeds? and stats\b"):
        edits.add("consider_telemetry_overlay_only_if_timestamp_matched")
    if choice == "skip" and _has(text, r"\bdelete\b", r"\btrash\b", r"\berase\b"):
        edits.add("manual_only_discard_request")

    # An athletic_skill default is not proof of an athletic editorial goal.
    # Explicit rationale and personal_memory goal take precedence.
    conflict = choice == "keep" and _has(
        text, r"\bnot worth keeping\b", r"\bnot highlighting\b",
        r"\bshould (?:be )?skip(?:ped)?\b")
    failure = outcome == "failed" or _has(
        text, r"\bcrash(?:ed|ing)?\b", r"\bi fall\b", r"\bfailure reels?\b")
    technique = _has(text, r"\btechnique\b", r"\bform\b",
                     r"\banaly[sz]e\b", r"\bfeedback\b",
                     r"\bimprov(?:e|ement)\b", r"\bfailure reels?\b")
    memory = goal == "personal_memory" or _has(
        text, r"\bpersonal memor(?:y|ies)\b",
        r"\bwith (?:my )?(?:family|sister|brother|parents)\b",
        r"\bfamily kayaking\b", r"\bfirst snorkel.*family\b")
    scenic = goal == "visual_quality" or _has(
        text, r"\btransition\b", r"\bintro(?:duction)?\b",
        r"\bconclusion\b", r"\bscenic\b", r"\baesthetic view\b",
        r"\bviews? of (?:the )?lake\b")
    nature = goal == "nature_encounter" or _has(
        text, r"\b(?:eagle|manta) rays?\b", r"\brays?\b",
        r"\bsharks?\b", r"\b(?:needle|angel)fish\b", r"\bfish\b",
        r"\bcorals?\b", r"\breefs?\b", r"\bsnorkel\b", r"\bunderwater\b")

    if choice not in ("keep", "skip", "neither", "cannot_tell"):
        destination = "unresolved"
    elif choice != "keep":
        destination = "not_selected" if choice == "skip" else "unresolved"
    elif conflict:
        destination = "needs_human_routing"
        edits.add("resolve_conflicting_review_choice")
    elif failure and technique:
        destination = "technique_analysis"
    elif memory:
        destination = "personal_memory"
    elif scenic:
        destination = "scenic_transition"
    elif nature:
        destination = "nature_encounter"
    elif failure:
        destination = "technique_analysis"
    else:
        destination = "sport_action"

    if _has(text, r"\bmust highlight\b", r"\b10/10\b", r"\bkeep for sure\b"):
        priority = "explicit_top_pick"
    elif _has(text, r"\bamazing\b", r"\bperfectly\b", r"\bgreat clip\b"):
        priority = "strong_positive"
    else:
        priority = "ordinary_keep_or_unknown"

    # Multiple uses are possible, but not inferred broadly from weak keywords.
    secondary = []
    if (destination == "personal_memory" and _has(
            text, r"\bathletic achievement\b", r"\bdifficult snorkel\b")):
        secondary.append("sport_action_for_human_review")
    if (destination == "personal_memory" and _has(
            text, r"\bshowcase.*coral\b", r"\bunique.*reef\b")):
        secondary.append("nature_encounter_for_human_review")

    return {
        "version": VERSION,
        "destination": destination,
        "additionalHumanReviewUses": secondary,
        "eligibleForSportHighlightDraft": choice == "keep" and destination in (
            "sport_action", "nature_encounter", "scenic_transition"),
        "editorialPriority": priority,
        "editActions": sorted(edits),
        "humanTimeAnchorsMs": {
            "eventPeak": review.get("eventPeakMs"),
            "observedResolution": review.get("resolutionMs"),
        },
        "autoTrimAuthorized": False,
        "deleteAuthorized": False,
        "publicationAuthorized": False,
        "basis": "heuristic_rationale_routing_not_video_ground_truth",
    }


def routing_summary(validated_reviews: list[dict]) -> dict:
    routes = [editorial_route(x) for x in validated_reviews]
    return {
        "version": VERSION,
        "reviewCount": len(routes),
        "destinations": dict(sorted(Counter(
            x["destination"] for x in routes).items())),
        "editActions": dict(sorted(Counter(
            tag for x in routes for tag in x["editActions"]).items())),
        "userDataTrained": False,
    }

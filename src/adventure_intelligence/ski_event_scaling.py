"""Temporal grouping of reviewed ski features into lines and standalone edits.

Only owner-annotated point markers and an immutable source-backed run context
are used. A marker is NOT a verified takeoff/landing interval; the output is an
editable preview plan with tentative boundaries, never a final master cut.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
from statistics import median

from .ski_run_context import FEATURES, validate_owner_run

VERSION = "klip-ski-temporal-event-scaling-v1"
ACTION_FEATURES = frozenset((
    "jump", "rail", "box", "halfpipe", "tree_section", "speed_flow",
    "moguls", "turn_sequence",
))
HARD_BREAK_FEATURES = frozenset(("stopped", "lift"))
SCALES = {
    "tight": {"ratio": 1.35, "minimumGapMs": 6500, "maximumGapMs": 10000},
    "balanced": {"ratio": 1.80, "minimumGapMs": 8000, "maximumGapMs": 12000},
    "immersive": {"ratio": 2.50, "minimumGapMs": 9500, "maximumGapMs": 18000},
}
SCHEMA_FIELDS = frozenset({
    "forcedBreakAfterMs", "forcedJoinAfterMs", "secondaryFeatureMs",
    "preRollMs", "postRollMs",
})


def hints_template() -> dict:
    """User-editable preferences stay separate from the raw owner run labels."""
    return {
        "forcedBreakAfterMs": [], "forcedJoinAfterMs": [],
        "secondaryFeatureMs": [],
        "preRollMs": 3000, "postRollMs": 3000,
    }


def validate_hints(hints: dict | None, action_times: list[int]) -> dict:
    if hints is None:
        return hints_template()
    if not isinstance(hints, dict) or set(hints) != SCHEMA_FIELDS:
        raise ValueError("Editing hints must use the exact versioned field set")
    known = set(action_times)
    boundaries = set(action_times[:-1])
    result = {}
    for field, permitted in (
        ("forcedBreakAfterMs", boundaries),
        ("forcedJoinAfterMs", boundaries),
        ("secondaryFeatureMs", known),
    ):
        raw = hints[field]
        if (not isinstance(raw, list) or len(raw) > 50
                or any(type(x) is not int or x not in permitted for x in raw)
                or len(set(raw)) != len(raw)):
            raise ValueError(f"{field} must contain distinct known feature timestamps")
        result[field] = sorted(raw)
    if set(result["forcedBreakAfterMs"]) & set(result["forcedJoinAfterMs"]):
        raise ValueError("A feature boundary cannot be both force-joined and force-split")
    for field in ("preRollMs", "postRollMs"):
        x = hints[field]
        if type(x) is not int or not 0 <= x <= 12000:
            raise ValueError(f"{field} must be an integer from 0 to 12,000ms")
        result[field] = x
    return result


def _baseline(gaps: list[int]) -> int | None:
    """Lower-half cadence reduces sensitivity to one unusually long transition."""
    if not gaps:
        return None
    shortest = sorted(gaps)[:(len(gaps)+1)//2]
    return round(median(shortest))


def _threshold(scale: str, baseline: int | None) -> int:
    profile = SCALES[scale]
    if baseline is None:
        return profile["minimumGapMs"]
    return min(
        profile["maximumGapMs"],
        max(profile["minimumGapMs"], round(profile["ratio"]*baseline)),
    )


def _events(review: dict, run_start: int, run_end: int) -> tuple[list[dict], list[dict]]:
    markers = sorted(review["featureMarkers"], key=lambda x: (x["sourceMs"], x["kind"]))
    actions, breaks = [], []
    seen = set()
    for marker in markers:
        at, kind = marker["sourceMs"], marker["kind"]
        if not run_start <= at <= run_end:
            continue
        if kind in ACTION_FEATURES:
            if at in seen:
                raise ValueError(
                    "Multiple action markers share a timestamp; disambiguate before grouping"
                )
            seen.add(at)
            actions.append({"sourceMs": at, "kind": kind})
        elif kind in HARD_BREAK_FEATURES:
            breaks.append({"sourceMs": at, "kind": kind})
    return actions, breaks


def _partition(events: list[dict], hard_breaks: list[dict], threshold: int,
               hints: dict) -> tuple[list[list[dict]], list[dict]]:
    if not events:
        return [], []
    clusters = [[events[0]]]
    decisions = []
    forced_break = set(hints["forcedBreakAfterMs"])
    forced_join = set(hints["forcedJoinAfterMs"])
    for prior, current in zip(events, events[1:]):
        left, right = prior["sourceMs"], current["sourceMs"]
        gap = right-left
        barriers = [x for x in hard_breaks if left < x["sourceMs"] < right]
        if barriers:
            join, reason = False, "verified_stop_or_lift_marker"
        elif left in forced_break:
            join, reason = False, "owner_forced_break"
        elif left in forced_join:
            join, reason = True, "owner_forced_join"
        else:
            join = gap <= threshold
            reason = "within_local_cadence" if join else "gap_exceeds_local_cadence"
        decisions.append({
            "afterFeatureMs": left,
            "nextFeatureMs": right,
            "gapMs": gap,
            "join": join,
            "reason": reason,
            "hardBreakMarkers": barriers,
        })
        if join:
            clusters[-1].append(current)
        else:
            clusters.append([current])
    return clusters, decisions


def _segment_bounds(groups: list[list[dict]], start: int, end: int,
                    hints: dict) -> list[dict]:
    windows = []
    pre, post = hints["preRollMs"], hints["postRollMs"]
    for i,group in enumerate(groups):
        first, last = group[0]["sourceMs"], group[-1]["sourceMs"]
        a = start if i == 0 else max(start, first-pre)
        b = min(end, last+post)
        if b <= a:
            raise ValueError("Event window is empty; check owner run boundaries")
        windows.append({
            "sourceStartMs": a, "sourceEndMs": b,
            "firstFeatureMs": first,"lastFeatureMs": last,
            "requestedPostRollMs": post,
        })
    # Avoid two independent edits overlapping when someone forces a split
    # within a short gap. The split midpoint is editorial, not event evidence.
    for i in range(len(windows)-1):
        before, after = windows[i],windows[i+1]
        midpoint = (before["lastFeatureMs"]+after["firstFeatureMs"])//2
        before["sourceEndMs"] = min(before["sourceEndMs"], midpoint)
        after["sourceStartMs"] = max(after["sourceStartMs"], midpoint)
        if (before["sourceEndMs"] <= before["sourceStartMs"]
                or after["sourceEndMs"] <= after["sourceStartMs"]):
            raise ValueError("Forced split leaves no room around an event marker")
    for item in windows:
        item["durationMs"] = item["sourceEndMs"]-item["sourceStartMs"]
        item["postRollAvailableMs"] = item["sourceEndMs"]-item["lastFeatureMs"]
        item["postRollShortfallMs"] = max(0,post-item["postRollAvailableMs"])
        item["boundaryEvidence"] = "owner_point_markers_with_heuristic_padding"
    return windows


def _build_scale(events: list[dict], barriers: list[dict],
                 run_start: int, run_end: int, hints: dict,
                 *, scale: str, baseline: int | None) -> dict:
    threshold = _threshold(scale, baseline)
    groups, gaps = _partition(events, barriers, threshold, hints)
    windows = _segment_bounds(groups,run_start,run_end,hints)
    secondary = set(hints["secondaryFeatureMs"])
    main_index = max(
        range(len(groups)),
        key=lambda i: (len(groups[i]),
                       -sum(x["sourceMs"] in secondary for x in groups[i]),
                       -groups[i][0]["sourceMs"]),
        default=None,
    )
    result=[]
    for i,(group,window) in enumerate(zip(groups,windows)):
        primary = i == main_index and len(group) >= 2
        role = ("main_line_candidate" if primary else
                "single_feature_candidate" if len(group)==1 else
                "linked_line_candidate")
        if group and all(x["sourceMs"] in secondary for x in group):
            role = "secondary_feature_candidate"
        result.append({
            "groupId": f"{scale}-{i+1:02d}",
            "role": role,
            "features": group,
            "featureCount": len(group),
            "secondaryFeatureTimesMs": [
                x["sourceMs"] for x in group if x["sourceMs"] in secondary
            ],
            **window,
            "ownerConfirmedGrouping": False,
            "editingApprovalRequired": True,
        })
    return {
        "scale": scale,"cadenceJoinThresholdMs":threshold,
        "groupCount":len(result),"groups":result,"gapDecisions":gaps,
        "previewMontageOrder":[x["groupId"] for x in result],
    }


def plan_event_lines(review: dict, context: dict,
                     *, hints: dict | None=None) -> dict:
    """Three reversible scales over one human-marked complete run."""
    validate_owner_run(review, context)
    if not review["reviewerVerifiedContinuousRun"]:
        raise ValueError("Run must have reviewed boundaries before event grouping")
    if review["runKind"] == "transit":
        raise ValueError("A transit-only segment is not an athletic run")
    start,end = review["runStartMs"],review["runEndMs"]
    if start is None or end is None:
        raise ValueError("Complete reviewed start/end required")
    events, barriers = _events(review,start,end)
    if not events:
        raise ValueError("No in-run action feature markers to group")
    hints = validate_hints(hints,[e["sourceMs"] for e in events])
    gaps=[right["sourceMs"]-left["sourceMs"] for left,right in zip(events,events[1:])]
    cadence=_baseline(gaps)
    variants={
        scale:_build_scale(events,barriers,start,end,hints,scale=scale,
                           baseline=cadence)
        for scale in SCALES
    }
    balanced=variants["balanced"]
    ordered=balanced["groups"]
    transitions=[]
    for left,right in zip(ordered,ordered[1:]):
        transitions.append({
            "previousGroupId":left["groupId"],"nextGroupId":right["groupId"],
            "unselectedSourceGapMs":max(0,right["sourceStartMs"]-left["sourceEndMs"]),
            "startsAtMs":left["sourceEndMs"],"endsAtMs":right["sourceStartMs"],
            "notVerifiedAsUninteresting":True,
        })
    return {
        "version":VERSION,
        "sourceAssetSha256":review["sourceAssetSha256"],
        "anchorRecordId":review["anchorRecordId"],
        "runSourceBoundsMs":[start,end],
        "contextSourceBoundsMs":[
            context["contextSourceStartMs"], context["contextSourceEndMs"],
        ],
        "reviewedRunKind":review["runKind"],
        "ownerRunPreference":review["preference"],
        "features":events,"hardBreakMarkers":barriers,
        "consecutiveFeatureGapsMs":gaps,
        "shortGapCadenceBaselineMs":cadence,
        "gapCadenceSampleCount":len(gaps),
        "smallSampleCaution":len(gaps)<5,
        "editingHints":hints,
        "alternatives":variants,
        "recommendedScale":"balanced",
        "balancedTransitionGaps":transitions,
        "nativePtsAlignmentVerified":False,
        "videoEventsDecodedForThisPlan":False,
        "eventMarkersArePointsNotActionBoundaries":True,
        "nonDestructivePlan":True,
        "automaticFinalMasterAuthorization":False,
        "limitations":[
            "Temporal spacing alone cannot verify takeoff, landing, tricks or excitement.",
            "A long gap may be a valuable transition, not necessarily downtime.",
            "Scales are heuristic editorial alternatives; owner must choose groups and cut points.",
            "Source-relative times from re-encoded review are approximate until native frame PTS is mapped.",
        ],
    }

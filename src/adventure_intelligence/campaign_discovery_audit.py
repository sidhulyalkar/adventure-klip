"""Read-only, receipt-checked coverage audit and balanced candidate sampling.

A coarse candidate window is an UNKNOWN interval for human inspection,
not evidence of a sporting action or a positive highlight. This module reads
only frozen workspace reports and never opens/stat()s camera source paths.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import json

from .full_archive_campaign import AUDIT_VERSION, SCAN_VERSION, _load_json

VERSION = "klip-campaign-discovery-audit-v1"
ACTIVITIES = ("ski", "bike", "snorkeling", "unknown")
ROLES = ("uniform_timeline_control", "motion_change_hypothesis")


def _workspace_report(root: Path, candidate: Path) -> Path:
    target = Path(candidate).expanduser().absolute()
    if not target.is_relative_to(root):
        raise ValueError("Inputs must be inside the specified workspace")
    relative = target.relative_to(root)
    if not relative.parts or relative.parts[0] not in ("reports", "exports"):
        raise ValueError("Inputs must be from workspace reports/ or exports/")
    cursor = target
    while cursor != root:
        if cursor.is_symlink():
            raise ValueError("Symlinked report input or parent refused")
        cursor = cursor.parent
    return target


def _valid_role_window(row: dict, duration: int) -> bool:
    return (
        isinstance(row, dict)
        and row.get("role") in ROLES
        and type(row.get("startMs")) is int
        and type(row.get("endMs")) is int
        and 0 <= row["startMs"] < row["endMs"] <= duration
        and 1000 <= row["endMs"] - row["startMs"] <= 60000
    )


def _union_ms(intervals: list[tuple[int, int]]) -> int:
    total = 0
    finish = 0
    for start, end in sorted(intervals):
        if end <= finish:
            continue
        total += end - max(start, finish)
        finish = end
    return total


def inspect_discovery(workspace: Path, audit_file: Path, scan_dir: Path) -> tuple[dict, list[dict]]:
    """Check source report SHA and summarize candidate windows; never read source footage."""
    root = Path(workspace).expanduser().resolve()
    audit_path = _workspace_report(root, audit_file)
    scan_path = _workspace_report(root, Path(scan_dir) / "scan-state.json")
    audit, audit_sha = _load_json(audit_path)
    state, state_sha = _load_json(scan_path)
    if (
        audit.get("version") != AUDIT_VERSION
        or audit.get("workspace") != str(root)
        or not isinstance(audit.get("assets"), list)
        or audit.get("catalogAssetCount") != len(audit["assets"])
        or state.get("version") != SCAN_VERSION
        or state.get("campaignAuditSha256") != audit_sha
        or not isinstance(state.get("assets"), dict)
    ):
        raise ValueError("Frozen audit and scan checkpoint are incompatible")
    by_id = {a["sourceAssetSha256"]: a for a in audit["assets"]}
    if len(by_id) != len(audit["assets"]) or not set(state["assets"]).issubset(by_id):
        raise ValueError("Duplicate or unknown source in frozen campaign")

    counts = Counter()
    by_activity = {name: Counter() for name in ACTIVITIES}
    proposals = []
    full_duration = 0
    verified_duration = 0
    proposed_union_ms = 0
    for aid, item in by_id.items():
        ms = item.get("durationMsFromCatalog")
        ms = ms if type(ms) is int and ms > 0 else 0
        full_duration += ms
        activity = item.get("catalogActivity")
        activity = activity if activity in ACTIVITIES else "unknown"
        group = by_activity[activity]
        group["catalogSources"] += 1
        group["catalogDurationMs"] += ms
        entry = state["assets"].get(aid)
        if entry is None:
            counts["unattempted"] += 1
            group["unattempted"] += 1
            continue
        status = entry.get("status")
        if status == "failed":
            counts["failed"] += 1
            group["failed"] += 1
            continue
        if status == "blocked_missing_local_media":
            counts["blocked"] += 1
            group["blocked"] += 1
            continue
        if status != "coarse_completed":
            raise ValueError("Unexpected scan checkpoint source status")
        receipt_path = scan_path.parent / "per-source" / (aid + ".json")
        try:
            receipt_path = _workspace_report(root, receipt_path)
            receipt, digest = _load_json(receipt_path)
            valid = (
                digest == entry.get("reportSha256")
                and receipt.get("version") == SCAN_VERSION
                and receipt.get("sourceAssetSha256") == aid
                and receipt.get("verifiedOriginalSha256") == aid
                and receipt.get("shaVerifiedDuringThisScan") is True
                and receipt.get("sourceDurationMs") == ms
                and receipt.get("originalSizeBytes") == item.get("sourceBytesFromCatalog")
                and isinstance(receipt.get("candidateHypotheses"), list)
            )
            if not valid:
                raise ValueError("Missing or inconsistent SHA-backed source receipt")
            windows = receipt["candidateHypotheses"]
            if any(not _valid_role_window(w, ms) for w in windows):
                raise ValueError("Malformed/unbounded candidate proposal")
        except (OSError, ValueError, KeyError, TypeError):
            counts["invalidReceipts"] += 1
            group["invalidReceipts"] += 1
            continue
        counts["verifiedCompleted"] += 1
        verified_duration += ms
        group["verifiedCompleted"] += 1
        group["verifiedDurationMs"] += ms
        intervals = []
        for w in windows:
            role = w["role"]
            counts["windows"] += 1
            counts[role] += 1
            group[role] += 1
            intervals.append((w["startMs"], w["endMs"]))
            proposals.append({
                "sourceAssetSha256": aid,
                "catalogActivity": activity,
                "startMs": w["startMs"],
                "endMs": w["endMs"],
                "role": role,
                "motionChangeEvidenceForAuditOnly": w.get("motionChangeEvidence"),
                "observedEvent": "unknown",
                "humanApprovalRequired": True,
            })
        if intervals:
            group["sourcesWithAnyProposal"] += 1
        if any(w["role"] == "uniform_timeline_control" for w in windows):
            group["sourcesWithUniformControl"] += 1
        if any(w["role"] == "motion_change_hypothesis" for w in windows):
            group["sourcesWithMotionProposal"] += 1
        union = _union_ms(intervals)
        proposed_union_ms += union
        group["proposedUnionMs"] += union
    summary = {
        "version": VERSION,
        "auditSha256": audit_sha,
        "scanStateSha256": state_sha,
        "catalogSourceCount": len(by_id),
        "verifiedCompletedSources": counts["verifiedCompleted"],
        "failedSources": counts["failed"],
        "blockedSources": counts["blocked"],
        "invalidReceipts": counts["invalidReceipts"],
        "unattemptedSources": counts["unattempted"],
        "totalCatalogDurationMs": full_duration,
        "verifiedScanDurationMs": verified_duration,
        "proposedWindowUnionMs": proposed_union_ms,
        "proposedWindowCoveragePercentOfCatalog": (
            round(proposed_union_ms * 100 / full_duration, 2)
            if full_duration else 0.0
        ),
        "proposedWindowCoveragePercentOfVerifiedSources": (
            round(proposed_union_ms * 100 / verified_duration, 2)
            if verified_duration else 0.0
        ),
        "proposalCount": counts["windows"],
        "uniformControls": counts["uniform_timeline_control"],
        "motionChangeHypotheses": counts["motion_change_hypothesis"],
        "catalogActivityStrata": {k: dict(by_activity[k]) for k in ACTIVITIES},
        "fullArchiveReadyForHumanPacket": (
            bool(by_id) and counts["verifiedCompleted"] == len(by_id)
            and counts["failed"] == counts["blocked"] == counts["invalidReceipts"] == 0
        ),
        "sourceMediaRead": False,
        "candidateSemantics": "unverified interval hypotheses; never positive/negative event labels",
        "coverageMeaning": (
            "Fraction of source-video timeline spanned by merged candidate windows, "
            "NOT fraction of frames decoded or measured event recall"
        ),
    }
    return summary, proposals


def _intersection_ratio(a: dict, b: dict) -> float:
    overlap = max(0, min(a["endMs"], b["endMs"])
                  - max(a["startMs"], b["startMs"]))
    return overlap / min(a["endMs"] - a["startMs"], b["endMs"] - b["startMs"])


def choose_balanced_review(proposals: list[dict], *, limit: int = 120,
                           per_source: int = 2) -> list[dict]:
    """First cover distinct source recordings, then return to sources for a second view.

    Alternates quiet controls and motion hypotheses globally as possible;
    activity strata are round-robin, not proportional to archive frequency.
    These are human REVIEW candidates, not chosen 'highlights'.
    """
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("Window limit must be in 1..500")
    if type(per_source) is not int or not 1 <= per_source <= 8:
        raise ValueError("Per-source budget must be in 1..8")
    by_source = defaultdict(list)
    activity_by_source = {}
    for item in proposals:
        aid = item["sourceAssetSha256"]
        activity = item["catalogActivity"]
        if activity not in ACTIVITIES:
            raise ValueError("Unrecognized activity stratum")
        previous = activity_by_source.setdefault(aid, activity)
        if previous != activity:
            raise ValueError("Inconsistent activity for one source SHA")
        by_source[aid].append(item)

    groups = {activity: sorted(
        [aid for aid, label in activity_by_source.items() if label == activity]
    ) for activity in ACTIVITIES}
    selected = []
    selected_by_source = defaultdict(list)
    role_counts = Counter()
    seen = set()
    for _round in range(per_source):
        # A single round gives at most one window from every eligible source.
        queues = {key: list(values) for key, values in groups.items()}
        while any(queues.values()) and len(selected) < limit:
            for activity in ACTIVITIES:
                if not queues[activity] or len(selected) >= limit:
                    continue
                aid = queues[activity].pop(0)
                options = [
                    w for w in by_source[aid]
                    if (aid, w["startMs"], w["endMs"]) not in seen
                    and all(_intersection_ratio(w, old) < .70
                            for old in selected_by_source[aid])
                ]
                if not options:
                    continue
                # Prefer less-used role globally; preserve high/low-motion balance.
                options.sort(key=lambda w: (
                    role_counts[w["role"]],
                    0 if w["role"] == "uniform_timeline_control" else 1,
                    w["startMs"],
                    w["endMs"],
                ))
                chosen = options[0]
                selected.append(chosen.copy())
                selected_by_source[aid].append(chosen)
                role_counts[chosen["role"]] += 1
                seen.add((aid, chosen["startMs"], chosen["endMs"]))
    return selected


def discovery_selection_report(summary: dict, candidates: list[dict],
                               *, limit: int = 120, per_source: int = 2) -> dict:
    selection = choose_balanced_review(candidates, limit=limit,
                                       per_source=per_source)
    counts = Counter(x["catalogActivity"] for x in selection)
    roles = Counter(x["role"] for x in selection)
    sources = len({x["sourceAssetSha256"] for x in selection})
    return {
        "version": VERSION,
        "auditSha256": summary["auditSha256"],
        "scanStateSha256": summary["scanStateSha256"],
        "status": ("qualified_full_archive_candidate_roster"
                   if summary["fullArchiveReadyForHumanPacket"]
                   else "exploratory_partial_scan_roster_do_not_freeze_as_final"),
        "requestedWindows": limit,
        "perSourceLimit": per_source,
        "selectedWindows": len(selection),
        "selectedDistinctSources": sources,
        "selectedActivityStrata": dict(sorted(counts.items())),
        "selectedRoles": dict(sorted(roles.items())),
        "rows": selection,
        "originalMediaRead": False,
        "reviewApprovalInferred": False,
        "warning": (
            "This is a review-sampling proposal, not a semantic highlight ranker "
            "or verified playable media packet. Source and scan fingerprints "
            "must be checked again before generating previews."
        ),
    }

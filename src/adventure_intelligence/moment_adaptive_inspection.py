"""Plan bounded, source-linked dense *inspection* of existing clip proposals.

This planner ONLY reads catalog SQLite metadata, versioned ranking JSON and
checksum-verified cached semantic artifacts. It never opens/hashes videos,
decodes frames, runs inference or modifies data. Requested frame times are
future work, not observed decoded PTS or verified action boundaries.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sqlite3
from statistics import median

from .encoder_dataset import safe_training_workspace
from .moment_clip_ranking import VERSION as RANK_VERSION
from .scene_atlas import frames_for_asset, similarity, unit_vector

VERSION = "klip-kmoment-adaptive-inspection-plan-v1"
REPORT_NAME = "kmoment-clip-ranking-v1.json"
SHA = re.compile(r"^[a-f0-9]{64}$")
SPORTS = ("ski", "bike", "snorkeling", "unknown")
PADDING_MS = {
    "ski": (3500, 4000),
    "bike": (3500, 4000),
    "snorkeling": (5500, 6500),
    "unknown": (4500, 4500),
}
# Multi-scale inspection is a decode *request*: 2 fps context, 4 fps core.
# It does NOT claim to identify the fast action, a peak or an exact edit.
CONTEXT_STEP_MS = 500
CORE_STEP_MS = 250
MAX_EXPANDED_MS = 45_000
MAX_REPORT_BYTES = 4_000_000


def _strict_fraction(value, low=0.0, high=1.0):
    return (type(value) in (int, float) and math.isfinite(value)
            and low <= value <= high)


def _load_rank_report(root: Path, path: Path | None) -> dict:
    reports = root / "reports"
    if reports.is_symlink() or not reports.is_dir():
        raise ValueError("Reports directory must be a real workspace directory")
    src = reports / REPORT_NAME if path is None else path.expanduser().absolute()
    if (src.is_symlink() or src.parent.resolve() != reports.resolve()
            or not src.is_file() or src.stat().st_size > MAX_REPORT_BYTES):
        raise ValueError("Use a regular, bounded private ranking JSON in workspace/reports")
    raw = src.read_bytes()
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("Ranking input exceeds size limit")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Malformed ranking JSON") from exc
    if (not isinstance(data, dict) or data.get("version") != RANK_VERSION
            or type(data.get("rankedCount")) is not int
            or type(data.get("explorationCount")) is not int
            or not isinstance(data.get("rankedClips"), list)
            or not isinstance(data.get("explorationClips"), list)
            or len(data["rankedClips"]) != data["rankedCount"]
            or len(data["explorationClips"]) != data["explorationCount"]
            or data["rankedCount"] > 200 or data["explorationCount"] > 200):
        raise ValueError("Unsupported ranked/exploration packet")
    return data


def _prepare_one(row: dict, mode: str, sources: dict) -> dict | None:
    if not isinstance(row, dict):
        raise ValueError("Ranking item must be an object")
    aid = row.get("sourceSha256")
    start, end = row.get("startMs"), row.get("endMs")
    sport = row.get("sportHint")
    if (not isinstance(aid, str) or not SHA.fullmatch(aid)
            or type(start) is not int or type(end) is not int
            or not 0 <= start < end <= 86_400_000 or sport not in SPORTS):
        raise ValueError("Invalid ranking item identity, bounds or sport hint")
    source = sources.get(aid)
    if source is None or source["durationMs"] is None:
        return None
    if end > source["durationMs"]:
        return None  # stale packet; never infer a replacement asset
    if mode == "exploration":
        if row.get("selectionType") != "unscored_archive_coverage_probe":
            raise ValueError("Unexpected exploration item type")
    elif not (_strict_fraction(row.get("priorityLower"),0,100)
              and _strict_fraction(row.get("priorityUpper"),0,100)
              and row["priorityLower"] <= row["priorityUpper"]
              and isinstance(row.get("eventKindProposal"),str)
              and re.fullmatch(r"[a-z][a-z0-9_.:-]{0,79}",
                               row["eventKindProposal"])):
        raise ValueError("Invalid ranked event proposal or heuristic range")
    left, right = PADDING_MS[sport]
    padded_start = max(0, start - left)
    padded_end = min(source["durationMs"], end + right)
    # A long action must be reviewed and split by a human, not truncated
    # around a guessed peak. No frame requests are planned for that item.
    if padded_end - padded_start > MAX_EXPANDED_MS:
        return {
            "status": "manual_split_required", "sourceSha256": aid,
            "coreStartMs": start, "coreEndMs": end,
            "expandedStartMs": padded_start, "expandedEndMs": padded_end,
            "sportHint": sport, "selectionType": mode,
            "requestedTimesMs": [], "plannedFrameCount": 0,
        }
    # Keep the full candidate core at 4 fps, plus lower-rate context.
    core_times = range(start, end, CORE_STEP_MS)
    context_times = list(range(padded_start, start, CONTEXT_STEP_MS))
    context_times += list(range(end, padded_end, CONTEXT_STEP_MS))
    times = sorted(set((*context_times, *core_times)))
    return {
        "status": "inspection_requested", "sourceSha256": aid,
        "coreStartMs": start, "coreEndMs": end,
        "expandedStartMs": padded_start, "expandedEndMs": padded_end,
        "sportHint": sport, "selectionType": mode,
        "eventKindProposal": (row.get("eventKindProposal")
                              if mode == "ranked" else None),
        "heuristicReviewPriority": (
            {"lower": row["priorityLower"], "upper": row["priorityUpper"]}
            if mode == "ranked" else None),
        "requestedTimesMs": times,
        "plannedFrameCount": len(times),
        "requestedFrameSchedule": {
            "contextStepMs": CONTEXT_STEP_MS,
            "candidateCoreStepMs": CORE_STEP_MS,
            "notMeasuredDecodedPts": True,
            "notVerifiedEventPeak": True,
        },
        "eventPhases": {
            "setup": None, "onset": None,
            "peak": None, "resolution": None,
        },
        "completeEventVerified": False,
        "trimReady": False,
    }


def _overlap(left: dict, right: dict) -> float:
    if left["sourceSha256"] != right["sourceSha256"]:
        return 0.0
    a = max(left["expandedStartMs"], right["expandedStartMs"])
    b = min(left["expandedEndMs"], right["expandedEndMs"])
    denom = min(left["expandedEndMs"]-left["expandedStartMs"],
                right["expandedEndMs"]-right["expandedStartMs"])
    return max(0,b-a)/max(1,denom)


def _allowed(item, selected, used, max_per_source):
    aid = item["sourceSha256"]
    return (used[aid] < max_per_source and
            all(_overlap(item, old) <= .5 for old in selected))


def _round_robin(items, chosen, used, quota, frame_budget, max_per_source):
    """Interleave sport hints; priorities never become cross-sport quality."""
    pools = defaultdict(list)
    for item in items:
        pools[item["sportHint"]].append(item)
    cursors = Counter()
    rejected_budget = 0
    while len(chosen) < quota:
        moved = False
        for sport in SPORTS:
            pool = pools[sport]
            while cursors[sport] < len(pool):
                item = pool[cursors[sport]]
                cursors[sport] += 1
                if not _allowed(item, chosen, used, max_per_source):
                    continue
                if sum(x["plannedFrameCount"] for x in chosen) + item["plannedFrameCount"] > frame_budget:
                    rejected_budget += 1
                    continue
                chosen.append(item)
                used[item["sourceSha256"]] += 1
                moved = True
                break
            if len(chosen) >= quota:
                break
        if not moved:
            break
    return rejected_budget


def _cached_context(root, db, aid, start, end, cache):
    if aid not in cache:
        active = db.execute(
            """SELECT j.filename FROM semantic_jobs j JOIN semantic_active a
            ON a.asset_id=j.asset_id AND a.config_id=j.config_id
            WHERE a.asset_id=? AND a.provider='siglip2'
            AND j.status='completed'""", (aid,)).fetchone()
        if active is not None and active["filename"]:
            path = root / "semantics" / active["filename"]
            if path.is_symlink() or path.parent.resolve() != (root/"semantics").resolve():
                raise ValueError("Unsafe semantic artifact path")
        cache[aid] = frames_for_asset(db, root, aid, "siglip2")
    frames, model_key = cache[aid]
    valid = []
    decoded_receipts = 0
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        # Requested tMs is source-relative. Decoded PTS may have an
        # unrelated stream origin; compare deltas later rather than
        # treating absolute PTS as if it were a source-relative seek.
        t = frame.get("tMs")
        vector = unit_vector(frame.get("embedding"))
        if type(t) is int and vector is not None and start <= t < end:
            valid.append((t, vector))
            if type(frame.get("decodedPtsMs")) is int:
                decoded_receipts += 1
    valid.sort(key=lambda x: x[0])
    # Do not join observations from incompatible or duplicated timestamps.
    valid = list({t:v for t,v in valid}.items())
    valid.sort()
    gaps = [b[0] - a[0] for a,b in zip(valid,valid[1:])]
    connected = sum(0 < x <= 7000 for x in gaps)
    contrast = [
        max(0.0, 1.0 - similarity(a[1], b[1]))
        for a,b in zip(valid,valid[1:])
        if 0 < b[0] - a[0] <= 30_000 and len(a[1]) == len(b[1])
    ]
    return {
        "coarseFeatureModelKey": model_key,
        "observationsInExpandedInterval": len(valid),
        "decodedPtsReceiptCount": decoded_receipts,
        "adjacentObservedPairs": len(gaps),
        "pairsConnectedAt7000Ms": connected,
        "medianAdjacentGapMs": round(median(gaps),1) if gaps else None,
        "largestSparseVisualContrast": round(max(contrast),5) if contrast else None,
        "contrastMeaning": "appearance_change_over_sparse_interval_not_event_motion",
        "gapConditionedTemporalActionInferenceSupported": connected >= 2,
        "sourceVideoReadForContext": False,
    }


def _latest_human_skips(db):
    revisions = [dict(row) for row in db.execute(
        """SELECT r.* FROM example_revisions r WHERE NOT EXISTS (
        SELECT 1 FROM example_revisions newer
        WHERE newer.example_id=r.example_id AND newer.rowid>r.rowid
        )""")]
    skips = defaultdict(list)
    for r in revisions:
        if r["highlight"] == "skip":
            skips[r["asset_id"]].append((r["start_ms"], r["end_ms"]))
    return skips


def _human_skip_overlap(item, skips):
    a,b = item["expandedStartMs"],item["expandedEndMs"]
    return any(max(a,c) < min(b,d)
               for c,d in skips.get(item["sourceSha256"], ()))


def plan_inspection(
    workspace: Path, *, rank_report: Path | None = None,
    max_clips: int = 16, frame_budget: int = 1700,
    explore_fraction: float = .25, max_per_source: int = 2,
) -> dict:
    """Prepare a read-only dense-inspection request, not a final clip edit.

    Reservations: at least a quarter of configured requests can be unscored
    exploration; selection stays source-balanced and bounded by frame budget.
    """
    if (type(max_clips) is not int or not 1 <= max_clips <= 100
            or type(frame_budget) is not int or not 64 <= frame_budget <= 8000
            or not _strict_fraction(explore_fraction, .10, .75)
            or type(max_per_source) is not int or not 1 <= max_per_source <= 4):
        raise ValueError("Invalid bounded inspection budget")
    root = safe_training_workspace(workspace)
    report = _load_rank_report(root, rank_report)
    catalog = root / "catalog.sqlite3"
    if catalog.is_symlink() or not catalog.is_file():
        raise ValueError("Missing or symlinked source catalog")
    semantics_dir = root / "semantics"
    if semantics_dir.is_symlink():
        raise ValueError("Symlinked semantic directory not trusted")
    with sqlite3.connect(catalog.as_uri()+"?mode=ro",uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        sources = {}
        for row in db.execute("SELECT id,sha256,duration FROM assets"):
            if row["id"] != row["sha256"] or not SHA.fullmatch(row["id"]):
                raise ValueError("Invalid catalog source SHA identity")
            dur = row["duration"]
            ms = (round(dur*1000)
                  if type(dur) in (float,int) and math.isfinite(dur)
                  and 0 < dur <= 86400 else None)
            sources[row["id"]] = {"durationMs": ms}
        skips = _latest_human_skips(db)
        prepared = {"ranked":[], "exploration":[]}
        manual_split_requests = []
        skipped = Counter()
        identities = set()
        for mode, key in (("ranked","rankedClips"),
                          ("exploration","explorationClips")):
            for row in report[key]:
                item = _prepare_one(row, mode, sources)
                if item is None:
                    skipped["missing_source_or_stale_interval"] += 1
                    continue
                if item["status"] == "manual_split_required":
                    skipped["long_clip_requires_manual_split"] += 1
                    manual_split_requests.append(item)
                    continue
                identity = (item["sourceSha256"],item["coreStartMs"],
                            item["coreEndMs"])
                if identity in identities:
                    skipped["duplicate_window"] += 1
                    continue
                identities.add(identity)
                if _human_skip_overlap(item, skips):
                    skipped["overlaps_human_skip"] += 1
                    continue
                prepared[mode].append(item)

        chosen, used = [], Counter()
        # Reserve actual exploration slots rather than letting event
        # proposal scores entirely suppress quiet or unfamiliar footage.
        target_explore = max(1,math.ceil(max_clips*explore_fraction))
        missed_budget = _round_robin(
            prepared["exploration"], chosen, used, target_explore,
            frame_budget, max_per_source)
        # Fill remaining places with ranked clips; don't compare numeric
        # ranking intervals across different sports as calibrated utility.
        missed_budget += _round_robin(
            prepared["ranked"], chosen, used, max_clips,
            frame_budget, max_per_source)
        # If ranked supply is small, use distinct exploratory controls.
        missed_budget += _round_robin(
            prepared["exploration"], chosen, used, max_clips,
            frame_budget, max_per_source)
        cache = {}
        for idx, item in enumerate(chosen,1):
            item["inspectionOrder"] = idx
            item["coarseContext"] = _cached_context(
                root, db, item["sourceSha256"],
                item["expandedStartMs"],item["expandedEndMs"],cache)
    return {
        "version": VERSION,
        "sourceRankingVersion": RANK_VERSION,
        "reviewGoal": "understand_full_event_before_trimming",
        "sourcesInCatalog": len(sources),
        "inputRankedProposals": len(report["rankedClips"]),
        "inputExplorationProbes": len(report["explorationClips"]),
        "maxClips": max_clips,
        "plannedClips": len(chosen),
        "distinctSources": len({i["sourceSha256"] for i in chosen}),
        "requestedFrames": sum(i["plannedFrameCount"] for i in chosen),
        "frameBudget": frame_budget,
        "requestedContextFps": 2,
        "requestedCoreFps": 4,
        "countsBySelectionType": dict(sorted(Counter(
            item["selectionType"] for item in chosen).items())),
        "countsByUnverifiedSportHint": dict(sorted(Counter(
            item["sportHint"] for item in chosen).items())),
        "skipped": {**dict(sorted(skipped.items())),
                    "frame_budget_rejections": missed_budget},
        "manualSplitRequests": manual_split_requests,
        "inspections": chosen,
        "nextStage": (
            "Explicitly approved bounded source verification and batched "
            "decode, timestamp QA, temporal event review, then cut refinement"
        ),
        "frameTimesAreRequestsNotDecodedObservations": True,
        "automaticClipSelectionOrDeletion": False,
        "eventPhaseInferencePerformed": False,
        "independentlyVerifiedOutings": False,
        "sourceVideoRead": False,
        "modelInferencePerformed": False,
        "catalogModified": False,
    }

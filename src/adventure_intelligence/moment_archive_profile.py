"""Source-free K-MOMENT archive profiling and representative pilot planning.

Only reads existing SQLite metadata and optionally a prior private judge JSON.
Never resolves original-video paths, opens media, calls models, or changes
the catalog. Capture days are NOT outings, catalog activities are NOT
verified labels, and model proposals are NOT independently annotated truth.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sqlite3
from statistics import median

from .encoder_dataset import SHA, safe_training_workspace
from .moment_archive_context import _time

VERSION = "klip-kmoment-archive-profile-v1"
MAX_ASSETS = 10000
BUCKETS = ("ski", "bike", "snorkeling", "unknown")
_VALID_CODEC = re.compile(r"^[A-Za-z0-9_.-]{1,30}$")
_FPS = re.compile(r"^([0-9]{1,8})/([0-9]{1,8})$")


def _positive(value: object, maximum: float) -> float | None:
    if (type(value) not in (int, float)
            or not math.isfinite(value) or not 0 < value <= maximum):
        return None
    return float(value)


def _quantile(numbers: list[float], q: float) -> float | None:
    if not numbers:
        return None
    ordered = sorted(numbers)
    place = (len(ordered) - 1) * q
    low, high = math.floor(place), math.ceil(place)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (place - low), 3)


def _metadata(raw: object) -> tuple[dict, bool]:
    """Allow only physical probe fields; never expose raw EXIF, paths or GPS."""
    if not isinstance(raw, str) or len(raw) > 64_000:
        return {}, False
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, UnicodeError):
        return {}, False
    if not isinstance(value, dict):
        return {}, False
    return value, True


def _fps(value: object) -> float | None:
    if type(value) in (float, int):
        rate = _positive(value, 240)
        return round(rate, 3) if rate is not None else None
    if not isinstance(value, str) or len(value) > 40:
        return None
    found = _FPS.fullmatch(value)
    if found:
        numerator, denominator = int(found[1]), int(found[2])
        if denominator == 0:
            return None
        rate = _positive(numerator / denominator, 240)
        return round(rate, 3) if rate is not None else None
    try:
        if not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,5})?", value):
            return None
        rate = _positive(float(value), 240)
        return round(rate, 3) if rate is not None else None
    except ValueError:
        return None


def _sport(value: object) -> str:
    return value if value in ("ski", "bike", "snorkeling") else "unknown"


def _sport_from_subsport(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    if value.startswith("skiing_"):
        return "ski"
    if value.startswith("mountain_biking_"):
        return "bike"
    if value.startswith("snorkeling_"):
        return "snorkeling"
    return "unknown"


def _duration_band(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    if seconds < 60:
        return "under_1m"
    if seconds < 300:
        return "1_to_5m"
    if seconds < 900:
        return "5_to_15m"
    if seconds < 1800:
        return "15_to_30m"
    return "over_30m"


def _judge_hypotheses(judge_path: Path | None, reports: Path) -> dict[str, dict]:
    if judge_path is None:
        return {}
    if judge_path.is_symlink() or judge_path.name != "proposals.json":
        raise ValueError("Judge input must be a non-symlink proposals.json")
    source = judge_path.resolve(strict=True)
    if not source.is_relative_to(reports) or source.parent == reports:
        raise ValueError("Judge report must be under the private reports directory")
    if source.stat().st_size > 16_000_000:
        raise ValueError("Judge report exceeds private input budget")
    from .ktide_study_contract import load_json
    doc, _ = load_json(source, max_bytes=16_000_000)
    if (not isinstance(doc, dict)
            or doc.get("version") != "klip-ktide-local-vision-judge-v1"
            or not isinstance(doc.get("proposals"), list)
            or doc.get("sourceCount") != len(doc["proposals"])):
        raise ValueError("Unsupported judge report")
    results = {}
    successful = 0
    for record in doc["proposals"]:
        if not isinstance(record, dict):
            raise ValueError("Invalid judge proposal row")
        key = record.get("sourceSha256")
        if not isinstance(key, str) or not SHA.fullmatch(key) or key in results:
            raise ValueError("Invalid or duplicate judge source identity")
        valid = record.get("judgeStatus") == "model_hypothesis"
        if valid:
            # Read only the model-proposed sub-sport enum, not narrative text.
            if (record.get("sourceShaVerifiedForJudge") is not True
                    or not isinstance(record.get("judgeProposal"), dict)):
                raise ValueError("Unreceipted model hypothesis")
            frames = record.get("observedFrames")
            if not isinstance(frames, list) or not 2 <= len(frames) <= 8:
                raise ValueError("Unreceipted model frame evidence")
            last_ms = -1
            for index, frame in enumerate(frames):
                if (not isinstance(frame, dict)
                        or type(frame.get("frameIndex")) is not int
                        or frame["frameIndex"] != index
                        or type(frame.get("requestedMs")) is not int
                        or frame["requestedMs"] <= last_ms
                        or not isinstance(frame.get("imageSha256"), str)
                        or not SHA.fullmatch(frame["imageSha256"])):
                    raise ValueError("Unreceipted model frame evidence")
                last_ms = frame["requestedMs"]
            successful += 1
        results[key] = {
            "judgeStatus": record.get("judgeStatus"),
            "candidateSport": (_sport_from_subsport(
                record["judgeProposal"].get("subsport")) if valid else "unknown"),
            "validationStatus": (
                record.get("judgeValidationStatus", "legacy_strict_unknown")
                if valid else "failed_or_unjudged"),
        }
    if doc.get("successes") != successful:
        raise ValueError("Judge success total is inconsistent")
    return results


def _select_pilot(rows: list[dict], n: int, seed: int) -> list[dict]:
    by_type: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_type[row["candidateSport"]].append(row)
    chosen, selected = [], set()
    month_used, duration_used = Counter(), Counter()
    for bucket in BUCKETS:
        by_type[bucket].sort(
            key=lambda r: sha256(f"{seed}:{r['sourceSha256']}".encode()).hexdigest())
    # Each pass takes one from each category, maximizing diversity of capture
    # month and duration class within that category. Unlabeled sources still
    # compete and never become training-negative labels.
    while len(chosen) < n:
        advanced = False
        for bucket in BUCKETS:
            available = [r for r in by_type[bucket] if r["sourceSha256"] not in selected]
            if not available:
                continue
            row = min(available, key=lambda r: (
                month_used[(bucket, r["captureMonthUtc"] or "unknown")],
                duration_used[(bucket, r["durationBand"])],
                sha256(f"{seed}:{r['sourceSha256']}".encode()).hexdigest(),
            ))
            selected.add(row["sourceSha256"])
            month_used[(bucket, row["captureMonthUtc"] or "unknown")] += 1
            duration_used[(bucket, row["durationBand"])] += 1
            chosen.append({
                "sourceSha256": row["sourceSha256"],
                "candidateSport": row["candidateSport"],
                "sportEvidence": row["sportEvidence"],
                "captureMonthUtc": row["captureMonthUtc"],
                "durationSeconds": row["durationSeconds"],
                "durationBand": row["durationBand"],
                "captureStyle": "unknown_not_observed",
                "verifiedIndependentOuting": False,
                "ownerConfirmedActivity": False,
                "selectionReason": "stratified_metadata_coverage_not_salience",
                "sourceMediaRead": False,
            })
            advanced = True
            if len(chosen) == n:
                break
        if not advanced:
            break
    return chosen


def audit_archive(workspace: Path, *, limit: int = 5000,
                  pilot_sources: int = 24, seed: int = 17,
                  coarse_seconds: float = 10.,
                  dense_seconds_per_source: float = 30.,
                  dense_fps: float = 8.,
                  judge_report: Path | None = None) -> dict:
    """Audit source catalog plus optional *previous* judge's labels, no media."""
    if (type(limit) is not int or not 1 <= limit <= MAX_ASSETS
            or type(pilot_sources) is not int or not 1 <= pilot_sources <= 256
            or type(seed) is not int or not 0 <= seed <= 2**31-1
            or any(_positive(v, bound) is None for v, bound in (
                (coarse_seconds, 120.), (dense_seconds_per_source, 300.),
                (dense_fps, 60.)))):
        raise ValueError("Invalid audit limit, pilot size, seed or frame budget")
    root = safe_training_workspace(workspace)
    reports = root / "reports"
    if reports.is_symlink() or not reports.is_dir():
        raise ValueError("Private reports directory required")
    catalog = root / "catalog.sqlite3"
    if catalog.is_symlink() or not catalog.is_file():
        raise ValueError("Catalog missing or symlinked")
    judge = _judge_hypotheses(judge_report, reports)
    with sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        count = db.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        if count > limit:
            raise ValueError("Catalog source count exceeds audit limit")
        assets = db.execute(
            "SELECT id, sha256, duration, size_bytes, suffix, "
            "capture_time, activity, metadata_json FROM assets ORDER BY id"
        ).fetchall()
        active = db.execute(
            "SELECT asset_id, provider FROM semantic_active"
        ).fetchall()
        # Latest revisions per independently named review target. These
        # are owner-labelled interactions but not automatically verified trips.
        examples = db.execute(
            "SELECT example_id,asset_id,decision,highlight,created_at,revision_id "
            "FROM example_revisions ORDER BY created_at,revision_id"
        ).fetchall()
        pairs = db.execute(
            "SELECT comparison_id,left_asset_id,right_asset_id,preference,"
            "created_at,revision_id FROM pairwise_revisions "
            "ORDER BY created_at,revision_id"
        ).fetchall()
    source_ids = set()
    source_rows = []
    duration_values, sizes = [], []
    years, months, days = Counter(), Counter(), set()
    activities, routes, codecs, resolutions, fps_categories = (
        Counter(), Counter(), Counter(), Counter(), Counter())
    missing = Counter()
    latest_examples = {x["example_id"]: dict(x) for x in examples}
    latest_pairs = {x["comparison_id"]: dict(x) for x in pairs}
    reviewed_ids = {
        x["asset_id"] for x in latest_examples.values()
    } | {
        aid for x in latest_pairs.values()
        for aid in (x["left_asset_id"], x["right_asset_id"])
    }
    provider_ids = defaultdict(set)
    for entry in active:
        provider = entry["provider"]
        if isinstance(provider, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", provider):
            provider_ids[provider].add(entry["asset_id"])
    for source in assets:
        aid = source["id"]
        if (not isinstance(aid, str) or not SHA.fullmatch(aid)
                or aid != source["sha256"] or aid in source_ids):
            raise ValueError("Invalid catalog content identity")
        source_ids.add(aid)
        dur = _positive(source["duration"], 86400.)
        size = _positive(source["size_bytes"], 10**13)
        stamp = _time(source["capture_time"])
        year = stamp.strftime("%Y") if stamp else None
        month = stamp.strftime("%Y-%m") if stamp else None
        day = stamp.strftime("%Y-%m-%d") if stamp else None
        if dur is None:
            missing["unusableDuration"] += 1
        else:
            duration_values.append(dur)
        if size is None:
            missing["unusableSourceSize"] += 1
        else:
            sizes.append(size)
        if not stamp:
            missing["unparseableOrNaiveCaptureTime"] += 1
        else:
            years[year] += 1
            months[month] += 1
            days.add(day)
        meta, valid_json = _metadata(source["metadata_json"])
        if not valid_json:
            missing["invalidProbeMetadata"] += 1
        width = meta.get("width")
        height = meta.get("height")
        if not (type(width) is int and type(height) is int
                and 16 <= width <= 16384 and 16 <= height <= 16384):
            width = height = None
            missing["missingOrInvalidResolution"] += 1
        resolution = f"{width}x{height}" if width else "unknown"
        resolutions[resolution] += 1
        orientation = ("landscape" if width and width > height else
                       "portrait" if width and height and width < height else
                       "square" if width and height else "unknown")
        raw_codec = meta.get("codec")
        codec = (raw_codec.lower()
                 if isinstance(raw_codec, str) and _VALID_CODEC.fullmatch(raw_codec)
                 else "unknown")
        codecs[codec] += 1
        framerate = _fps(meta.get("avg_frame_rate"))
        fps_categories[(f"{framerate:g}" if framerate is not None else "unknown")] += 1
        if framerate is None:
            missing["frameRateUnavailable"] += 1
        audio = meta.get("audio") if type(meta.get("audio")) is bool else None
        if audio is None:
            missing["audioAvailabilityUnknown"] += 1
        label = _sport(source["activity"])
        activities[label] += 1
        prior = judge.get(aid, {})
        suggested = prior.get("candidateSport", "unknown")
        routed = label if label != "unknown" else suggested
        source_evidence = (
            "catalog_activity_metadata_not_verified"
            if label != "unknown" else
            "prior_local_model_hypothesis_not_verified"
            if suggested != "unknown" else
            "unknown_needs_vision_or_owner_review"
        )
        routes[routed] += 1
        source_rows.append({
            "sourceSha256": aid,
            "catalogActivity": label,
            "candidateSport": routed,
            "sportEvidence": source_evidence,
            "captureYearUtc": year,
            "captureMonthUtc": month,
            "durationSeconds": dur,
            "durationBand": _duration_band(dur),
            "sizeBytes": int(size) if size is not None else None,
            "formatExtension": (
                source["suffix"].lower() if isinstance(source["suffix"],str)
                and re.fullmatch(r"\.[a-zA-Z0-9]{1,8}",source["suffix"])
                else "unknown"),
            "probeResolution": resolution,
            "imageOrientationHint": orientation,
            "fpsMetadata": framerate,
            "codecMetadata": codec,
            "audioPresentMetadata": audio,
            "cameraDeviceModel": None,
            "captureStyle": "unknown_not_observed",
            "reviewedByOwnerAtLeastOnce": aid in reviewed_ids,
            "hasPriorJudgeHypothesis": prior.get("judgeStatus") == "model_hypothesis",
            "matchedSemanticProvidersNotReceiptVerified": sorted(
                k for k,v in provider_ids.items() if aid in v
            ),
        })
    selected = _select_pilot(source_rows, min(pilot_sources, len(source_rows)), seed)
    possible_coarse = sum(math.ceil(x/coarse_seconds) for x in duration_values)
    dense_budget = sum(
        math.ceil(min(x["durationSeconds"] or dense_seconds_per_source,
                      dense_seconds_per_source) * dense_fps)
        for x in selected if x["durationSeconds"] is not None
    )
    return {
        "version": VERSION,
        "archiveSourceCount": count,
        "duration": {
            "sourcesWithValidDuration": len(duration_values),
            "totalHoursKnown": round(sum(duration_values)/3600, 3),
            "medianSeconds": round(median(duration_values),3) if duration_values else None,
            "p90Seconds": _quantile(duration_values, .9),
            "p95Seconds": _quantile(duration_values, .95),
            "maximumSeconds": round(max(duration_values),3) if duration_values else None,
            "unknownDurationSources": missing["unusableDuration"],
        },
        "sourceStorage": {
            "sourcesWithValidSize": len(sizes),
            "totalDecimalGBKnown": round(sum(sizes)/1e9,3),
            "unknownSizeSources": missing["unusableSourceSize"],
        },
        "captureMetadata": {
            "sourcesWithTimezoneAwareDates": count-missing["unparseableOrNaiveCaptureTime"],
            "distinctUtcCaptureDaysNotVerifiedOutings": len(days),
            "sourcesByCaptureYearUtc": dict(sorted(years.items())),
            "sourcesByCaptureMonthUtc": dict(sorted(months.items())),
            "recencyNotRateEstimate": True,
            "cameraClockAccuracyVerified": False,
        },
        "catalogActivityUnverifiedCounts": dict(sorted(activities.items())),
        "candidateSportRoutingUnverifiedCounts": dict(sorted(routes.items())),
        "probeSummaries": {
            "codec": dict(sorted(codecs.items())),
            "resolution": dict(sorted(resolutions.items())),
            "frameRate": dict(sorted(fps_categories.items())),
        },
        "metadataGaps": dict(sorted(missing.items())),
        "existingEvidenceCoverage": {
            "sourcesWithAnyPriorOwnerReview": len(reviewed_ids & source_ids),
            "latestExampleJudgments": len(latest_examples),
            "latestPairwiseJudgments": len(latest_pairs),
            "activeSemanticProviderSourceCountsNotReceiptVerified": {
                k: len(v & source_ids) for k,v in sorted(provider_ids.items())
            },
            "priorJudgeSourcesPresent": len(set(judge) & source_ids),
            "priorJudgeSuccessfulSourcesPresent": sum(
                judge.get(aid,{}).get("judgeStatus") == "model_hypothesis"
                for aid in source_ids),
            "independentlyVerifiedOutings": None,
        },
        "pilotPlan": {
            "requestedSources": pilot_sources,
            "selectedSources": len(selected),
            "selectionSeed": seed,
            "selectionMethod": "deterministic_round_robin_sport_hints_month_duration",
            "allSportLabelsRemainUnverified": True,
            "independentTripSplitEligible": False,
            "pilotCandidateCountsBySport": dict(sorted(Counter(
                r["candidateSport"] for r in selected).items())),
            "candidateSources": selected,
        },
        "illustrativeComputeBudgets": {
            "coarseStepSeconds": coarse_seconds,
            "frameReadsIfAllValidSourcesUniformlySampled": possible_coarse,
            "pilotDenseSecondsPerSourceCap": dense_seconds_per_source,
            "pilotDenseFramesPerSecond": dense_fps,
            "maximumPlannedDensePilotFrames": dense_budget,
            "doesNotPredictWallClockRuntime": True,
            "originalVideoReadForCalculation": False,
        },
        "sources": source_rows,
        "containsPrivateSourceIdentifiers": True,
        "pathsOrGpsOrFreeformProbeMetadataExported": False,
        "videoDecoded": False,
        "modelInferencePerformed": False,
        "sourceMediaRead": False,
        "catalogModified": False,
        "trainingPerformed": False,
        "editorialScoresComputed": False,
    }

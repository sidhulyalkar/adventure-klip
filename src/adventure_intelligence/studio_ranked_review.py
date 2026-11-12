"""Local, opt-in, evidence-safe ranked review feed for Klip Studio.

Converts a previously generated private K-MOMENT ranking report into the
Studio's existing review item shape. This never reruns scoring, decodes media,
updates a ranking, or writes to SQLite. Playback remains an explicit action
in the existing token-protected local Studio.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import sqlite3

from .examples import latest_examples
from .moment_clip_ranking import VERSION

SHA = re.compile(r"^[0-9a-f]{64}$")
CLIP = re.compile(r"^clip-[0-9a-f]{24}$")
MAX_REPORT_BYTES = 4_000_000
MAX_ITEMS_PER_LANE = 200
REPORT_FILENAME = "kmoment-clip-ranking-v1.json"


def _bounded_interval(row, sources):
    if not isinstance(row, dict):
        raise ValueError("Invalid ranked report item")
    aid = row.get("sourceSha256")
    start, end = row.get("startMs"), row.get("endMs")
    if (not isinstance(aid, str) or not SHA.fullmatch(aid)
            or type(start) is not int or type(end) is not int
            or start < 0 or end <= start):
        raise ValueError("Invalid ranked report source or interval")
    duration = sources.get(aid, {}).get("duration")
    if duration is None or type(duration) not in (float, int) or not duration > 0:
        return None
    if end > round(duration * 1000):
        return None
    return aid, start, end


def _reviewed(row, reviews):
    aid, start, end = row
    length = end - start
    for review in reviews.get(aid, []):
        if review["highlight"] not in ("keep", "skip"):
            continue
        intersection = max(0, min(end, review["end_ms"]) - max(start, review["start_ms"]))
        if intersection / length >= .70:
            return True
    return False


def _read_report(workspace):
    base = workspace / "reports"
    if base.is_symlink() or not base.is_dir():
        raise ValueError("Private ranking report directory unavailable")
    path = base / REPORT_FILENAME
    if path.is_symlink() or not path.is_file():
        raise ValueError("Ranking report unavailable: run scripts/klip_rank_clips.py first")
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError("Ranking report exceeds Studio read limit")
    with path.open("rb") as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError("Ranking report exceeds Studio read limit")
    try:
        report = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ValueError("Ranking report is not valid JSON") from None
    if (not isinstance(report, dict) or report.get("version") != VERSION
            or not isinstance(report.get("rankedClips"), list)
            or not isinstance(report.get("explorationClips"), list)
            or len(report["rankedClips"]) > MAX_ITEMS_PER_LANE
            or len(report["explorationClips"]) > MAX_ITEMS_PER_LANE
            or report.get("rankedCount") != len(report["rankedClips"])
            or report.get("explorationCount") != len(report["explorationClips"])):
        raise ValueError("Unsupported or inconsistent ranking report")
    return report


def studio_ranked_queue(workspace: Path) -> dict:
    """Read one precomputed packet and translate to a source-safe review feed.

    A packet may refer to an older catalog snapshot. Missing/changed sources
    are skipped, never silently replaced. Keep/Skip reviews are excluded,
    while Later/uncertain judgments remain reviewable.
    """
    root = Path(workspace).expanduser().resolve(strict=True)
    report = _read_report(root)
    dbfile = root / "catalog.sqlite3"
    if dbfile.is_symlink() or not dbfile.is_file():
        raise ValueError("Missing source catalog")
    with sqlite3.connect(dbfile.as_uri()+"?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        sources = {
            r["id"]: {
                "duration": r["duration"],
                "activity": r["activity"],
                "title": r["original_name"] or r["id"][:12],
            }
            for r in db.execute(
                "SELECT a.id,a.duration,a.activity,"
                "(SELECT i.original_name FROM imports i "
                "WHERE i.asset_id=a.id ORDER BY i.imported_at DESC LIMIT 1)"
                " AS original_name FROM assets a"
            )
        }
        review_by_asset = {}
        for r in latest_examples(db):
            review_by_asset.setdefault(r["asset_id"], []).append(r)
    result, seen = [], set()
    skipped = Counter()
    for lane, records in (
        ("ranked", report["rankedClips"]),
        ("exploration", report["explorationClips"]),
    ):
        for row in records:
            bounds = _bounded_interval(row, sources)
            if bounds is None:
                skipped["unavailable_source_or_changed_duration"] += 1
                continue
            aid, start, end = bounds
            if (aid, start, end) in seen:
                skipped["duplicate_interval"] += 1
                continue
            seen.add((aid, start, end))
            if _reviewed(bounds, review_by_asset):
                skipped["already_kept_or_skipped"] += 1
                continue
            if lane == "ranked":
                clip_id = row.get("clipId")
                priority = row.get("priorityLower")
                upper = row.get("priorityUpper")
                coverage = row.get("observableCueCoverage")
                kind = row.get("eventKindProposal")
                if (not isinstance(clip_id, str) or not CLIP.fullmatch(clip_id)
                        or type(priority) not in (int, float)
                        or type(upper) not in (int, float)
                        or not 0 <= priority <= upper <= 100
                        or type(coverage) not in (int, float)
                        or not 0 <= coverage <= 1
                        or not isinstance(kind, str)
                        or not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,79}", kind)):
                    raise ValueError("Malformed ranked review evidence")
                reasons = [
                    "Uncalibrated heuristic review priority, not clip quality",
                    "Event type and boundaries remain unverified",
                ]
                if row.get("missingCueNames"):
                    reasons.append("Some ranking evidence is unavailable")
                label = kind
                visual_priority = {
                    "lower": priority, "upper": upper,
                    "cueCoverage": coverage, "isCalibrated": False,
                }
            else:
                if row.get("selectionType") != "unscored_archive_coverage_probe":
                    raise ValueError("Malformed archive exploration item")
                clip_id = (
                    "probe-" + aid[:16] + "-" + str(start) + "-" + str(end)
                )
                label = "unverified_scene"
                reasons = [
                    "Unscored archive-coverage sample; no event identified",
                    "Do not assume this is the best moment in this recording",
                ]
                visual_priority = None
            sport = row.get("sportHint")
            if sport not in ("ski", "bike", "snorkeling", "unknown"):
                raise ValueError("Unsupported sport suggestion")
            result.append({
                "assetId": aid,
                "startMs": start, "endMs": end,
                "label": label,
                "activity": sport,
                "sourceName": sources[aid]["title"],
                "candidateId": clip_id,
                "lane": "ranked_candidate" if lane == "ranked" else "unscored_exploration",
                "selectionType": lane,
                "priorityRange": visual_priority,
                "editorialScore": None,
                "reasonCodes": [],
                "reasons": reasons,
                "evidence": {
                    "scene": None, "motion": None,
                    "audio": None, "lowValue": None,
                    "incidentFlags": [],
                },
                "trimSuggestion": None,
                "reviewState": "unreviewed",
                "requiresReview": True,
                "proposalNotVerified": True,
            })
    return {
        "schemaVersion": 1,
        "version": VERSION,
        "mode": "precomputed_ranked_review",
        "sourceCatalogCount": len(sources),
        "moments": result,
        "counts": {
            "ranked": sum(x["selectionType"] == "ranked" for x in result),
            "exploration": sum(x["selectionType"] == "exploration" for x in result),
        },
        "skipped": dict(sorted(skipped.items())),
        "rawSourceMediaRead": False,
        "modelInferencePerformed": False,
        "reportRegenerated": False,
        "automaticDeletionOrPublishing": False,
        "note": (
            "Precomputed ranking for human review. Ranked items are unverified "
            "heuristic hypotheses; exploration items have no computed score."
        ),
    }

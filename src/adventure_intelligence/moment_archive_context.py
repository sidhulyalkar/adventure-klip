"""K-MOMENT read-only cross-recording capture-time neighborhood proposals.

This is a *metadata-only* graph of candidate recording relationships.
Adjacent capture times are not location, encounter, outing, or trip truth.
Never reads camera videos, modifies catalog, rehashes originals, or sends data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import math
from pathlib import Path
import sqlite3

from .encoder_dataset import SHA, safe_training_workspace

VERSION = "klip-moment-capture-neighborhoods-v1"
MAX_SOURCES = 5000


def _time(raw):
    if not isinstance(raw, str) or len(raw) > 80:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def capture_neighborhoods(workspace: Path, *,
                          max_gap_hours: float = 12.0,
                          max_neighbors: int = 4,
                          limit: int = 3000) -> dict:
    """Propose bounded source-to-source chronology with no inferred GPS."""
    if (type(limit) is not int or not 1 <= limit <= MAX_SOURCES
            or type(max_neighbors) is not int or not 1 <= max_neighbors <= 12
            or type(max_gap_hours) not in (float, int)
            or isinstance(max_gap_hours, bool)
            or not math.isfinite(max_gap_hours)
            or not 0.1 <= max_gap_hours <= 168):
        raise ValueError("Invalid source limit, capture gap or neighbor bound")
    root = safe_training_workspace(workspace)
    dbpath = root / "catalog.sqlite3"
    if dbpath.is_symlink() or not dbpath.is_file():
        raise ValueError("Klip catalog not found or is symlinked")
    with sqlite3.connect(dbpath.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        count = db.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        if count > limit:
            raise ValueError("Catalog exceeds source limit; no partial graph generated")
        rows = db.execute(
            "SELECT id, capture_time, duration, activity FROM assets ORDER BY id"
        ).fetchall()
    dated = []
    undated = []
    unknown_duration = 0
    for row in rows:
        aid = row["id"]
        if not isinstance(aid, str) or not SHA.fullmatch(aid):
            raise ValueError("Malformed source content identity in catalog")
        stamp = _time(row["capture_time"])
        if stamp is None:
            undated.append(aid)
            continue
        duration = row["duration"]
        valid_duration = (
            type(duration) in (int, float)
            and math.isfinite(duration)
            and 0 < duration <= 86400
        )
        if not valid_duration:
            unknown_duration += 1
        dated.append({
            "sourceSha256": aid,
            "captureStart": stamp,
            "approximateCaptureEnd": (
                stamp + timedelta(seconds=duration) if valid_duration else None),
            "durationSecondsMetadata": duration if valid_duration else None,
            "catalogActivityIsUnverified": row["activity"],
        })
    dated.sort(key=lambda item: (item["captureStart"], item["sourceSha256"]))
    max_delta = timedelta(hours=max_gap_hours)
    # Each source competes for at most max_neighbors candidate recordings.
    # A pair may exist in one endpoint's list only. We explicitly do not
    # merge connected components into a trip or valid train/test group.
    related = {}
    edges = {}
    for i, left in enumerate(dated):
        candidates = []
        for j in range(i-1,-1,-1):
            other = dated[j]
            # Start-time proximity is reliable only as camera metadata.
            if left["captureStart"]-other["captureStart"] > max_delta + timedelta(days=1):
                break
            candidates.append(other)
        for j in range(i+1,len(dated)):
            other = dated[j]
            if other["captureStart"]-left["captureStart"] > max_delta + timedelta(days=1):
                break
            candidates.append(other)
        neighbors = []
        for other in candidates:
            if left["sourceSha256"] == other["sourceSha256"]:
                continue
            a, b = sorted((left, other),
                          key=lambda r:(r["captureStart"],r["sourceSha256"]))
            end = a["approximateCaptureEnd"]
            # Duration may be unknown; then use start-time proximity only.
            signed_gap = (
                (b["captureStart"]-end).total_seconds() if end is not None else None)
            start_gap = (b["captureStart"]-a["captureStart"]).total_seconds()
            # Include near ends, overlaps and start-adjacent clips.
            if (start_gap > max_gap_hours*3600
                    and (signed_gap is None or signed_gap > max_gap_hours*3600)):
                continue
            proximity = (abs(signed_gap) if signed_gap is not None
                         else abs(start_gap))
            neighbors.append((proximity, other, signed_gap, start_gap))
        neighbors.sort(key=lambda item: (item[0],item[1]["sourceSha256"]))
        related[left["sourceSha256"]] = []
        for _, other, signed_gap, start_gap in neighbors[:max_neighbors]:
            a_id, b_id = sorted((left["sourceSha256"],other["sourceSha256"]))
            edge_id = "capture-near-" + sha256((a_id + ":" + b_id).encode()).hexdigest()[:24]
            related[left["sourceSha256"]].append(edge_id)
            if edge_id not in edges:
                edges[edge_id] = {
                    "edgeId": edge_id,
                    "sourceSha256Pair": [a_id,b_id],
                    "evidenceType": "capture_metadata_temporal_proximity",
                    "sourceStartGapSeconds": round(start_gap,3),
                    "earlierRecordingEndToLaterStartGapSeconds": (
                        round(signed_gap,3) if signed_gap is not None else None),
                    "sameOuting": "unknown",
                    "sameSkiRunOrDiveSite": "unknown",
                    "sameLocation": "unknown",
                    "sharedSubjects": "unknown",
                    "capturedClockAccuracyVerified": False,
                    "humanConfirmed": False,
                    "relativeVisualAppearanceCompared": False,
                    "mediaReadForThisEdge": False,
                }
    # Include each selected link in both endpoints' neighbor references.
    adjacency = {r["sourceSha256"]: set() for r in dated}
    for edge in edges.values():
        for aid in edge["sourceSha256Pair"]:
            adjacency[aid].add(edge["edgeId"])
    return {
        "version": VERSION,
        "catalogSourceCount": len(rows),
        "sourcesWithParseableTimezoneAwareCapture": len(dated),
        "sourcesWithUnknownOrAmbiguousCaptureTime": len(undated),
        "sourcesWithUnusableDuration": unknown_duration,
        "maxGapHours": float(max_gap_hours),
        "maxNeighborsProposedPerSource": max_neighbors,
        "candidateRelationships": len(edges),
        "timeLinksOnly": True,
        "verifiedOutingGroups": 0,
        "geolocationInferred": False,
        "cameraClockErrorMeasured": False,
        "trainingGroupAssignmentsGenerated": False,
        "animalOrPersonReidentificationPerformed": False,
        "sourceVideoRead": False,
        "sourceFileRehashed": False,
        "catalogModified": False,
        "links": [edges[key] for key in sorted(edges)],
        "sourceNeighborhoods": {
            aid: sorted(refs) for aid, refs in sorted(adjacency.items())
        },
        "unlinkedOrUndatedSourceSha256": sorted(
            undated + [aid for aid, neighbors in adjacency.items() if not neighbors]
        ),
    }

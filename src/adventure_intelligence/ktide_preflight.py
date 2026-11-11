"""K-TIDE preflight of cached features, read-only, without camera-media access.

Reads the SQLite catalog in read-only mode and independently checks existing
completed semantic JSON artifact checksums. No writes, model inference, source
path resolution, or implicit grouping of recordings into independent trips.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from .scene_atlas import unit_vector

VERSION = "klip-ktide-cache-preflight-v1"


def _document(root: Path, row) -> tuple[dict | None, str]:
    if row["status"] != "completed":
        return None, "not_completed"
    filename = row["filename"]
    if not isinstance(filename, str) or not filename or Path(filename).name != filename:
        return None, "missing_or_unsafe_filename"
    base = root / "semantics"
    path = base / filename
    if base.is_symlink() or path.is_symlink() or not path.is_file():
        return None, "semantic_artifact_missing_or_symlinked"
    if path.stat().st_size > 50_000_000:
        return None, "semantic_artifact_oversized"
    try:
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != row["sha256"]:
            return None, "semantic_artifact_checksum_mismatch"
        doc = json.loads(raw)
        if (not isinstance(doc, dict) or doc.get("assetId") != row["asset_id"]
                or doc.get("configId") != row["config_id"]):
            return None, "semantic_artifact_identity_mismatch"
        return doc, "verified"
    except (OSError, ValueError, UnicodeError, TypeError):
        return None, "semantic_artifact_unreadable"


def preflight(workspace: Path, *, provider: str = "histogram", limit: int = 3000) -> dict:
    if provider not in {"histogram", "siglip2"}:
        raise ValueError("Explicit histogram or siglip2 required")
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError("Invalid audit limit")
    root = workspace.resolve(strict=True)
    catalog = root / "catalog.sqlite3"
    if catalog.is_symlink() or not catalog.is_file():
        raise ValueError("Missing catalog.sqlite3")
    with sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        if db.execute("SELECT COUNT(*) FROM assets").fetchone()[0] > limit:
            raise ValueError("Audit limit exceeded")
        assets = db.execute(
            "SELECT id, activity, duration, capture_time FROM assets ORDER BY id"
        ).fetchall()
        active = {
            (r["asset_id"], r["provider"]): r["config_id"]
            for r in db.execute("SELECT asset_id,provider,config_id FROM semantic_active")
        }
        jobs = db.execute(
            "SELECT asset_id,config_id,config_json,status,filename,sha256 "
            "FROM semantic_jobs ORDER BY asset_id,config_id"
        ).fetchall()
        frame_count = db.execute("SELECT COUNT(*) FROM semantic_frames").fetchone()[0]
    by_asset = {r["id"]: r for r in assets}
    jobs_by_provider = Counter()
    reason_counts = Counter()
    usable = []
    for job in jobs:
        try:
            config = json.loads(job["config_json"])
            kind = config.get("provider") if isinstance(config, dict) else None
        except (TypeError, ValueError):
            kind = None
        kind = kind if isinstance(kind, str) else "invalid_config"
        jobs_by_provider[(kind, job["status"])] += 1
        if kind != provider:
            continue
        doc, status = _document(root, job)
        vectors = []
        if doc is not None and doc.get("provider") == provider:
            for frame in doc.get("frames", []) if isinstance(doc.get("frames"), list) else []:
                if not isinstance(frame, dict):
                    continue
                t = frame.get("tMs")
                vec = unit_vector(frame.get("embedding"))
                if type(t) is int and t >= 0 and vec is not None:
                    vectors.append((t, len(vec)))
        elif status == "verified":
            status = "provider_mismatch"
        times = [t for t, _ in vectors]
        dims = {dim for _, dim in vectors}
        is_active = active.get((job["asset_id"], provider)) == job["config_id"]
        if status == "verified" and not is_active:
            status = "inactive_job"
        if status == "verified" and len(times) != len(set(times)):
            status = "duplicate_timestamps"
        if status == "verified" and len(dims) != 1:
            status = "invalid_or_mixed_dimensions"
        if status == "verified" and len(times) < 4:
            status = "too_few_observed_frames"
        reason_counts[status] += 1
        if status == "verified" and job["asset_id"] in by_asset:
            asset = by_asset[job["asset_id"]]
            usable.append({
                "sourceSha256": job["asset_id"],
                "activityFromCatalog": asset["activity"],
                "captureTimeMetadata": asset["capture_time"],
                "captureDateForInspectionOnly": (
                    asset["capture_time"][:10]
                    if isinstance(asset["capture_time"], str)
                    and len(asset["capture_time"]) >= 10 else None),
                "durationSeconds": asset["duration"],
                "usableFrames": len(times),
                "embeddingDimensions": next(iter(dims)),
                "firstFrameMs": min(times),
                "lastFrameMs": max(times),
                "configId": job["config_id"],
                "groupId": None,
                "tripIndependenceVerified": False,
            })
    capture_dates = Counter(r["captureDateForInspectionOnly"] or "unknown"
                            for r in usable)
    return {
        "version": VERSION,
        "provider": provider,
        "catalogSources": len(assets),
        "semanticJobs": len(jobs),
        "checkpointedSemanticFrames": frame_count,
        "jobsByProviderStatus": [
            {"provider": p, "status": s, "count": n}
            for (p, s), n in sorted(jobs_by_provider.items())],
        "selectedProviderReceiptStatuses": dict(sorted(reason_counts.items())),
        "readySources": len(usable),
        "captureDateBucketsForInspection": dict(sorted(capture_dates.items())),
        "independentTripsIdentified": False,
        "usableSources": usable,
        "rawMediaRead": False,
        "modelInferencePerformed": False,
        "workspaceCatalogModified": False,
    }

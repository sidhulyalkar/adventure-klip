"""Read-only K-TIDE cache diagnosis and explicitly scoped semantic bootstrap.

The inspect/plan paths read SQLite in *read-only* mode, inspect filesystem
metadata only, and never open camera files, import AI models, hash media, or
modify the workspace. The run path is a separately authorized operation:
Klip's existing semantic pipeline reads + SHA-verifies selected originals.
"""
from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path
import re
import sqlite3

from .encoder_dataset import SHA, safe_training_workspace
from .ktide_planner import validate_assignments

VERSION = "klip-ktide-zero-cache-bootstrap-v1"


def _ro_db(workspace: Path) -> sqlite3.Connection:
    path = workspace / "catalog.sqlite3"
    if path.is_symlink() or not path.is_file():
        raise ValueError("No safe local Klip catalog.sqlite3; refusing to create a database")
    # Query a persisted Klip catalog without opening journal write handles.
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def _provider(config_json: object) -> str:
    try:
        parsed = json.loads(config_json)
        name = parsed.get("provider")
        return name if isinstance(name, str) and name else "unknown"
    except (TypeError, ValueError, AttributeError):
        return "invalid_config"


def _source_candidates(db: sqlite3.Connection, aid: str) -> list[Path]:
    row = db.execute(
        "SELECT stored_path FROM assets WHERE id=?", (aid,)
    ).fetchone()
    if row is None:
        return []
    names = [row["stored_path"]]
    names += [r["original_path"] for r in db.execute(
        "SELECT original_path FROM imports WHERE asset_id=? ORDER BY original_path",
        (aid,),
    )]
    paths = []
    for name in dict.fromkeys(names):
        if isinstance(name, str) and name.startswith("/"):
            paths.append(Path(name))
    return paths


def _metadata_available_path(db: sqlite3.Connection, aid: str) -> Path | None:
    """Metadata only. This is NOT fresh source SHA-256 verification."""
    for path in _source_candidates(db, aid):
        try:
            if path.is_file() and not path.is_symlink():
                return path
        except (OSError, ValueError):
            pass
    return None


def inspect_workspace(workspace: Path, *, candidates: int = 16) -> dict:
    """Discover why a populated catalog might have zero training-ready caches."""
    if type(candidates) is not int or not 0 <= candidates <= 100:
        raise ValueError("candidates must be 0..100")
    root = safe_training_workspace(workspace)
    with _ro_db(root) as db:
        assets = db.execute(
            "SELECT id,activity,duration,size_bytes,capture_time "
            "FROM assets ORDER BY id"
        ).fetchall()
        jobs = db.execute(
            "SELECT asset_id,config_id,config_json,status,filename "
            "FROM semantic_jobs"
        ).fetchall()
        active = db.execute(
            "SELECT a.asset_id,a.provider,a.config_id,j.status,j.filename "
            "FROM semantic_active a LEFT JOIN semantic_jobs j "
            "ON j.asset_id=a.asset_id AND j.config_id=a.config_id"
        ).fetchall()
        checkpoint_rows = db.execute(
            "SELECT COUNT(*) FROM semantic_frames"
        ).fetchone()[0]
        job_statuses = Counter(
            (_provider(row["config_json"]), row["status"]) for row in jobs
        )
        active_statuses = Counter(
            (row["provider"], row["status"] or "missing_job")
            for row in active
        )
        active_ids = {(r["asset_id"], r["config_id"]) for r in active}
        inactive_completed = Counter(
            _provider(r["config_json"]) for r in jobs
            if r["status"] == "completed"
            and (r["asset_id"], r["config_id"]) not in active_ids
        )
        active_missing_files = Counter()
        for row in active:
            if row["status"] == "completed":
                filename = row["filename"]
                # Never traverse symlinks; only test filename metadata.
                if (not isinstance(filename, str)
                        or not re.fullmatch(r"[a-zA-Z0-9_.-]+\.json", filename)
                        or (root / "semantics").is_symlink()
                        or (root / "semantics" / filename).is_symlink()
                        or not (root / "semantics" / filename).is_file()):
                    active_missing_files[row["provider"]] += 1
        semantic_dir = root / "semantics"
        semantic_files = (sum(1 for p in semantic_dir.glob("*.json")
                              if p.is_file() and not p.is_symlink())
                          if semantic_dir.is_dir() and not semantic_dir.is_symlink() else 0)
        # Prefer balanced preview candidates over blindly choosing the three
        # shortest or the first catalog records. IDs remain full SHA256.
        eligible = [r for r in assets
                    if isinstance(r["duration"], (int, float))
                    and not isinstance(r["duration"], bool)
                    and math.isfinite(r["duration"])
                    and 30 <= r["duration"] <= 480
                    and isinstance(r["size_bytes"], int) and r["size_bytes"] > 0]
        groups = {}
        for row in eligible:
            activity = row["activity"] or "unknown"
            groups.setdefault(activity, []).append(row)
        # Deterministic balanced round-robin; date is only a *review hint*.
        names = sorted(groups)
        chosen = []
        index = 0
        while len(chosen) < candidates:
            added = False
            for name in names:
                if index < len(groups[name]) and len(chosen) < candidates:
                    chosen.append(groups[name][index])
                    added = True
            if not added:
                break
            index += 1
        hints = []
        for row in chosen:
            path = _metadata_available_path(db, row["id"])
            hints.append({
                "sourceSha256": row["id"],
                "activityFromCatalogNotVerified": row["activity"],
                "captureTimeFromCatalogNotTripID": row["capture_time"],
                "durationSeconds": round(row["duration"], 2),
                "catalogSizeGB": round(row["size_bytes"]/1e9, 3),
                "originalPathAppearsAvailable": path is not None,
                "groupId": None,
                "goal": None,
                "subsport": None,
            })
    sig_active_completed = active_statuses.get(("siglip2", "completed"), 0)
    sig_completed = job_statuses.get(("siglip2", "completed"), 0)
    if sig_active_completed:
        reason = "active_completed_cache_exists_check_validity_sampling_and_receipts"
    elif sig_completed:
        reason = "completed_siglip2_jobs_exist_but_are_not_active"
    elif checkpoint_rows:
        reason = "semantic_checkpoints_exist_but_no_active_completed_siglip2"
    else:
        reason = "no_registered_completed_siglip2_cache"
    return {
        "version": VERSION,
        "workspace": str(root),
        "catalogSources": len(assets),
        "catalogActivityCounts": dict(sorted(Counter(
            str(r["activity"] or "unknown") for r in assets).items())),
        "semanticJobs": len(jobs),
        "jobsByProviderStatus": [
            {"provider": p, "status": status, "count": n}
            for (p, status), n in sorted(job_statuses.items())
        ],
        "activeByProviderStatus": [
            {"provider": p, "status": status, "count": n}
            for (p, status), n in sorted(active_statuses.items())
        ],
        "inactiveCompletedByProvider": dict(sorted(inactive_completed.items())),
        "activeCompletedCacheFileMissingByProvider": dict(sorted(active_missing_files.items())),
        "partialSemanticFrameCheckpointRows": checkpoint_rows,
        "semanticJsonFilesOnDisk": semantic_files,
        "suspectedZeroReadyCause": reason,
        "suggestedPilotSourcesForHumanSelection": hints,
        "sourcesPickedForAutomaticProcessing": 0,
        "tripAssignmentsInferred": False,
        "sourceVideoOpenedOrHashed": False,
        "semanticReceiptsRevalidated": False,
        "catalogMutated": False,
        "modelDownloaded": False,
    }


def preflight_bootstrap(workspace: Path, assignments: dict, *,
                        sample_seconds: float = 4.0, max_frames: int = 160,
                        max_sources: int = 6,
                        max_duration_minutes: float = 8.0,
                        max_total_gb: float = 30.0) -> dict:
    """Validate a *human-approved* small source roster, with no media reads."""
    root = safe_training_workspace(workspace)
    rows = validate_assignments(assignments)
    if (type(max_sources) is not int or not 3 <= max_sources <= 8
            or len(rows) > max_sources):
        raise ValueError("Source roster exceeds 3..8 source pilot cap")
    if (not isinstance(sample_seconds, (int,float)) or isinstance(sample_seconds, bool)
            or not math.isfinite(sample_seconds) or not 2 <= sample_seconds <= 7
            or type(max_frames) is not int or not 96 <= max_frames <= 256
            or not isinstance(max_duration_minutes, (float,int))
            or isinstance(max_duration_minutes, bool)
            or not math.isfinite(max_duration_minutes)
            or not 1 <= max_duration_minutes <= 20
            or not isinstance(max_total_gb, (int,float))
            or isinstance(max_total_gb, bool)
            or not math.isfinite(max_total_gb) or not .000001 <= max_total_gb <= 200):
        raise ValueError("Invalid bounded extraction parameters")
    planned = []
    bytes_total = 0
    with _ro_db(root) as db:
        for entry in rows:
            aid = entry["sourceSha256"]
            asset = db.execute(
                "SELECT duration,size_bytes FROM assets WHERE id=?", (aid,)
            ).fetchone()
            if asset is None:
                raise ValueError("Selection source missing from this workspace catalog")
            duration = asset["duration"]
            if (not isinstance(duration, (int,float))
                    or isinstance(duration, bool)
                    or not math.isfinite(duration)
                    or not 30 <= duration <= max_duration_minutes * 60):
                raise ValueError("Source exceeds pilot duration bounds")
            path = _metadata_available_path(db, aid)
            if path is None:
                raise ValueError("Selected original unavailable (SSD unmounted or path missing)")
            actual_size = path.stat().st_size
            if actual_size <= 0 or actual_size != asset["size_bytes"]:
                raise ValueError("Source size differs from registered catalog; refuse bootstrap")
            bytes_total += actual_size
            if bytes_total > max_total_gb * 1e9:
                raise ValueError("Pilot exceeds source-byte budget")
            interval_ms = max(int(sample_seconds * 1000),
                              math.ceil(duration * 1000 / max_frames))
            if interval_ms > 7000:
                raise ValueError("Planned sampling too sparse for 30s training windows")
            planned.append({
                "sourceSha256": aid,
                "groupIdOwnerDeclared": entry["groupId"],
                "subsportOwnerDeclared": entry["subsport"],
                "goalOwnerDeclared": entry["goal"],
                "durationSeconds": round(duration,2),
                "actualSizeGB": round(actual_size/1e9,3),
                "estimatedFrames": len(range(0,int(duration*1000),interval_ms)),
                "estimatedSampleIntervalMs": interval_ms,
                "originalAppearsAvailable": True,
            })
    return {
        "version": VERSION, "mode": "preflight",
        "workspace": str(root),
        "provider": "siglip2", "sampleSeconds": sample_seconds,
        "maxFramesPerSource": max_frames, "selectedSources": len(planned),
        "selectedSourceGB": round(bytes_total/1e9,3),
        "sources": planned,
        "sourceMediaRead": False,
        "sourceSha256Reverified": False,
        "sourceOriginalWillBeReadAndHashedIfRun": True,
        "externalModelWeightsMayDownloadIfRun": True,
        "workspaceOriginalsWillNotBeDeletedOrRewritten": True,
        "modelInferencePerformed": False,
    }


def run_bootstrap(workspace: Path, assignments: dict, *,
                  sample_seconds: float = 4.0, max_frames: int = 160,
                  max_sources: int = 6, max_duration_minutes: float = 8.0,
                  max_total_gb: float = 30.0) -> dict:
    """Explicit opt-in; allows source reads only on selected source identities."""
    plan = preflight_bootstrap(
        workspace, assignments, sample_seconds=sample_seconds,
        max_frames=max_frames, max_sources=max_sources,
        max_duration_minutes=max_duration_minutes, max_total_gb=max_total_gb)
    from .locking import lock_workspace
    from .semantic import generate_semantics
    ids = {r["sourceSha256"] for r in plan["sources"]}
    with lock_workspace(safe_training_workspace(workspace)):
        result = generate_semantics(
            workspace, backend="siglip2",
            sample_seconds=sample_seconds, max_frames=max_frames, asset_ids=ids)
    return {
        "version": VERSION, "mode": "explicit_selected_source_run",
        "preflight": plan, "semanticResult": result,
        "sourceMediaRead": True,
        "sourceSha256VerifiedByExistingSemanticPipeline": (
            result["processed"] + result["cached"] > 0
            and not result["errors"]
            and result["missing"] == 0
        ),
        "scope": "only_selected_source_sha256",
        "autoDeleteOrPublish": False,
        "trainingPerformed": False,
    }

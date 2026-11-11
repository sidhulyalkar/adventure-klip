"""Report locally completed semantic feature availability without video reads."""
from __future__ import annotations

from pathlib import Path
import math

from .catalog import connect
from .scene_atlas import frames_for_asset, unit_vector


def inventory_cached_features(workspace: Path, *, provider: str = "siglip2",
                              limit: int = 3000) -> dict:
    if provider not in {"siglip2", "histogram"}:
        raise ValueError("Only explicitly named completed feature providers")
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError("Invalid inventory limit")
    workspace = workspace.resolve(strict=True)
    with connect(workspace) as db:
        rows = db.execute(
            "SELECT id,duration,activity,capture_time FROM assets "
            "ORDER BY id LIMIT ?", (limit + 1,)).fetchall()
        if len(rows) > limit:
            raise ValueError("Archive exceeds inventory limit, use a bounded pilot")
        entries = []
        for row in rows:
            # frames_for_asset verifies completion/artifact SHA and uses local
            # semantic cache files. We never resolve asset stored_path here.
            frames, key = frames_for_asset(db, workspace, row["id"], provider)
            valid = []
            for frame in frames:
                t = frame.get("tMs") if isinstance(frame, dict) else None
                if type(t) is int and t >= 0 and unit_vector(frame.get("embedding")) is not None:
                    valid.append(t)
            valid = sorted(set(valid))
            duration = row["duration"]
            duration_ms = (round(duration * 1000)
                           if isinstance(duration, (int, float))
                           and not isinstance(duration, bool)
                           and math.isfinite(duration) and duration > 0 else None)
            entries.append({
                "sourceSha256": row["id"],
                "catalogActivity": row["activity"],
                "captureTimeMetadata": row["capture_time"],
                "durationMs": duration_ms,
                "featureModelKey": key,
                "usableCachedSamples": len(valid),
                "firstCachedSampleMs": valid[0] if valid else None,
                "lastCachedSampleMs": valid[-1] if valid else None,
                "likelyWindowReady": key is not None and len(valid) >= 4,
                "groupId": None,
                "goal": None,
                "subsport": None,
            })
    return {
        "version": "klip-ktide-feature-inventory-v1",
        "provider": provider, "sourcesScannedInCatalog": len(entries),
        "readySources": sum(bool(row["likelyWindowReady"]) for row in entries),
        "requiresHumanTripAndProfileAssignment": True,
        "sourceMediaRead": False, "modelInferencePerformed": False,
        "entries": entries,
    }

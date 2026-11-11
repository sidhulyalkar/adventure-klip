"""Prepare K-TIDE feature shards from *completed* local semantic caches only.

Explicit selection JSON -> bounded immutable .npz dataset. This module never
reads source video or checks source bytes. It checks that the cached semantic
document is already checksum-validated and tracks this limitation in metadata.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Mapping

from .encoder_dataset import VERSION, KEY, SHA, validate_manifest
from .scene_profiles import SUBSPORT_PRIORS
from .scene_relevance import GOAL_WEIGHTS

SELECTION_VERSION = "klip-ktide-selection-v1"
REQUIRED = {"sampleId", "sourceSha256", "groupId", "goal",
            "subsport", "startMs", "endMs"}


def validate_selection(doc: dict) -> list[dict]:
    if (not isinstance(doc, dict) or set(doc) != {"version", "selections"}
            or doc["version"] != SELECTION_VERSION):
        raise ValueError("Invalid K-TIDE selection file")
    rows = doc["selections"]
    if not isinstance(rows, list) or not 3 <= len(rows) <= 40000:
        raise ValueError("Need 3..40000 explicitly selected windows")
    ids, sources, groups = set(), {}, set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != REQUIRED:
            raise ValueError("Invalid selected window")
        for field in ("sampleId", "groupId"):
            if not isinstance(row[field], str) or not KEY.fullmatch(row[field]):
                raise ValueError("Invalid sample/group ID")
        if row["sampleId"] in ids:
            raise ValueError("Duplicate sampleId")
        ids.add(row["sampleId"])
        aid, group = row["sourceSha256"], row["groupId"]
        if not isinstance(aid, str) or not SHA.fullmatch(aid):
            raise ValueError("Invalid source SHA")
        if aid in sources and sources[aid] != group:
            raise ValueError("Source appears across multiple groups")
        sources[aid] = group
        groups.add(group)
        if (not isinstance(row["goal"], str) or not isinstance(row["subsport"], str)
                or row["goal"] not in GOAL_WEIGHTS
                or row["subsport"] not in SUBSPORT_PRIORS):
            raise ValueError("Unsupported goal/sub-sport")
        start, end = row["startMs"], row["endMs"]
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= 86_400_000):
            raise ValueError("Invalid selected source-relative window")
    if len(groups) < 3:
        raise ValueError("At least 3 user-declared independent groups needed")
    return rows


def _safe_destination(workspace: Path, dirname: str) -> Path:
    if not isinstance(dirname, str) or not re.fullmatch(r"ktide-[a-zA-Z0-9_-]{4,60}", dirname):
        raise ValueError("Invalid output dataset name")
    root = workspace.resolve(strict=True)
    report = root / "reports"
    if not report.is_dir() or report.is_symlink():
        raise ValueError("Workspace reports/ must exist and not be symlinked")
    target = report / dirname
    if target.exists() or target.is_symlink():
        raise ValueError("Refusing to replace an existing dataset folder")
    return target


def export_cached_training_dataset(workspace: Path, selection: dict, *,
                                   name: str, provider: str = "siglip2",
                                   max_frames: int = 96) -> dict:
    """Export no footage, only existing SHA-checked model features.

    Fails if any explicitly selected interval is missing adequate cached
    visual samples. Does not hallucinate/duplicate frames or interpolate gaps.
    """
    if provider not in {"siglip2", "histogram"}:
        raise ValueError("Training feature provider must be explicit cached model")
    if type(max_frames) is not int or not 4 <= max_frames <= 256:
        raise ValueError("max_frames must be 4..256")
    rows = validate_selection(selection)
    output = _safe_destination(workspace, name)
    # Import optional NumPy only in the explicit export code path.
    import numpy as np
    from .catalog import connect
    from .scene_atlas import frames_for_asset, unit_vector
    root = workspace.resolve(strict=True)
    entries = []
    cache = {}
    model_key, width = None, None
    for row in rows:
        aid = row["sourceSha256"]
        if aid not in cache:
            with connect(root) as db:
                source = db.execute("SELECT id FROM assets WHERE id=?", (aid,)).fetchone()
                if source is None:
                    raise ValueError("Selected source SHA missing from local catalog")
                frames, key = frames_for_asset(db, root, aid, provider)
            if not key or not frames:
                raise ValueError("No completed checksum-validated semantic cache for " + aid[:12])
            cache[aid] = (frames, key)
        frames, key = cache[aid]
        if model_key is None:
            model_key = key
        elif key != model_key:
            raise ValueError("Different frozen teacher model revisions in one dataset")
        usable = []
        for frame in frames:
            t = frame.get("tMs") if isinstance(frame, dict) else None
            if type(t) is not int or not row["startMs"] <= t < row["endMs"]:
                continue
            v = unit_vector(frame.get("embedding"))
            if v is None:
                continue
            pts = frame.get("decodedPtsMs")
            pts = pts if type(pts) is int and pts >= 0 else -1
            usable.append((t, pts, v))
        usable.sort(key=lambda r: r[0])
        if len({x[0] for x in usable}) != len(usable):
            raise ValueError("Duplicate source-relative frame timestamps")
        if len(usable) < 4:
            raise ValueError("Insufficient cached features for " + row["sampleId"])
        if len(usable) > max_frames:
            # Distinct observed samples only, no fabricated interpolation.
            indices = [int(i * (len(usable) - 1) / (max_frames - 1))
                       for i in range(max_frames)]
            usable = [usable[i] for i in indices]
        dims = {len(r[2]) for r in usable}
        if len(dims) != 1:
            raise ValueError("Mixed teacher feature dimensions within a clip")
        d = dims.pop()
        if width is None:
            width = d
        elif d != width:
            raise ValueError("Mismatched teacher embedding dimensions")
        entries.append((row, usable))
    manifest = {
        "version": VERSION, "featureModelKey": model_key,
        "embeddingDim": width, "samples": [],
    }
    # Do not create output until *every* selected source and cache passes.
    output.mkdir(mode=0o700)
    (output / "shards").mkdir(mode=0o700)
    for row, usable in entries:
        sid = row["sampleId"]
        safe_id = sha256(sid.encode()).hexdigest()[:24]
        shard = f"shards/{safe_id}.npz"
        dest = output / shard
        arr = np.asarray([f[2] for f in usable], dtype=np.float32)
        times = np.asarray([f[0] for f in usable], dtype=np.int64)
        pts = np.asarray([f[1] for f in usable], dtype=np.int64)
        with dest.open("xb") as file:
            np.savez_compressed(file, embeddings=arr, t_ms=times,
                                decoded_pts_ms=pts)
        digest = sha256(dest.read_bytes()).hexdigest()
        manifest["samples"].append({
            **row, "shard": shard, "shardSha256": digest,
            "sampleCount": len(usable),
        })
    validate_manifest(manifest, root=output, verify_shards=True)
    manifest_path = output / "manifest.json"
    with manifest_path.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return {
        "datasetPath": str(manifest_path), "samples": len(entries),
        "uniqueSources": len(cache),
        "frozenTeacherModelKey": model_key, "embeddingDim": width,
        "mediaFilesRead": 0, "rawOriginalsRehashed": False,
        "completedSemanticCacheRequired": True,
        "modelDownloadPerformed": False,
        "publicationOrDeletionAuthorized": False,
    }

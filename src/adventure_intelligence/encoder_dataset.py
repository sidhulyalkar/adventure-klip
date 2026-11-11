"""K-TIDE V1 local-only feature-shard contract and trip-disjoint split policy.

No raw media, inference, network requests, or training. Dataset manifests refer
only to checksum-verified local compressed NUMPY feature shards generated from
already completed semantic caches (never original recordings).
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Mapping

from .scene_profiles import SUBSPORT_PRIORS
from .scene_relevance import GOAL_WEIGHTS

VERSION = "klip-ktide-feature-dataset-v1"
SHA = re.compile(r"^[0-9a-f]{64}$")
KEY = re.compile(r"^[a-zA-Z0-9_.:-]{1,120}$")
MAX_SAMPLES = 40000
MAX_FRAMES = 256
MAX_DIMENSIONS = 8192
SAMPLE_KEYS = {
    "sampleId", "sourceSha256", "groupId", "goal", "subsport",
    "startMs", "endMs", "shard", "shardSha256", "sampleCount",
}
DATASET_KEYS = {"version", "featureModelKey", "embeddingDim", "samples"}


def _safe_file(root: Path, relative: str) -> Path:
    if (not isinstance(relative, str)
            or not re.fullmatch(r"shards/[a-f0-9]{24}\.npz", relative)):
        raise ValueError("Invalid feature shard relative path")
    path = root / relative
    if root.is_symlink() or (root / "shards").is_symlink() or path.is_symlink():
        raise ValueError("Symlinked dataset input refused")
    return path


def validate_manifest(manifest: Mapping, *, root: Path | None = None,
                      verify_shards: bool = False) -> dict:
    if (not isinstance(manifest, dict) or set(manifest) != DATASET_KEYS
            or manifest["version"] != VERSION):
        raise ValueError("Invalid K-TIDE dataset manifest")
    key, dim, samples = (
        manifest["featureModelKey"], manifest["embeddingDim"], manifest["samples"])
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise ValueError("Missing or unsafe frozen model key")
    if type(dim) is not int or not 8 <= dim <= MAX_DIMENSIONS:
        raise ValueError("Unsupported embedding dimensions")
    if not isinstance(samples, list) or not 3 <= len(samples) <= MAX_SAMPLES:
        raise ValueError("At least 3 and at most 40000 samples required")
    seen, source_to_group, paths = set(), {}, set()
    counts = Counter()
    for row in samples:
        if not isinstance(row, dict) or set(row) != SAMPLE_KEYS:
            raise ValueError("Invalid sample metadata")
        sid, group, source = row["sampleId"], row["groupId"], row["sourceSha256"]
        if (not isinstance(sid, str) or not KEY.fullmatch(sid) or sid in seen
                or not isinstance(group, str) or not KEY.fullmatch(group)
                or not isinstance(source, str) or not SHA.fullmatch(source)):
            raise ValueError("Missing, duplicated or malformed sample provenance")
        seen.add(sid)
        if source in source_to_group and source_to_group[source] != group:
            raise ValueError("One source must never appear in multiple split groups")
        source_to_group[source] = group
        if (not isinstance(row["goal"], str) or not isinstance(row["subsport"], str)
                or row["goal"] not in GOAL_WEIGHTS
                or row["subsport"] not in SUBSPORT_PRIORS):
            raise ValueError("Unrecognized goal or sub-sport")
        start, end, n = row["startMs"], row["endMs"], row["sampleCount"]
        if (type(start) is not int or type(end) is not int
                or not 0 <= start < end <= 86_400_000
                or type(n) is not int or not 4 <= n <= MAX_FRAMES):
            raise ValueError("Invalid source time or frame count")
        shard = row["shard"]
        if (not isinstance(shard, str)
                or not re.fullmatch(r"shards/[a-f0-9]{24}\.npz", shard)):
            raise ValueError("Invalid feature shard relative path")
        if shard in paths:
            raise ValueError("Duplicate shard path")
        paths.add(shard)
        if not isinstance(row["shardSha256"], str) or not SHA.fullmatch(row["shardSha256"]):
            raise ValueError("Missing shard digest")
        if root is not None:
            target = _safe_file(root, shard)
            if verify_shards:
                if not target.is_file() or target.stat().st_size > 12_000_000:
                    raise ValueError(f"Missing or oversized feature shard: {sid}")
                h = sha256()
                with target.open("rb") as file:
                    for block in iter(lambda: file.read(1024 * 1024), b""):
                        h.update(block)
                if h.hexdigest() != row["shardSha256"]:
                    raise ValueError(f"Feature shard SHA mismatch: {sid}")
        counts[group] += 1
    if len(counts) < 3:
        raise ValueError("Need >= 3 independent groups for train/val/test")
    return {"samples": len(samples), "groups": len(counts),
            "sources": len(source_to_group),
            "profiles": dict(sorted(Counter(r["subsport"] for r in samples).items())),
            "shardsSha256Checked": bool(verify_shards)}


def group_splits(manifest: dict, *, seed: int = 17) -> dict[str, str]:
    """Assign entire *declared* trip/session groups, no clip-level leakage.

    Validation/test each get max(1, 15% of groups); training gets the rest.
    Model selection may use validation only; test is held out. Group labels
    are user supplied and can be broader than one recording/outing.
    """
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError("seed must be a bounded integer")
    validate_manifest(manifest)
    groups = sorted({r["groupId"] for r in manifest["samples"]},
                    key=lambda g: sha256(f"{seed}:{g}".encode()).hexdigest())
    n = len(groups)
    holdout = max(1, round(n * .15))
    if 2 * holdout >= n:
        holdout = 1
    output = {}
    for i, group in enumerate(groups):
        output[group] = ("test" if i < holdout else
                         "val" if i < 2 * holdout else "train")
    if not {"train", "val", "test"} <= set(output.values()):
        raise ValueError("Cannot create independent holdout groups")
    return output


def load_manifest(path: Path) -> tuple[dict, str]:
    if (path.is_symlink() or not path.is_file()
            or not 2 <= path.stat().st_size <= 32_000_000):
        raise ValueError("Missing, symlinked or oversized training manifest")
    raw = path.read_bytes()
    try:
        data = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Invalid training manifest JSON") from exc
    validate_manifest(data)
    return data, sha256(raw).hexdigest()

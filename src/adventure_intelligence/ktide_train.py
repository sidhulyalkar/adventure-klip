"""Train K-TIDE locally on frozen, verified .npz feature windows.

No raw media reads, network/model downloads, pretrained weight execution or
training on derived/ambiguous review labels. Held-out trip groups are never used
for fitting or selecting hyperparameters. Opt-in torch / numpy dependencies.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import random
import re

from .encoder_dataset import (
    MAX_FRAMES, _safe_file, group_splits, load_manifest, validate_manifest,
)


def _read_shard(root: Path, row: dict, dimensions: int):
    import numpy as np
    path = _safe_file(root, row["shard"])
    if not path.is_file() or path.stat().st_size > 12_000_000:
        raise ValueError("Missing or excessive feature shard")
    # Detect mutation since preflight before each shard read.
    hasher = sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            hasher.update(block)
    if hasher.hexdigest() != row["shardSha256"]:
        raise ValueError("Feature shard changed after preflight")
    with np.load(path, allow_pickle=False) as data:
        if set(data.files) != {"t_ms", "decoded_pts_ms", "embeddings"}:
            raise ValueError("Unexpected feature shard arrays")
        t = data["t_ms"]
        pts = data["decoded_pts_ms"]
        x = data["embeddings"]
    n = row["sampleCount"]
    if (x.shape != (n, dimensions) or t.shape != (n,)
            or pts.shape != (n,)
            or x.dtype != np.dtype("float32")
            or t.dtype != np.dtype("int64") or pts.dtype != np.dtype("int64")):
        raise ValueError("Invalid embedding shard dimensions or numeric type")
    if (not np.isfinite(x).all()
            or np.any(t < row["startMs"]) or np.any(t >= row["endMs"])
            or not np.all(np.diff(t) > 0) or np.any(pts < -1)):
        raise ValueError("Nonfinite vectors, duplicate timestamps or out-of-window sample")
    norms = np.linalg.norm(x, axis=1)
    if np.any(norms < 1e-8):
        raise ValueError("Degenerate feature embedding")
    return x / norms[:, None], t


def _collate(batch, device):
    import torch
    n = len(batch)
    max_len = max(item[0].shape[0] for item in batch)
    width = batch[0][0].shape[1]
    x = torch.zeros((n, max_len, width), dtype=torch.float32)
    t = torch.zeros((n, max_len), dtype=torch.int64)
    valid = torch.zeros((n, max_len), dtype=torch.bool)
    groups = []
    for i, (features, times, group) in enumerate(batch):
        count = len(times)
        x[i, :count] = torch.from_numpy(features.copy())
        t[i, :count] = torch.from_numpy(times.copy())
        t[i, count:] = int(times[-1])
        valid[i, :count] = True
        groups.append(group)
    return x.to(device), t.to(device), valid.to(device), groups


def _batches(root, rows, dimensions, batch_size, *, rng=None, device="cpu"):
    indexed = list(rows)
    if rng is not None:
        rng.shuffle(indexed)
    for i in range(0, len(indexed), batch_size):
        selection = indexed[i:i + batch_size]
        batch = [(*_read_shard(root, row, dimensions), row["groupId"])
                 for row in selection]
        yield _collate(batch, device)


def _evaluate(model, root, rows, dimensions, batch_size, *,
              device, seed, mask_ratio):
    import torch
    from .ktide_model import self_supervised_loss
    model.eval()
    torch.manual_seed(seed)
    scores = []
    with torch.no_grad():
        for x, times, valid, groups in _batches(
                root, rows, dimensions, batch_size, device=device):
            objective, parts = self_supervised_loss(
                model, x, times, valid, groups, mask_ratio=mask_ratio)
            if not bool(torch.isfinite(objective)):
                raise ValueError("Nonfinite held-out loss")
            scores.append((float(objective), parts, len(groups)))
    total = sum(item[2] for item in scores)
    if not total:
        raise ValueError("Missing held-out samples")
    return {
        "objective": round(sum(v * n for v, _, n in scores) / total, 6),
        "maskedCosine": round(sum(p["maskedCosine"] * n for _, p, n in scores) / total, 6),
        "samples": total,
    }


def train_local(manifest_path: Path, *, workspace: Path, run_name: str,
                epochs: int = 8, batch_size: int = 8,
                learning_rate: float = 0.0003, seed: int = 17,
                mask_ratio: float = .3, hidden: int = 128,
                layers: int = 2, max_gap_ms: int = 7000,
                device: str = "auto") -> dict:
    """Train from a verified dataset under workspace reports/ with no raw media."""
    if (not isinstance(run_name, str)
            or not re.fullmatch(r"ktide-run-[A-Za-z0-9_-]{4,60}", run_name)):
        raise ValueError("Use a new ktide-run-* name")
    if (type(epochs) is not int or not 1 <= epochs <= 300
            or type(batch_size) is not int or not 2 <= batch_size <= 128
            or type(seed) is not int or not 0 <= seed < 2**32
            or not 0.05 <= mask_ratio <= .85
            or not isinstance(learning_rate, float)
            or not math.isfinite(learning_rate)
            or not 1e-6 <= learning_rate <= 0.01):
        raise ValueError("Unsafe or unsupported training hyperparameters")
    if device not in {"cpu", "cuda", "mps", "auto"}:
        raise ValueError("Unsupported training device")
    root = workspace.resolve(strict=True)
    reports = root / "reports"
    if reports.is_symlink() or not reports.is_dir():
        raise ValueError("Workspace reports directory missing or symlinked")
    dataset_root = manifest_path.parent.resolve(strict=True)
    if (manifest_path.is_symlink() or dataset_root == reports
            or not dataset_root.is_relative_to(reports)
            or any(part.is_symlink() for part in manifest_path.parents
                   if part.is_relative_to(reports))):
        raise ValueError("Training input must be in the workspace reports directory")
    output = reports / run_name
    if output.exists() or output.is_symlink():
        raise ValueError("Refusing to overwrite an existing training run")
    doc, digest = load_manifest(manifest_path)
    validate_manifest(doc, root=dataset_root, verify_shards=True)
    splits = group_splits(doc, seed=seed)
    assigned = {k: [r for r in doc["samples"] if splits[r["groupId"]] == k]
                for k in ("train", "val", "test")}
    if any(not rows for rows in assigned.values()):
        raise ValueError("Train/validation/test split missing samples")
    import numpy as np
    import torch
    from .ktide_model import KTIDEEncoder, MODEL_VERSION, self_supervised_loss
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if device == "auto":
        device = ("cuda" if torch.cuda.is_available() else
                  "mps" if torch.backends.mps.is_available() else "cpu")
    if device == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA not available")
    if device == "mps" and not torch.backends.mps.is_available():
        raise ValueError("Apple MPS backend not available")
    model = KTIDEEncoder(doc["embeddingDim"], hidden=hidden, layers=layers,
                         max_gap_ms=max_gap_ms).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate,
                                  weight_decay=.01)
    history, best, best_state, best_epoch, stale = [], float("inf"), None, 0, 0
    for epoch in range(1, epochs + 1):
        model.train()
        # Reproducible sampling, fixed by epoch. Not scientific determinism
        # across Apple MPS and CUDA kernel implementations.
        torch.manual_seed(seed + epoch)
        rng = random.Random(seed + epoch)
        losses = []
        for x, times, valid, groups in _batches(
                dataset_root, assigned["train"], doc["embeddingDim"],
                batch_size, rng=rng, device=device):
            optimizer.zero_grad(set_to_none=True)
            loss, _ = self_supervised_loss(
                model, x, times, valid, groups, mask_ratio=mask_ratio)
            if not bool(torch.isfinite(loss)):
                raise ValueError("Nonfinite training objective")
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            losses.append((float(loss.detach()), len(groups)))
        train_loss = sum(v * n for v, n in losses) / sum(n for _, n in losses)
        val = _evaluate(
            model, dataset_root, assigned["val"], doc["embeddingDim"],
            batch_size, device=device, seed=seed + 90001,
            mask_ratio=mask_ratio)
        history.append({"epoch": epoch, "trainObjective": round(train_loss, 6),
                        "valObjective": val["objective"]})
        if val["objective"] < best - 1e-5:
            best, best_epoch, stale = val["objective"], epoch, 0
            best_state = {k: v.detach().cpu().clone()
                          for k, v in model.state_dict().items()}
        else:
            stale += 1
            if stale >= 5:
                break
    if best_state is None:
        raise ValueError("No finite checkpoint selected")
    model.load_state_dict(best_state)
    test = _evaluate(
        model, dataset_root, assigned["test"], doc["embeddingDim"],
        batch_size, device=device, seed=seed + 91001,
        mask_ratio=mask_ratio)
    # Only create output after all dataset checks and training complete.
    output.mkdir(mode=0o700)
    checkpoint = output / "ktide-best.pt"
    torch.save({
        "state_dict": best_state, "modelConfig": model.config,
        "modelVersion": MODEL_VERSION,
        "teacherModelKey": doc["featureModelKey"],
        "manifestSha256": digest, "splitSeed": seed,
        "bestEpoch": best_epoch,
    }, checkpoint)
    checkpoint_sha = sha256(checkpoint.read_bytes()).hexdigest()
    report = {
        "version": "klip-ktide-training-run-v1",
        "modelVersion": MODEL_VERSION,
        "teacherModelKey": doc["featureModelKey"],
        "datasetManifestSha256": digest,
        "modelConfig": model.config,
        "device": device, "seed": seed, "maskRatio": mask_ratio,
        "trainGroups": sum(1 for v in splits.values() if v == "train"),
        "valGroups": sum(1 for v in splits.values() if v == "val"),
        "testGroups": sum(1 for v in splits.values() if v == "test"),
        "sampleCounts": {k: len(v) for k, v in assigned.items()},
        "splitAssignments": dict(sorted(splits.items())),
        "history": history, "selectedEpoch": best_epoch,
        "heldOutTest": test, "checkpointSha256": checkpoint_sha,
        "weightsTrainedOnUserReviews": False,
        "rawMediaRead": False, "featureCacheOnly": True,
        "testNotUsedForSelection": True,
        "eventRecognitionQualified": False,
        "humanPreferenceRankingQualified": False,
        "publishOrDeleteAuthorized": False,
    }
    with (output / "training-report.json").open("x", encoding="utf-8") as file:
        json.dump(report, file, indent=2, sort_keys=True)
        file.write("\n")
    return {"runDirectory": str(output), **report}

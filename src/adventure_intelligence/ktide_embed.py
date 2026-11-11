"""Inference over source-verified frozen features with a trained local K-TIDE.

Outputs learned and frozen-mean embeddings for offline retrieval comparisons.
No assertion that nearest clips share an event, animal, or athlete.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from .encoder_dataset import load_manifest, validate_manifest
from .ktide_train import _batches


def encode_dataset(manifest: Path, checkpoint: Path, *, batch_size: int = 8,
                   device: str = "cpu") -> dict:
    import torch
    from .ktide_model import KTIDEEncoder, MODEL_VERSION
    if (checkpoint.is_symlink() or not checkpoint.is_file()
            or checkpoint.stat().st_size > 200_000_000):
        raise ValueError("Missing/symlinked/oversized local checkpoint")
    doc, _ = load_manifest(manifest)
    validate_manifest(doc, root=manifest.parent, verify_shards=True)
    if device not in {"cpu", "cuda", "mps"} or not 1 <= batch_size <= 64:
        raise ValueError("Invalid device or batch size")
    if device == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA unavailable")
    if device == "mps" and not torch.backends.mps.is_available():
        raise ValueError("MPS unavailable")
    # Never unpickle arbitrary checkpoint objects. Only load trusted local
    # tensor state dictionaries with weights_only=True.
    state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    if (not isinstance(state, dict)
            or state.get("modelVersion") != MODEL_VERSION
            or state.get("teacherModelKey") != doc["featureModelKey"]):
        raise ValueError("Model version or frozen teacher mismatch")
    config = state.get("modelConfig")
    if not isinstance(config, dict) or set(config) != {
            "input_dim", "hidden", "layers", "heads", "dropout", "max_gap_ms"}:
        raise ValueError("Invalid model config")
    if config["input_dim"] != doc["embeddingDim"]:
        raise ValueError("Feature dimensions incompatible with checkpoint")
    model = KTIDEEncoder(**config)
    model.load_state_dict(state["state_dict"], strict=True)
    model.to(device).eval()
    out = []
    with torch.no_grad():
        for offset in range(0, len(doc["samples"]), batch_size):
            rows = doc["samples"][offset:offset + batch_size]
            batch = next(_batches(manifest.parent, rows, doc["embeddingDim"],
                                  len(rows), device=device))
            x, t, valid, _ = batch
            representation = model(x, t, valid)["embedding"].cpu().float()
            counts = valid.sum(dim=1).unsqueeze(-1).float()
            teacher = ((x * valid.unsqueeze(-1)).sum(dim=1) / counts)
            teacher = torch.nn.functional.normalize(teacher, dim=-1)
            teacher = teacher.cpu().float()
            for i, row in enumerate(rows):
                out.append({
                    "sampleId": row["sampleId"], "sourceSha256": row["sourceSha256"],
                    "groupId": row["groupId"],
                    "ktideEmbedding": representation[i].tolist(),
                    "meanTeacherEmbedding": teacher[i].tolist(),
                })
    return {
        "version": "klip-ktide-embedding-report-v1",
        "modelVersion": MODEL_VERSION,
        "featureModelKey": doc["featureModelKey"],
        "checkpointSha256": sha256(checkpoint.read_bytes()).hexdigest(),
        "numSamples": len(out),
        "results": out,
        "eventClaimsVerified": False, "mediaRead": False,
        "humanPreferenceRankingQualified": False,
    }

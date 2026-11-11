"""Opt-in CPU smoke test for K-TIDE. Synthetic features only."""
from hashlib import sha256
import json
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
np = pytest.importorskip("numpy")

from adventure_intelligence.encoder_dataset import VERSION, group_splits
from adventure_intelligence.ktide_model import (
    KTIDEEncoder, group_safe_contrastive, self_supervised_loss,
)
from adventure_intelligence.ktide_train import train_local


def make_features(seed, t=8, d=16):
    rng = np.random.default_rng(seed)
    # Smooth synthetic temporal stream plus small structured changes.
    x = np.cumsum(rng.normal(size=(t, d)).astype(np.float32) * .2, axis=0)
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    return (x / np.maximum(norms, 1e-6)).astype(np.float32)


def test_encoder_self_supervision_backward_works_without_target_leak():
    torch.manual_seed(7)
    encoder = KTIDEEncoder(16, hidden=32, layers=1, heads=4, dropout=0.0)
    x = torch.from_numpy(np.stack([make_features(i) for i in range(3)]))
    times = torch.arange(8).unsqueeze(0).expand(3, -1) * 1000
    valid = torch.ones(3, 8, dtype=torch.bool)
    loss, metrics = self_supervised_loss(
        encoder, x, times, valid, ["a", "b", "c"], mask_ratio=.4)
    assert torch.isfinite(loss)
    loss.backward()
    assert encoder.reconstruct.weight.grad is not None
    assert torch.isfinite(encoder.reconstruct.weight.grad).all()
    assert metrics["maskedCosine"] >= 0


def test_encoder_gap_blocks_attention_and_unobserved_transitions():
    encoder = KTIDEEncoder(16, hidden=32, layers=1, heads=4, dropout=0)
    x = torch.from_numpy(np.stack([make_features(i) for i in range(2)]))
    times = torch.tensor([[0, 1000, 2000, 15000, 16000, 17000, 18000, 19000]] * 2)
    valid = torch.ones(2, 8, dtype=torch.bool)
    result = encoder(x, times, valid)
    assert result["embedding"].shape == (2, 32)
    assert not bool(result["contiguous"][0, 3])
    assert bool(result["contiguous"][0, 4])
    # Perturb one disconnected segment; other segment must not be affected.
    changed = x.clone()
    changed[:, 4:] *= -1
    encoder.eval()
    with torch.no_grad():
        left = encoder(x, times, valid)["tokens"][:, :3]
        other = encoder(changed, times, valid)["tokens"][:, :3]
    assert torch.allclose(left, other, atol=1e-5)


def test_group_safe_contrastive_does_not_penalize_same_trip():
    z1 = torch.randn(2, 8, requires_grad=True)
    z2 = torch.randn(2, 8, requires_grad=True)
    same = group_safe_contrastive(z1, z2, ["trip", "trip"])
    assert float(same) == 0.
    different = group_safe_contrastive(z1, z2, ["trip-a", "trip-b"])
    assert float(different) >= 0


def _dataset(workspace: Path):
    root = workspace / "reports" / "ktide-synthetic-source"
    (root / "shards").mkdir(parents=True)
    rows = []
    for i in range(6):
        features = make_features(i + 32)
        times = np.arange(len(features), dtype=np.int64) * 1000 + 1000
        shard = f"shards/{i+1:024x}.npz"
        path = root / shard
        np.savez_compressed(path, embeddings=features, t_ms=times,
                            decoded_pts_ms=np.full((len(times),), -1, np.int64))
        rows.append({
            "sampleId": f"synthetic-{i}", "sourceSha256": f"{i+1:064x}",
            "groupId": f"synthetic-trip-{i}", "goal": "sport_action",
            "subsport": "skiing_speed", "startMs": 0, "endMs": 10000,
            "shard": shard, "shardSha256": sha256(path.read_bytes()).hexdigest(),
            "sampleCount": len(times),
        })
    data = {"version": VERSION, "featureModelKey": "synthetic-teacher-v0",
            "embeddingDim": 16, "samples": rows}
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps(data))
    return manifest


def test_end_to_end_one_epoch_train_and_held_out_evaluate(tmp_path):
    manifest = _dataset(tmp_path)
    result = train_local(
        manifest, workspace=tmp_path, run_name="ktide-run-synthetic",
        epochs=1, batch_size=3, learning_rate=.0003, seed=11,
        hidden=32, layers=1, device="cpu")
    assert result["sampleCounts"]["test"] >= 1
    assert result["sampleCounts"]["val"] >= 1
    assert result["sampleCounts"]["train"] >= 1
    assert result["heldOutTest"]["objective"] >= 0
    assert result["testNotUsedForSelection"]
    assert result["rawMediaRead"] is False
    assert result["humanPreferenceRankingQualified"] is False
    checkpoint = tmp_path / "reports" / "ktide-run-synthetic" / "ktide-best.pt"
    assert checkpoint.is_file()
    saved = torch.load(checkpoint, map_location="cpu", weights_only=True)
    assert saved["manifestSha256"] == sha256(manifest.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="overwrite"):
        train_local(
            manifest, workspace=tmp_path, run_name="ktide-run-synthetic",
            epochs=1, batch_size=3, learning_rate=.0003, seed=11,
            hidden=32, layers=1, device="cpu")

"""Opt-in CPU smoke test for K-TIDE. Synthetic features only."""
from hashlib import sha256
import json
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
np = pytest.importorskip("numpy")

from adventure_intelligence.encoder_dataset import VERSION, group_splits
from adventure_intelligence.ktide_model import (
    KTIDEEncoder, ORDER_OBJECTIVE_VERSION, group_safe_contrastive,
    reverse_connected_observations, self_supervised_loss,
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
    assert result["trainingObjectiveVersion"] == ORDER_OBJECTIVE_VERSION
    assert result["temporalObservability"]["threeFrameOrderEligibleWindows"] == 6
    assert result["temporalObservability"]["connectedPairFraction"] == 1.0
    assert result["temporalObservability"]["sourceMediaRead"] is False
    checkpoint = tmp_path / "reports" / "ktide-run-synthetic" / "ktide-best.pt"
    assert checkpoint.is_file()
    saved = torch.load(checkpoint, map_location="cpu", weights_only=True)
    assert saved["manifestSha256"] == sha256(manifest.read_bytes()).hexdigest()
    from adventure_intelligence.ktide_embed import encode_dataset
    encoded = encode_dataset(manifest, checkpoint, batch_size=2, device="cpu")
    assert encoded["numSamples"] == 6
    assert encoded["mediaRead"] is False
    assert len(encoded["results"][0]["ktideEmbedding"]) == 32
    assert len(encoded["results"][0]["meanTeacherEmbedding"]) == 16
    assert len(encoded["results"][0]["untrainedEmbedding"]) == 32
    assert len(encoded["results"][0]["temporalChangeEmbedding"]) == 16
    assert encoded["randomControlSeed"] == 1337
    assert encoded["humanPreferenceRankingQualified"] is False
    with pytest.raises(ValueError, match="overwrite"):
        train_local(
            manifest, workspace=tmp_path, run_name="ktide-run-synthetic",
            epochs=1, batch_size=3, learning_rate=.0003, seed=11,
            hidden=32, layers=1, device="cpu")



def test_reversal_is_strictly_within_connected_time_runs_and_reverses_visibility():
    # Two runs (3 and 5 frames) separated by a 13-second unsupported gap.
    x = torch.arange(8 * 16, dtype=torch.float32).reshape(1, 8, 16)
    t = torch.tensor([[0, 1000, 2000, 15000, 16000, 17000, 18000, 19000]])
    valid = torch.ones((1, 8), dtype=torch.bool)
    visible = torch.tensor([[True, False, True, True, True, False, True, False]])
    flipped, flip_mask, eligible = reverse_connected_observations(
        x, t, valid, visible, 7000)
    assert eligible.tolist() == [True]
    assert torch.equal(flipped[0, :3], x[0, :3].flip(0))
    assert torch.equal(flipped[0, 3:], x[0, 3:].flip(0))
    assert torch.equal(flip_mask[0, :3], visible[0, :3].flip(0))
    assert torch.equal(flip_mask[0, 3:], visible[0, 3:].flip(0))
    assert int(flip_mask.sum()) == int(visible.sum())
    assert torch.equal(t, torch.tensor([[0, 1000, 2000, 15000, 16000, 17000, 18000, 19000]]))


def test_reversal_respects_padding_and_does_not_invent_order_in_sparse_features():
    x = torch.arange(7*16, dtype=torch.float32).reshape(1,7,16)
    t = torch.tensor([[0, 12000, 24000, 36000, 48000, 48000, 48000]])
    valid = torch.tensor([[True, True, True, True, True, False, False]])
    visible = torch.tensor([[True, False, True, True, False, False, False]])
    flipped, flip_mask, eligible = reverse_connected_observations(
        x, t, valid, visible, 7000)
    assert eligible.tolist() == [False]
    assert torch.equal(flipped, x)
    assert torch.equal(flip_mask, visible)


def test_matched_mask_order_objective_cannot_cheat_by_unmasked_negative():
    torch.manual_seed(123)
    calls = []
    class Recorder(KTIDEEncoder):
        def forward(self, x, t_ms, valid, visible=None):
            calls.append((x.detach().clone(),
                          visible.detach().clone() if visible is not None else None))
            return super().forward(x, t_ms, valid, visible)
    model = Recorder(16, hidden=32, layers=1, heads=4, dropout=0)
    x = torch.from_numpy(np.stack([make_features(i) for i in range(2)]))
    t = torch.arange(8).repeat(2,1)*1000
    valid = torch.ones(2,8,dtype=torch.bool)
    loss, metrics = self_supervised_loss(
        model,x,t,valid,["trip-a","trip-b"],mask_ratio=.4)
    assert bool(torch.isfinite(loss))
    assert metrics["orderEligibleSamples"] == 2
    assert metrics["orderObjectiveVersion"] == ORDER_OBJECTIVE_VERSION
    assert len(calls) == 3  # masked view 1, masked view 2, matched reversed view
    a_x, a_mask = calls[0]
    reverse_x, reverse_mask = calls[2]
    assert torch.equal(a_x, x)
    assert torch.equal(reverse_x, x.flip(1))
    assert torch.equal(reverse_mask, a_mask.flip(1))
    assert reverse_mask.sum() == a_mask.sum()


def test_order_loss_abstains_when_all_observation_gaps_exceed_threshold():
    torch.manual_seed(77)
    calls = []
    class Recorder(KTIDEEncoder):
        def forward(self, x, t_ms, valid, visible=None):
            calls.append(visible)
            return super().forward(x, t_ms, valid, visible)
    model = Recorder(16,hidden=32,layers=1,heads=4,dropout=0)
    x = torch.from_numpy(np.stack([make_features(i) for i in range(2)]))
    t = torch.arange(8).repeat(2,1)*12000
    valid = torch.ones(2,8,dtype=torch.bool)
    total, parts = self_supervised_loss(model,x,t,valid,["a","b"])
    assert torch.isfinite(total)
    assert parts["orderEligibleSamples"] == 0
    assert parts["orderControl"] == 0.
    assert len(calls) == 2  # No fake chronological supervision.


def test_read_only_temporal_audit_uses_only_sha_checked_shards(tmp_path):
    from adventure_intelligence.ktide_observability import temporal_observability
    manifest = _dataset(tmp_path)
    mtime = manifest.stat().st_mtime_ns
    report = temporal_observability(manifest,workspace=tmp_path,max_gap_ms=7000)
    assert report["sampleWindows"] == 6
    assert report["totalObservedFrames"] == 6 * 8
    assert report["adjacentPairs"] == 6 * 7
    assert report["attentionConnectedPairFraction"] == 1.0
    assert report["windowsWithThreeFrameLocalOrder"] == 6
    assert report["decodedPtsCoverage"] == 0.0
    assert report["medianAdjacentGapMs"] == 1000.0
    assert "decoded_pts_missing_for_some_observations" in report["riskFlags"]
    assert report["workspaceModified"] is False
    assert report["originalVideoRead"] is False
    assert manifest.stat().st_mtime_ns == mtime


def test_temporal_audit_finds_disconnected_samples_and_fails_closed_on_tamper(tmp_path):
    from adventure_intelligence.ktide_observability import temporal_observability
    manifest = _dataset(tmp_path)
    doc = json.loads(manifest.read_text())
    first = doc["samples"][0]
    shard = manifest.parent / first["shard"]
    with np.load(shard,allow_pickle=False) as z:
        x = z["embeddings"].copy()
        pts = z["decoded_pts_ms"].copy()
    t = np.arange(8,dtype=np.int64)*12000
    np.savez_compressed(shard,embeddings=x,t_ms=t,decoded_pts_ms=pts)
    first["endMs"] = 100000
    first["shardSha256"] = sha256(shard.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(doc))
    report = temporal_observability(manifest,workspace=tmp_path,max_gap_ms=7000)
    assert report["windowsWithoutThreeFrameLocalOrder"] == 1
    assert report["attentionConnectedPairFraction"] < 1
    assert report["windows"][0]["isolatedFrames"] == 8
    assert "some_windows_have_no_three_frame_contiguous_order" in report["riskFlags"]
    # Never silently accept a changed feature shard after signing.
    with shard.open("ab") as file:
        file.write(b"tamper")
    with pytest.raises(ValueError,match="changed|SHA"):
        temporal_observability(manifest,workspace=tmp_path,max_gap_ms=7000)

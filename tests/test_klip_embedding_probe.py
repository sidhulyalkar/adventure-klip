"""Offline, synthetic controls for the dense embedding experiment probe."""
from pathlib import Path
import sys

import pytest

np = pytest.importorskip("numpy")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import klip_embedding_probe as probe  # noqa: E402


def synthetic_frames(seed=4):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(20, 12))
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def test_static_mean_and_absolute_delta_are_reversal_invariant():
    x = synthetic_frames()
    original = probe.representations(x)
    reversed_ = probe.representations(x[::-1])
    assert probe.distance(original["frozen_mean"], reversed_["frozen_mean"]) < 1e-7
    assert probe.distance(original["mean_absolute_delta"], reversed_["mean_absolute_delta"]) < 1e-7
    assert probe.distance(original["linear_time_weighted"], reversed_["linear_time_weighted"]) > 1e-5


def test_excludes_same_source_from_retrieval():
    x = synthetic_frames()
    base = {"clipId": "clip-a", "sourceSha256": "a"*64, "times": list(range(0, 5000, 250)), "vectors": x.tolist()}
    other = dict(base, clipId="clip-b", sourceSha256="b"*64)
    sibling = dict(base, clipId="clip-c")
    views = [probe.representations(x)] * 3
    deck = probe.retrieval([base, other, sibling], views)
    top = deck["frozen_mean"][0]["topUnjudgedCandidates"]
    assert len(top) == 1
    assert top[0]["clipId"] == "clip-b"


def test_sequence_diagnostic_rejects_unsupported_gaps():
    x = synthetic_frames()
    clip = {"clipId": "clip-a", "sourceSha256": "a"*64,
            "times": list(range(0, 5000, 250)), "vectors": x.tolist()}
    result = probe.inspect_sequence(clip, seed=31)
    assert result["connectedPairs"] == 19
    clip["times"][10] = 9000
    with pytest.raises(ValueError):
        probe.inspect_sequence(clip, seed=31)

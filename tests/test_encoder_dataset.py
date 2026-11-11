"""Pure manifest and reproducible grouped-split tests. No footage."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from adventure_intelligence.encoder_dataset import (
    VERSION, group_splits, validate_manifest, load_manifest,
    safe_training_workspace,
)
from adventure_intelligence.encoder_export import validate_selection, SELECTION_VERSION


def sample(index, group=None):
    return {
        "sampleId": f"clip-{index:02d}", "sourceSha256": f"{index+1:064x}",
        "groupId": group or f"trip-{index:02d}",
        "goal": "sport_action", "subsport": "skiing_speed",
        "startMs": 2000, "endMs": 15000,
        "shard": f"shards/{index+1:024x}.npz",
        "shardSha256": sha256(f"test-{index}".encode()).hexdigest(),
        "sampleCount": 8,
    }


def doc(n=10):
    return {"version": VERSION, "featureModelKey": "a" * 20,
            "embeddingDim": 16, "samples": [sample(i) for i in range(n)]}


def test_splits_are_deterministic_disjoint_and_dataset_preserved():
    data = doc(12)
    result = validate_manifest(data)
    a = group_splits(data, seed=42)
    b = group_splits(data, seed=42)
    assert a == b
    assert result["groups"] == 12
    assert set(a.values()) == {"train", "val", "test"}
    assert not any("split" in s for s in data["samples"])
    assert sum(1 for v in a.values() if v == "test") >= 1
    assert set(s["groupId"] for s in data["samples"] if a[s["groupId"]] == "train").isdisjoint(
        s["groupId"] for s in data["samples"] if a[s["groupId"]] == "test")


def test_cannot_split_same_source_across_two_groups():
    data = doc(4)
    data["samples"][1]["sourceSha256"] = data["samples"][0]["sourceSha256"]
    with pytest.raises(ValueError, match="multiple split groups"):
        validate_manifest(data)


def test_same_trip_different_sources_always_same_split():
    data = doc(10)
    data["samples"][1]["groupId"] = data["samples"][0]["groupId"]
    assignment = group_splits(data, seed=123)
    assert len(assignment) == 9
    assert assignment[data["samples"][1]["groupId"]] == assignment[data["samples"][0]["groupId"]]


@pytest.mark.parametrize("field,value", [
    ("sourceSha256", "../../originals"),
    ("shard", "../../originals/ski.mp4"),
    ("shard", "shards/../../../originals/fake.npz"),
    ("sampleCount", 0),
    ("startMs", -1),
    ("endMs", 2000),
    ("goal", "unsafely_guessed"),
    ("subsport", "skiing_speed_from_model_score"),
    ("groupId", "trip/name"),
])
def test_reject_bad_provenance_or_metadata(field, value):
    data = doc(4)
    data["samples"][0][field] = value
    with pytest.raises(ValueError):
        validate_manifest(data)


def test_untrusted_tensor_shards_rejected_before_training(tmp_path):
    data = doc(4)
    (tmp_path / "shards").mkdir()
    with pytest.raises(ValueError, match="Missing or oversized"):
        validate_manifest(data, root=tmp_path, verify_shards=True)
    (tmp_path / "shards" / "000000000000000000000001.npz").symlink_to(
        tmp_path / "originals.mp4")
    with pytest.raises(ValueError, match="Symlinked"):
        validate_manifest(data, root=tmp_path, verify_shards=True)


def test_manifest_version_sha_and_file_limits(tmp_path):
    data = doc(4)
    path = tmp_path / "manifest.json"
    raw = json.dumps(data).encode()
    path.write_bytes(raw)
    loaded, digest = load_manifest(path)
    assert loaded == data
    assert digest == sha256(raw).hexdigest()
    wrong = deepcopy(data)
    wrong["version"] = "legacy"
    with pytest.raises(ValueError):
        validate_manifest(wrong)


def test_selection_needs_declared_groups_not_inferred_trip():
    selections = []
    for i in range(3):
        s = sample(i)
        selections.append({k: s[k] for k in (
            "sampleId", "sourceSha256", "groupId",
            "goal", "subsport", "startMs", "endMs")})
    result = validate_selection({"version": SELECTION_VERSION, "selections": selections})
    assert len(result) == 3
    selections[1]["groupId"] = selections[0]["groupId"]
    selections[2]["groupId"] = selections[0]["groupId"]
    with pytest.raises(ValueError, match="3 user-declared"):
        validate_selection({"version": SELECTION_VERSION, "selections": selections})


@pytest.mark.parametrize("subdir", ["DCIM", "DCIM/100MEDIA", "originals"])
def test_training_must_not_write_in_raw_video_directories(tmp_path, subdir):
    path = tmp_path / subdir
    path.mkdir(parents=True)
    with pytest.raises(ValueError, match="camera/raw-originals"):
        safe_training_workspace(path)


def test_separate_external_volume_workspace_is_allowed(tmp_path):
    path = tmp_path / "Sid_DJI_SSD" / "AdventureAI"
    path.mkdir(parents=True)
    assert safe_training_workspace(path) == path

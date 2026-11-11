"""K-TIDE cache-export tests: synthetic completed frame data, no video reads."""
from hashlib import sha256
import json
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

from adventure_intelligence.catalog import connect
from adventure_intelligence.encoder_dataset import validate_manifest
from adventure_intelligence.encoder_export import (
    SELECTION_VERSION, export_cached_training_dataset, validate_selection,
)
from adventure_intelligence.workspace import init


def create_workspace(tmp_path):
    root = tmp_path / "workspace"
    init(root)
    sources = [f"{i+1:064x}" for i in range(3)]
    with connect(root) as db:
        for source in sources:
            # Absolute source path intentionally does not exist. A feature-only
            # export must not try to open the camera recording.
            db.execute("""
                INSERT INTO assets
                (id,sha256,stored_path,size_bytes,suffix,duration,
                 capture_time,activity,metadata_json)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (source, source, f"/Volumes/unmounted/{source}.mp4", 42,
                 ".mp4", 20., None, "ski", "{}"))
        db.commit()
    return root, sources


def selection(sources):
    return {"version": SELECTION_VERSION, "selections": [{
        "sampleId": f"sample-{i}",
        "sourceSha256": source,
        "groupId": f"independent-trip-{i}",
        "goal": "sport_action", "subsport": "skiing_terrain_park",
        "startMs": 0, "endMs": 15000,
    } for i, source in enumerate(sources)]}


def test_feature_only_export_and_shard_integrity(tmp_path, monkeypatch):
    root, sources = create_workspace(tmp_path)
    import adventure_intelligence.scene_atlas as scene_atlas

    def verified_mock(db, workspace, source, provider):
        assert provider == "siglip2"
        return [
            {"tMs": i * 1000, "decodedPtsMs": i * 1000 + 10,
             "embedding": [float(i + 1), float(j + 1)] * 8}
            for i, j in zip(range(1, 11), range(1, 11))
        ], "a" * 20

    monkeypatch.setattr(scene_atlas, "frames_for_asset", verified_mock)
    report = export_cached_training_dataset(
        root, selection(sources), name="ktide-testing-001", max_frames=8)
    assert report["mediaFilesRead"] == 0
    assert report["rawOriginalsRehashed"] is False
    assert report["samples"] == 3
    manifest_path = Path(report["datasetPath"])
    doc = json.loads(manifest_path.read_text())
    assert validate_manifest(doc, root=manifest_path.parent,
                             verify_shards=True)["samples"] == 3
    assert all(row["sampleCount"] == 8 for row in doc["samples"])
    for row in doc["samples"]:
        with np.load(manifest_path.parent / row["shard"], allow_pickle=False) as arrays:
            assert arrays["embeddings"].shape == (8, 16)
            assert np.all(np.diff(arrays["t_ms"]) > 0)
    with pytest.raises(ValueError, match="existing dataset"):
        export_cached_training_dataset(
            root, selection(sources), name="ktide-testing-001", max_frames=8)


def test_cache_failure_does_not_leave_partial_dataset(tmp_path, monkeypatch):
    root, sources = create_workspace(tmp_path)
    import adventure_intelligence.scene_atlas as scene_atlas

    def empty_cache(db, workspace, aid, provider):
        return [], None
    monkeypatch.setattr(scene_atlas, "frames_for_asset", empty_cache)
    with pytest.raises(ValueError, match="No completed"):
        export_cached_training_dataset(root, selection(sources),
                                       name="ktide-no-cache-v1")
    assert not (root / "reports" / "ktide-no-cache-v1").exists()


def test_selection_duplicate_source_group_rejected():
    sources = [f"{i+1:064x}" for i in range(3)]
    s = selection(sources)
    s["selections"][1]["sourceSha256"] = s["selections"][0]["sourceSha256"]
    with pytest.raises(ValueError, match="multiple groups"):
        validate_selection(s)

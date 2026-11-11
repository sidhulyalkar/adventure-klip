"""Offline cache-preflight tests, fabricated semantic receipts only."""
from hashlib import sha256
import json
from pathlib import Path

from adventure_intelligence.catalog import connect
from adventure_intelligence.ktide_preflight import preflight
from adventure_intelligence.workspace import init


def setup(tmp_path):
    root = tmp_path / "workspace"
    init(root)
    aid = "a" * 64
    config_id = "b" * 64
    doc = {
        "assetId": aid, "configId": config_id, "provider": "histogram",
        "frames": [
            {"tMs": i * 1000, "embedding": [1.0, float(i + 1)] * 8}
            for i in range(5)
        ]
    }
    content = json.dumps(doc)
    (root / "semantics").mkdir()
    filename = "synthetic-semantic.json"
    (root / "semantics" / filename).write_text(content)
    with connect(root) as db:
        db.execute(
            "INSERT INTO assets(id,sha256,stored_path,size_bytes,suffix,duration,"
            "capture_time,activity,metadata_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (aid, aid, "/Volumes/NOT-MOUNTED/movie.mp4", 123, ".mp4", 8.,
             "2026-03-09T20:00:29Z", "unknown", "{}"))
        db.execute(
            "INSERT INTO semantic_jobs(asset_id,config_id,config_json,status,filename,sha256)"
            " VALUES(?,?,?,?,?,?)",
            (aid, config_id, '{"provider":"histogram"}', "completed",
             filename, sha256(content.encode()).hexdigest()))
        db.execute("INSERT INTO semantic_active VALUES (?,?,?)", (aid, "histogram", config_id))
    return root, aid, config_id


def test_readonly_preflight_accepts_only_receipted_activated_features(tmp_path):
    root, aid, _ = setup(tmp_path)
    before = (root / "catalog.sqlite3").stat().st_mtime_ns
    result = preflight(root)
    assert result["readySources"] == 1
    assert result["selectedProviderReceiptStatuses"] == {"verified": 1}
    assert result["captureDateBucketsForInspection"] == {"2026-03-09": 1}
    assert result["usableSources"][0]["sourceSha256"] == aid
    assert result["usableSources"][0]["groupId"] is None
    assert not result["independentTripsIdentified"]
    assert not result["rawMediaRead"]
    assert not result["workspaceCatalogModified"]
    assert (root / "catalog.sqlite3").stat().st_mtime_ns == before


def test_readonly_preflight_rejects_tampered_semantic_receipt(tmp_path):
    root, _, _ = setup(tmp_path)
    (root / "semantics" / "synthetic-semantic.json").write_text('{"changed":true}')
    report = preflight(root)
    assert report["readySources"] == 0
    assert report["selectedProviderReceiptStatuses"] == {
        "semantic_artifact_checksum_mismatch": 1}


def test_readonly_preflight_detects_inactive_job(tmp_path):
    root, _, _ = setup(tmp_path)
    with connect(root) as db:
        db.execute("DELETE FROM semantic_active")
    report = preflight(root)
    assert report["readySources"] == 0
    assert report["selectedProviderReceiptStatuses"] == {"inactive_job": 1}


def test_readonly_preflight_never_counts_non_embedding_context(tmp_path):
    root, _, _ = setup(tmp_path)
    path = root / "semantics" / "synthetic-semantic.json"
    doc = json.loads(path.read_text())
    for frame in doc["frames"]:
        frame["embedding"] = None
    content = json.dumps(doc)
    path.write_text(content)
    with connect(root) as db:
        db.execute("UPDATE semantic_jobs SET sha256=?", (sha256(content.encode()).hexdigest(),))
    report = preflight(root)
    assert report["readySources"] == 0
    assert report["selectedProviderReceiptStatuses"] == {"invalid_or_mixed_dimensions": 1}

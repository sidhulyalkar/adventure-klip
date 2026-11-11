"""K-TIDE balanced window planner, no raw recording access."""
import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.ktide_planner import (
    ASSIGNMENTS_VERSION, plan_cached_windows, validate_assignments,
)
from adventure_intelligence.encoder_export import SELECTION_VERSION
from adventure_intelligence.workspace import init


def setup_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    init(workspace)
    sources = [f"{i + 1:064x}" for i in range(3)]
    with connect(workspace) as db:
        for source in sources:
            db.execute("""
                INSERT INTO assets
                (id,sha256,stored_path,size_bytes,suffix,duration,
                 capture_time,activity,metadata_json)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (source, source, "/Volumes/unmounted/never-read.mp4", 30,
                  ".mp4", 75.0, None, "ski", "{}"))
        db.commit()
    return workspace, sources


def annotations(sources):
    return {"version": ASSIGNMENTS_VERSION, "sources": [{
        "sourceSha256": source, "groupId": f"verified-trip-{i}",
        "goal": "sport_action", "subsport": "skiing_trees",
    } for i, source in enumerate(sources)]}


def test_planner_emits_uniform_timeline_controls_without_original_media(tmp_path, monkeypatch):
    workspace, sources = setup_workspace(tmp_path)
    import adventure_intelligence.scene_atlas as atlas

    def cached(db, workspace, aid, provider):
        assert provider == "siglip2"
        # One vector every 3 seconds, without any action/motion labels.
        return [{"tMs": t, "embedding": [float(t + 1000), 2.0] * 8}
                for t in range(0, 73000, 3000)], "f" * 20

    monkeypatch.setattr(atlas, "frames_for_asset", cached)
    proposal = plan_cached_windows(
        workspace, annotations(sources), window_seconds=30,
        stride_seconds=30, max_per_source=2)
    assert proposal["version"] == SELECTION_VERSION
    assert len(proposal["selections"]) == 6
    assert set(r["groupId"] for r in proposal["selections"]) == {
        "verified-trip-0", "verified-trip-1", "verified-trip-2"}
    assert all(r["sourceSha256"] in sources for r in proposal["selections"])
    assert all(r["endMs"] <= 75000 for r in proposal["selections"])
    assert all("label" not in r and "score" not in r for r in proposal["selections"])


def test_assignments_demand_independent_trip_groups(tmp_path):
    _, sources = setup_workspace(tmp_path)
    doc = annotations(sources)
    doc["sources"][1]["groupId"] = doc["sources"][0]["groupId"]
    doc["sources"][2]["groupId"] = doc["sources"][0]["groupId"]
    with pytest.raises(ValueError, match="three independently"):
        validate_assignments(doc)


def test_incompatible_teacher_revision_fails_without_new_files(tmp_path, monkeypatch):
    workspace, sources = setup_workspace(tmp_path)
    import adventure_intelligence.scene_atlas as atlas

    def cached(db, workspace, aid, provider):
        return [{"tMs": i * 1000, "embedding": [float(i + 1)] * 16}
                for i in range(10)], aid[-20:]

    monkeypatch.setattr(atlas, "frames_for_asset", cached)
    with pytest.raises(ValueError, match="Incompatible frozen"):
        plan_cached_windows(workspace, annotations(sources))
    assert not (workspace / "reports" / "ktide-training").exists()

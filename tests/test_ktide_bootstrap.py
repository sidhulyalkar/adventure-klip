"""K-TIDE zero-cache bootstrap regression tests, with no actual video inference."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.ktide_bootstrap import (
    inspect_workspace, preflight_bootstrap, run_bootstrap,
)
from adventure_intelligence.ktide_planner import ASSIGNMENTS_VERSION
from adventure_intelligence.workspace import init


def fixture_workspace(tmp_path):
    root = tmp_path / "AdventureAI"
    init(root)
    original_dir = tmp_path / "test-camera-media"
    original_dir.mkdir()
    selected = []
    with connect(root) as db:
        for i, activity in enumerate(("ski", "snorkeling", "bike")):
            aid = f"{i + 1:064x}"
            path = original_dir / f"synthetic-camera-{i}.mp4"
            path.write_bytes(b"fake-media-not-an-actual-video" * 100)
            db.execute(
                """INSERT INTO assets (
                id, sha256, stored_path, size_bytes, suffix, duration,
                capture_time, activity, metadata_json
                ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (aid, aid, str(path), path.stat().st_size, ".mp4",
                 120.0, f"2026-01-{i + 1:02d}", activity, "{}"),
            )
            selected.append({
                "sourceSha256": aid, "groupId": f"verified-trip-{i}",
                "goal": "sport_action",
                "subsport": ("skiing_trees","snorkeling_reef","mountain_biking_trail")[i],
            })
        db.commit()
    return root, selected, original_dir


def assignment_doc(rows):
    return {"version": ASSIGNMENTS_VERSION, "sources": rows}


def test_empty_cache_inspect_has_three_candidates_and_zero_writes(tmp_path):
    root, rows, _ = fixture_workspace(tmp_path)
    catalog = root / "catalog.sqlite3"
    before = catalog.stat().st_mtime_ns
    report = inspect_workspace(root)
    assert report["catalogSources"] == 3
    assert report["semanticJobs"] == 0
    assert report["sourcesPickedForAutomaticProcessing"] == 0
    assert report["suspectedZeroReadyCause"] == "no_registered_completed_siglip2_cache"
    assert len(report["suggestedPilotSourcesForHumanSelection"]) == 3
    assert all(row["originalPathAppearsAvailable"]
               for row in report["suggestedPilotSourcesForHumanSelection"])
    assert all(row["groupId"] is None for row in report["suggestedPilotSourcesForHumanSelection"])
    assert report["sourceVideoOpenedOrHashed"] is False
    assert report["catalogMutated"] is False
    assert catalog.stat().st_mtime_ns == before


def test_inactive_and_active_semantic_jobs_are_distinguished(tmp_path):
    root, rows, _ = fixture_workspace(tmp_path)
    with connect(root) as db:
        db.execute(
            """INSERT INTO semantic_jobs
            (asset_id,config_id,config_json,status,filename,sha256)
            VALUES (?,? ,?, 'completed',?,?)""",
            (rows[0]["sourceSha256"],"config-a",
             json.dumps({"provider":"siglip2"}), "cached.json", "a" * 64))
        db.execute(
            """INSERT INTO semantic_jobs
            (asset_id,config_id,config_json,status,filename,sha256)
            VALUES (?,? ,?, 'failed',?,?)""",
            (rows[1]["sourceSha256"],"config-b",
             json.dumps({"provider":"owlv2"}),None,None))
        db.commit()
    report = inspect_workspace(root, candidates=0)
    assert report["suspectedZeroReadyCause"] == "completed_siglip2_jobs_exist_but_are_not_active"
    assert report["inactiveCompletedByProvider"] == {"siglip2": 1}
    assert report["jobsByProviderStatus"] == [
        {"provider":"owlv2","status":"failed","count":1},
        {"provider":"siglip2","status":"completed","count":1},
    ]
    with connect(root) as db:
        db.execute(
            "INSERT INTO semantic_active(asset_id,provider,config_id) VALUES (?,?,?)",
            (rows[0]["sourceSha256"],"siglip2","config-a"))
        db.commit()
    report = inspect_workspace(root, candidates=0)
    assert report["suspectedZeroReadyCause"].startswith("active_completed")
    assert report["activeCompletedCacheFileMissingByProvider"] == {"siglip2":1}


def test_bounded_plan_refuses_missing_and_mutated_sources(tmp_path):
    root, rows, original_dir = fixture_workspace(tmp_path)
    doc = assignment_doc(rows)
    report = preflight_bootstrap(root, doc)
    assert report["mode"] == "preflight"
    assert report["selectedSources"] == 3
    assert report["sourceMediaRead"] is False
    assert report["sourceOriginalWillBeReadAndHashedIfRun"] is True
    assert all(x["estimatedFrames"] == 30 for x in report["sources"])
    assert all(x["estimatedSampleIntervalMs"] == 4000 for x in report["sources"])
    with pytest.raises(ValueError, match="Source roster exceeds"):
        preflight_bootstrap(root,doc,max_sources=2)
    with pytest.raises(ValueError,match="source-byte budget"):
        preflight_bootstrap(root,doc,max_total_gb=.0001)
    original=(original_dir / "synthetic-camera-0.mp4")
    original.write_bytes(original.read_bytes() + b"mutated")
    with pytest.raises(ValueError, match="Source size differs"):
        preflight_bootstrap(root,doc)
    original.unlink()
    with pytest.raises(ValueError, match="original unavailable"):
        preflight_bootstrap(root,doc)


def test_no_media_access_in_readonly_preflight(tmp_path, monkeypatch):
    root, rows, original_dir = fixture_workspace(tmp_path)
    original_open = Path.open
    def no_original_open(path, *args, **kwargs):
        if path.parent == original_dir:
            raise AssertionError("Inspected original media contents without consent")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", no_original_open)
    inspect_workspace(root)
    preflight_bootstrap(root, assignment_doc(rows))


def test_explicit_run_calls_only_approved_sha_set(tmp_path, monkeypatch):
    root, rows, _ = fixture_workspace(tmp_path)
    import adventure_intelligence.semantic as semantic
    captured = []
    def fake_generate(workspace, *, backend, sample_seconds, max_frames, asset_ids):
        captured.append({
            "workspace": workspace, "backend": backend,
            "sampleSeconds": sample_seconds, "maxFrames": max_frames,
            "ids": asset_ids,
        })
        return {"processed": 3, "cached": 0, "missing": 0,
                "errors": [], "frames_computed": 90,
                "frames_cached": 0, "configId": "synthetic-config"}
    monkeypatch.setattr(semantic, "generate_semantics", fake_generate)
    result=run_bootstrap(root,assignment_doc(rows))
    assert len(captured) == 1
    assert captured[0]["backend"] == "siglip2"
    assert captured[0]["ids"] == {r["sourceSha256"] for r in rows}
    assert result["trainingPerformed"] is False
    assert result["semanticResult"]["processed"] == 3
    assert result["sourceSha256VerifiedByExistingSemanticPipeline"]


def test_cli_rejects_run_without_source_read_ack(tmp_path):
    root, rows, _ = fixture_workspace(tmp_path)
    selection = tmp_path / "assignments.json"
    selection.write_text(json.dumps(assignment_doc(rows)))
    script = Path(__file__).resolve().parents[1] / "scripts" / "klip_ktide_bootstrap.py"
    plan = subprocess.run([
        sys.executable, str(script), "--workspace", str(root),
        "--preflight", "--assignments", str(selection),
    ], capture_output=True, text=True, timeout=20)
    assert plan.returncode == 0, plan.stderr
    assert json.loads(plan.stdout)["sourceMediaRead"] is False
    refused = subprocess.run([
        sys.executable, str(script), "--workspace", str(root),
        "--run", "--assignments", str(selection),
    ], capture_output=True, text=True, timeout=20)
    assert refused.returncode != 0
    assert "--ack-original-reads" in refused.stderr
    readonly = subprocess.run([
        sys.executable, str(script), "--workspace", str(root), "--inspect",
    ], capture_output=True, text=True, timeout=20)
    assert readonly.returncode == 0, readonly.stderr
    assert json.loads(readonly.stdout)["catalogSources"] == 3


def test_dcim_workspace_and_wrong_catalog_refused(tmp_path):
    camera_dir = tmp_path / "DCIM"
    camera_dir.mkdir()
    with pytest.raises(ValueError, match="camera/raw-originals"):
        inspect_workspace(camera_dir)
    empty = tmp_path / "FreshWorkspace"
    empty.mkdir()
    with pytest.raises(ValueError, match="No safe local Klip catalog"):
        inspect_workspace(empty)

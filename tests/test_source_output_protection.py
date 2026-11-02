"""Source folders are read-only inputs, even for arbitrary output arguments."""
from __future__ import annotations

from pathlib import Path

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.report import write_report
from adventure_intelligence.export import export_manifest
from adventure_intelligence.safety import protect_source_output


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    folder = tmp_path / "Ski Videos"
    folder.mkdir()
    video = folder / "DJI_0001.MP4"
    video.write_bytes(b"untouched original")
    monkeypatch.setattr(
        "adventure_intelligence.indexing.probe",
        lambda p: {"duration": 4.0, "audio": False},
    )
    workspace = tmp_path / "AdventureAI"
    result = index_folder(folder, workspace, activity="ski")
    assert result["asset_ids"]
    return folder, video, workspace


@pytest.mark.parametrize("filename", [
    "DJI_0001.MP4", "new-output.mp4", "review.html",
    "highlight-plan.json", "public-export.json",
])
def test_rejects_any_generated_file_in_original_folder(indexed, filename):
    folder, video, workspace = indexed
    with connect(workspace) as db:
        with pytest.raises(ValueError, match="source-media directory"):
            protect_source_output(db, folder / filename)
    assert video.read_bytes() == b"untouched original"
    assert sorted(p.name for p in folder.iterdir()) == ["DJI_0001.MP4"]


def test_symlink_alias_to_source_folder_is_blocked(indexed, tmp_path):
    folder, video, workspace = indexed
    alias = tmp_path / "alias-to-ski"
    alias.symlink_to(folder, target_is_directory=True)
    with connect(workspace) as db:
        with pytest.raises(ValueError, match="source-media directory"):
            protect_source_output(db, alias / "new-output.mp4")
    assert video.read_bytes() == b"untouched original"


def test_review_and_export_reject_source_output_before_writing(indexed):
    folder, video, workspace = indexed
    with pytest.raises(ValueError, match="source-media directory"):
        write_report(workspace, folder / "report.html")
    with pytest.raises(ValueError, match="source-media directory"):
        export_manifest(workspace, folder / "public.json")
    assert video.read_bytes() == b"untouched original"
    assert sorted(p.name for p in folder.iterdir()) == ["DJI_0001.MP4"]


def test_safe_workspace_outputs_and_original_source_unchanged(indexed):
    folder, video, workspace = indexed
    with connect(workspace) as db:
        target = workspace / "exports" / "review.html"
        assert protect_source_output(db, target) == target.resolve()
    result = write_report(workspace, target)
    assert Path(result["report"]).exists()
    assert video.read_bytes() == b"untouched original"
    assert sorted(p.name for p in folder.iterdir()) == ["DJI_0001.MP4"]


def test_plan_cli_rejects_output_in_source_folder(indexed):
    from adventure_intelligence.cli import main
    folder, video, workspace = indexed
    result = main([
        "--workspace", str(workspace), "plan",
        "--recipe", "best-sequences",
        "--output", str(folder / "plan.json"),
    ])
    assert result == 2
    assert not (folder / "plan.json").exists()
    assert video.read_bytes() == b"untouched original"

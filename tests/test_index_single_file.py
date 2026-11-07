"""Zero-copy one-video indexing: no need to rehash the complete SSD catalog."""
from hashlib import sha256
from pathlib import Path

import pytest

from adventure_intelligence import indexing
from adventure_intelligence.catalog import connect


def test_single_file_index_does_not_touch_other_videos(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    library = tmp_path / "healthy-ssd"
    library.mkdir()
    target = library / "NEW.MP4"
    target.write_bytes(b"tiny synthetic camera payload")
    sibling = library / "ALREADY.MP4"
    sibling.write_bytes(b"other data")
    fingerprint = sha256(target.read_bytes()).hexdigest()
    monkeypatch.setattr(indexing, "probe",
                        lambda p: {"duration": 5.0, "capture_time": None})
    dry = indexing.index_folder(target, workspace, dry_run=True)
    assert dry["discovered"] == 1
    assert dry["paths"] == [str(target)]
    assert not (workspace / "catalog.sqlite3").exists()
    result = indexing.index_folder(target, workspace)
    assert result["indexed"] == 1
    assert result["asset_ids"] == [fingerprint]
    assert result["errors"] == []
    with connect(workspace) as db:
        rows = db.execute("SELECT id,stored_path FROM assets").fetchall()
        assert len(rows) == 1
        assert rows[0]["id"] == fingerprint
        assert rows[0]["stored_path"] == str(target)
    assert target.read_bytes() == b"tiny synthetic camera payload"
    assert sibling.read_bytes() == b"other data"
    assert not list((workspace / "originals").rglob("*.mp4"))
    again = indexing.index_folder(target, workspace)
    assert again["skipped"] == 1


def test_single_file_rejects_symlinks_and_nonvideos(tmp_path):
    root = tmp_path / "workspace"
    videos = tmp_path / "media"
    videos.mkdir()
    original = videos / "A.mp4"
    original.write_bytes(b"demo")
    symlink = videos / "link.mp4"
    symlink.symlink_to(original)
    with pytest.raises(ValueError, match="Symlinked"):
        indexing.index_folder(symlink, root, dry_run=True)
    wrong = videos / "notes.txt"
    wrong.write_text("not a video")
    with pytest.raises(ValueError, match="Unsupported"):
        indexing.index_folder(wrong, root, dry_run=True)
    assert not (root / "catalog.sqlite3").exists()


def test_single_file_duplicate_remaps_without_reopening_old_sd_path(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    videos = tmp_path / "healthy-ssd"
    videos.mkdir()
    selected = videos / "SOURCE.mp4"
    body = b"original-video-bytes"
    selected.write_bytes(body)
    digest = sha256(body).hexdigest()
    with connect(workspace) as db:
        db.execute(
            "INSERT INTO assets (id,sha256,stored_path,size_bytes,suffix,duration,activity)"
            " VALUES (?,?,?,?,?,?,?)",
            (digest,digest,"/Volumes/OLD_UNMOUNTED/DCIM/SOURCE.mp4",
             len(body),".mp4",10.0,"unknown"))
    monkeypatch.setattr(indexing, "probe",
                        lambda p: pytest.fail("Duplicate with valid duration needs no probe"))
    result = indexing.index_folder(selected, workspace)
    assert result["duplicates"] == 1
    with connect(workspace) as db:
        row=db.execute("SELECT stored_path FROM assets WHERE id=?", (digest,)).fetchone()
    assert row["stored_path"] == str(selected)

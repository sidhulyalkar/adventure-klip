"""Whole-SSD regression: AppleDouble metadata is not film and invalid assets fail fast.

These fixtures never modify source bytes and do not require media decoders.
"""
from pathlib import Path

import pytest

from adventure_intelligence.ingest import iter_videos
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.catalog import connect
from adventure_intelligence.semantic_jobs import run_semantics
from adventure_intelligence.analyze import analyze
from adventure_intelligence.acoustics import analyze_audio


def test_appledouble_and_mac_archive_metadata_not_video(tmp_path):
    source=tmp_path/"DJI"
    nested=source/"DCIM"/"100MEDIA"
    nested.mkdir(parents=True)
    for name in ("DJI_20250101010101_0001_D.MP4",
                 "._DJI_20250101010101_0001_D.MP4",
                 "._part.MOV","clip.mov"):
        (nested/name).write_bytes(name.encode())
    fake_dir=source/"__MACOSX"/"DCIM"
    fake_dir.mkdir(parents=True)
    (fake_dir/"dji_2.mp4").write_bytes(b"metadata")
    seen=[p.name for p in iter_videos(source)]
    assert seen==["DJI_20250101010101_0001_D.MP4","clip.mov"]


def test_index_rejects_invalid_duration_and_probe_errors(tmp_path,monkeypatch):
    source=tmp_path/"source";source.mkdir()
    payloads={
        "good.mp4":b"real-bytes",
        "._good.mp4":b"mac-fork",
        "empty.mp4":b"fake-video",
        "bad.mp4":b"unprobeable",
    }
    for name,contents in payloads.items():
        (source/name).write_bytes(contents)
    before={x.name:x.read_bytes() for x in source.iterdir()}
    def probe(path):
        if path.name=="bad.mp4":
            raise ValueError("no real video stream")
        return {"duration":0 if path.name=="empty.mp4" else 25.0,
                "capture_time":None,"audio":False}
    monkeypatch.setattr("adventure_intelligence.indexing.probe",probe)
    workspace=tmp_path/"work"
    dry=index_folder(source,workspace,dry_run=True)
    assert dry["discovered"]==3
    assert not workspace.exists()
    out=index_folder(source,workspace)
    assert out["discovered"]==3
    assert out["indexed"]==1
    assert len(out["errors"])==2
    with connect(workspace) as db:
        rows=db.execute("SELECT id,duration,stored_path FROM assets").fetchall()
        assert len(rows)==1
        assert rows[0]["duration"]==25
        assert Path(rows[0]["stored_path"]).name=="good.mp4"
    assert {x.name:x.read_bytes() for x in source.iterdir()}==before


def test_invalid_legacy_assets_are_skipped_before_source_hash(tmp_path,monkeypatch):
    source=tmp_path/"source";source.mkdir()
    file=source/"legacy.mp4"
    original=b"legacy-original-video"
    file.write_bytes(original)
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda p:{"duration":12.0,"audio":False})
    work=tmp_path/"work"
    assert index_folder(source,work)["indexed"]==1
    with connect(work) as db:
        db.execute("UPDATE assets SET duration=0")
    def must_not_verify(*args,**kwargs):
        raise AssertionError("Bad legacy video must be skipped before hashing")
    monkeypatch.setattr("adventure_intelligence.semantic_jobs.resolve_source",must_not_verify)
    monkeypatch.setattr("adventure_intelligence.analyze.resolve_source",must_not_verify)
    monkeypatch.setattr("adventure_intelligence.acoustics.resolve_source",must_not_verify)

    class Provider:
        name="test-provider"
        version="test-version"
        def describe(self,_):
            raise AssertionError("No model inference on invalid duration")

    sem=run_semantics(work,Provider(),sample_seconds=15.0)
    assert sem["missing"]==1
    assert sem["errors"]==[]
    assert sem["frames_computed"]==0
    audio=analyze_audio(work)
    assert audio["skipped_invalid"]==1
    assert audio["failed"]==[]
    motion=analyze(work)
    assert len(motion["failed"])==1
    assert "positive probed duration" in motion["failed"][0]["error"]
    assert file.read_bytes()==original

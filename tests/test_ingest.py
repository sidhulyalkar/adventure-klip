from pathlib import Path
from adventure_intelligence.catalog import connect
from adventure_intelligence.ingest import ingest, looks_like_dji_card, sha_file
import adventure_intelligence.ingest as ingestion
import pytest

@pytest.fixture
def fake_probe(monkeypatch):
    monkeypatch.setattr(ingestion,"probe",lambda p: {"duration":10.0,"capture_time":None,
                            "width":1920,"height":1080,"audio":True})


def test_safe_idempotent_import_and_sidecars(tmp_path, fake_probe):
    camera=tmp_path/"camera"
    media=camera/"DCIM"/"100MEDIA"
    media.mkdir(parents=True)
    video=media/"DJI_0001.MP4"
    video.write_bytes(b"original recording bytes")
    (media/"DJI_0001.LRF").write_bytes(b"preview")
    root=tmp_path/"workspace"
    assert looks_like_dji_card(camera)
    assert ingest(camera,root,dry_run=True)["discovered"]==1
    assert not root.exists()
    first=ingest(camera,root,activity="ski")
    assert (first["imported"],first["sidecars"],first["errors"])==(1,1,[])
    second=ingest(camera,root,activity="ski")
    assert second["skipped"]==1
    with connect(root) as db:
        assert db.execute("SELECT COUNT(*) FROM assets").fetchone()[0]==1
        row=db.execute("SELECT * FROM assets").fetchone()
        assert row["activity"]=="ski"
        assert sha_file(Path(row["stored_path"]))==row["sha256"]
        assert (root/"sidecars"/row["id"]/"DJI_0001.LRF").exists()
    video.write_bytes(b"different recording same filename")
    third=ingest(camera,root)
    assert third["imported"]==1
    with connect(root) as db:
        assert db.execute("SELECT COUNT(*) FROM assets").fetchone()[0]==2


def test_no_source_workspace_overlap(tmp_path, fake_probe):
    cam=tmp_path/"card"
    cam.mkdir()
    with pytest.raises(ValueError,match="contain"):
        ingest(cam,cam/"internal")
    with pytest.raises(ValueError,match="contain"):
        ingest(cam,cam)
    with pytest.raises(ValueError,match="contain"):
        ingest(cam,cam.parent)


def test_symlink_video_is_skipped(tmp_path, fake_probe):
    source=tmp_path/"card";source.mkdir()
    secret=tmp_path/"secret.mp4";secret.write_bytes(b"not from card")
    (source/"DJI_0001.mp4").symlink_to(secret)
    assert ingest(source,tmp_path/"work")["discovered"]==0

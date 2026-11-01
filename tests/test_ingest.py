from pathlib import Path
from adventure_intelligence.catalog import connect
from adventure_intelligence.ingest import ingest, looks_like_dji_card, sha_file
import adventure_intelligence.ingest as ingestion
import pytest

@pytest.fixture
def fake_probe(monkeypatch):
    fake=lambda p: {"duration":10.0,"capture_time":None,
                    "width":1920,"height":1080,"audio":True}
    monkeypatch.setattr(ingestion,"probe",fake)
    monkeypatch.setattr("adventure_intelligence.indexing.probe",fake)


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


@pytest.mark.parametrize('same_path', [True, False])
def test_index_then_import_survives_source_removal(tmp_path, fake_probe, same_path):
    from adventure_intelligence.indexing import index_folder
    source = tmp_path/'indexed'; source.mkdir()
    video = source/'DJI_0001.mp4'; video.write_bytes(b'precious footage')
    root = tmp_path/'work'
    assert index_folder(source, root)['indexed'] == 1
    card = source
    if not same_path:
        card = tmp_path/'card'; card.mkdir()
        (card/video.name).write_bytes(video.read_bytes())
    result = ingest(card, root)
    assert result['errors'] == []
    assert result['imported'] == 1
    video.unlink()
    if not same_path:
        (card/video.name).unlink()
    with connect(root) as db:
        row = db.execute('SELECT * FROM assets').fetchone()
        stored = Path(row['stored_path'])
        assert stored.is_relative_to(root/'originals')
        assert sha_file(stored) == row['id']


def test_repeat_import_recovers_late_sidecar(tmp_path, fake_probe):
    card = tmp_path/'card'; card.mkdir()
    (card/'DJI_0001.mp4').write_bytes(b'video')
    root = tmp_path/'work'
    assert ingest(card, root)['imported'] == 1
    (card/'DJI_0001.srt').write_bytes(b'telemetry')
    result = ingest(card, root)
    assert result['skipped'] == 1 and result['sidecars'] == 1
    assert result['errors'] == []


def test_repeat_import_does_not_accept_corrupt_managed_video(tmp_path, fake_probe):
    card = tmp_path/'card'; card.mkdir()
    video = card/'DJI_0001.mp4'; video.write_bytes(b'original')
    root = tmp_path/'work'
    ingest(card, root)
    with connect(root) as db:
        stored = Path(db.execute('SELECT stored_path FROM assets').fetchone()[0])
    stored.write_bytes(b'corrupted')
    result = ingest(card, root)
    assert result['skipped'] == 0 and len(result['errors']) == 1
    assert video.read_bytes() == b'original'
    assert stored.read_bytes() == b'corrupted'  # Preserve evidence; never overwrite silently.


def test_reused_filename_changed_middle_preserved_mtime(tmp_path, fake_probe):
    import os
    card = tmp_path/'card'; card.mkdir()
    video = card/'DJI_0001.mp4'
    video.write_bytes(b'a' * (3*1024*1024))
    root = tmp_path/'work'
    ingest(card, root)
    before = video.stat()
    with video.open('r+b') as handle:
        handle.seek(1500000); handle.write(b'b')
    os.utime(video, ns=(before.st_atime_ns, before.st_mtime_ns))
    result = ingest(card, root)
    assert result['imported'] == 1 and result['errors'] == []
    with connect(root) as db:
        assert db.execute('SELECT COUNT(*) FROM assets').fetchone()[0] == 2

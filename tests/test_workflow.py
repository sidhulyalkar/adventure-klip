import json
from pathlib import Path
from adventure_intelligence.catalog import connect
from adventure_intelligence.curation import import_labels,bind_event
from adventure_intelligence.export import export_manifest
from adventure_intelligence.reels import plan_reel
from adventure_intelligence.ingest import ingest
import adventure_intelligence.ingest as ingestion
import pytest


def test_manual_labels_episode_plan_and_publication_gate(tmp_path,monkeypatch):
    monkeypatch.setattr(ingestion,"probe",lambda p:{"duration":60.0,"audio":False,"capture_time":None})
    source=tmp_path/"camera";source.mkdir()
    (source/"DJI_1234.mp4").write_bytes(b"video placeholder")
    work=tmp_path/"work"
    ingest(source,work,activity="ski")
    with connect(work) as db:
        aid=db.execute("SELECT id FROM assets").fetchone()[0]
    labels={"asset_id":aid,"activity":"ski","events":[
         {"kind":"jump","start_ms":1000,"end_ms":2400,"score":.9},
         {"kind":"jump","start_ms":5200,"end_ms":6500,"score":.95},
         {"kind":"jump","start_ms":9300,"end_ms":10600,"score":.85}]}
    file=tmp_path/"labels.json";file.write_text(json.dumps(labels))
    assert import_labels(work,file)["manual_events"]==3
    with connect(work) as db:
        event_id=db.execute("SELECT id FROM events ORDER BY start_ms LIMIT 1").fetchone()[0]
    plan=plan_reel(work,"ski-highlights",75)
    assert len(plan["segments"])==1
    assert plan["segments"][0]["kind"]=="jump_chain"
    assert bind_event(work,event_id,"world.manta",approve=False)["state"]=="proposed"
    out=tmp_path/"manifest.json"
    assert export_manifest(work,out)["publishedBindings"]==0
    bind_event(work,event_id,"world.ski-jump",approve=True)
    assert export_manifest(work,out)["missingMedia"]==1
    media=tmp_path/"urls.json"
    media.write_text(json.dumps({aid:{"src":"https://cdn.example.org/a.mp4",
                                       "poster":"https://cdn.example.org/a.webp"}}))
    result=export_manifest(work,out,media)
    assert result["publishedBindings"]==1
    data=json.loads(out.read_text())
    assert data["worldBindings"][0]["worldEntityId"]=="world.ski-jump"
    assert str(work) not in out.read_text()
    assert "world.manta" not in out.read_text()


def test_bad_label_range_is_rejected(tmp_path,monkeypatch):
    monkeypatch.setattr(ingestion,"probe",lambda p:{"duration":3.0,"audio":False})
    source=tmp_path/"camera";source.mkdir()
    (source/"v.mp4").write_bytes(b"dummy")
    work=tmp_path/"work";ingest(source,work)
    with connect(work) as db:
        aid=db.execute("SELECT id FROM assets").fetchone()[0]
    file=tmp_path/"labels.json"
    file.write_text(json.dumps({"asset_id":aid,"events":[{"kind":"jump","start_ms":2500,"end_ms":6500}]}))
    with pytest.raises(ValueError,match="Invalid range"):
        import_labels(work,file)

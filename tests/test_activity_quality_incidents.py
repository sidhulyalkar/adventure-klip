"""Regressions for source-safe, uncertainty-aware scene/incident metadata."""
import json
from pathlib import Path

import pytest

from adventure_intelligence.review_signals import (
    context_intervals, disturbance_proposals, manual_incident_reviews,
    diagnostic_summary, review_cues,
)
from adventure_intelligence.diagnostics import (
    overlay_cues, render_vtt, write_diagnostics,
)
from adventure_intelligence.catalog import connect
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.editorial_quality import editorial_candidates
from adventure_intelligence.episodes import build_episodes


def frame(t, *, lift=.6, downhill=.2, flat=.1, stopped=.1):
    return {"tMs": t, "sceneScores": {
        "method": "siglip2_image_text_cosine",
        "similarities": {
            "ski_lift": lift, "ski_downhill": downhill,
            "ski_flat": flat, "ski_stopped": stopped,
        },
    }}


def sample_series(*, jolt=True, recovery=True):
    samples=[]
    for i in range(44):
        value=.065
        if i == 20 and jolt:
            value=.48
        if i >= 22 and recovery:
            value=.01
        samples.append({"t_ms":i*250,"change":value,
                        "flow_x":0.25,"flow_y":0.08,
                        "flow_magnitude":.35,
                        "flow_coherence":.5})
    return samples


def test_scene_state_requires_multiple_temporally_adjacent_evidence_samples():
    frames=[frame(t) for t in range(0,13000,2000)]
    states=context_intervals(frames,"ski",14000)
    lift=[s for s in states if s["label"]=="chairlift_candidate"]
    assert len(lift)==1
    assert lift[0]["startMs"]==0
    assert lift[0]["endMs"]>=11000
    assert lift[0]["status"]=="unverified_scene_hypothesis"
    assert not context_intervals([frame(0),frame(9000),frame(18000)],"ski",20000)
    assert not context_intervals(
        [frame(t,lift=.1,downhill=.5) for t in range(0,13000,2000)],"ski",14000)


def test_low_action_flat_and_stopped_remain_hypotheses():
    flat=[frame(t,lift=.2,downhill=.18,flat=.58)
          for t in (0,2000,4000,6000,8000)]
    states=context_intervals(flat,"ski",10000)
    assert "flat_traverse_candidate" in {s["label"] for s in states}
    assert "chairlift_candidate" not in {s["label"] for s in states}


def test_camera_jolt_followed_by_pause_never_verified_as_crash():
    found=disturbance_proposals(sample_series(),duration_ms=11000)
    assert len(found)==1
    assert found[0]["label"]=="abrupt_camera_disturbance"
    assert found[0]["status"]=="requires_review"
    assert "fall" in found[0]["evidence"]["caveat"]
    assert not disturbance_proposals(sample_series(jolt=False),duration_ms=11000)
    assert not disturbance_proposals(
        sample_series(recovery=False),duration_ms=11000)


def test_manual_incident_only_from_human_annotations():
    event={"id":"x","origin":"flow","kind":"crash","start_ms":5000,
           "end_ms":6500,"score":.9}
    assert not manual_incident_reviews([event],10000)
    assert "incident_review" not in {
        e["kind"] for e in build_episodes("a", [event], 10000)}
    event["origin"]="manual"
    assert manual_incident_reviews([event],10000)[0]["status"]=="human_labeled"
    assert "incident_review" in {
        e["kind"] for e in build_episodes("a", [event], 10000)}


def test_private_overlays_are_temporally_aligned_and_public_is_empty():
    asset={"id":"hash","activity":"ski","duration":12}
    events=[{"id":"c1","origin":"manual","kind":"fall",
             "start_ms":5000,"end_ms":7000,"score":.8}]
    review=diagnostic_summary(asset,[],sample_series(),events)
    assert review["reviewedIncidents"]
    assert review["cameraDisturbances"]
    cues=overlay_cues(review,mode="review")
    assert all(c["privacy"]=="private_review" for c in cues)
    assert overlay_cues(review,mode="public")==[]
    vtt=render_vtt(cues)
    assert vtt.startswith("WEBVTT")
    assert "00:00:05." in vtt or "00:00:02." in vtt
    assert "injury" not in "\n".join(c["title"].lower() for c in cues)


def test_diagnose_writes_only_to_workspace_and_not_sources(tmp_path,monkeypatch):
    originals=tmp_path/"Ski Videos"
    originals.mkdir()
    video=originals/"DJI_SKI.MP4"
    video.write_bytes(b"do not modify source")
    monkeypatch.setattr(
        "adventure_intelligence.indexing.probe",
        lambda p: {"duration":11,"audio":False,"capture_time":None}
    )
    workspace=tmp_path/"AdventureAI"
    aid=index_folder(originals,workspace,activity="ski")["asset_ids"][0]
    output=write_diagnostics(workspace,asset_id=aid)
    assert output["generated"]==1
    payload=json.loads(Path(output["paths"][0]["json"]).read_text())
    assert payload["assetId"]==aid
    assert Path(output["paths"][0]["vtt"]).read_text().startswith("WEBVTT")
    assert list(originals.iterdir())==[video]
    assert video.read_bytes()==b"do not modify source"
    with pytest.raises(ValueError,match="inside the Klip workspace"):
        write_diagnostics(workspace,output_dir=originals)
    with pytest.raises(ValueError,match="Unknown asset ID"):
        write_diagnostics(workspace,asset_id="not-present")


def test_edits_downrank_scene_hypotheses_but_not_hard_exclude(tmp_path,monkeypatch):
    from adventure_intelligence.semantic_jobs import encoded,digest
    originals=tmp_path/"raw";originals.mkdir()
    p=originals/"DJI.MP4"
    p.write_bytes(b"original")
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda p:{"duration":30,"audio":False})
    work=tmp_path/"work"
    aid=index_folder(originals,work,activity="ski")["asset_ids"][0]
    doc={"assetId":aid,"configId":"fixture","frames":[frame(t)
        for t in range(0,30000,2000)]}
    raw=encoded(doc)
    (work/"semantics").mkdir(exist_ok=True)
    (work/"semantics"/"scene.json").write_text(raw)
    with connect(work) as db:
        db.execute("""INSERT INTO semantic_jobs
        (asset_id,config_id,config_json,status,filename,sha256)
        VALUES (?,?,'{}','completed',?,?)""",
        (aid,"fixture","scene.json",digest(raw)))
        db.execute("INSERT INTO semantic_active VALUES (?,?,?)",(aid,"context","fixture"))
        db.execute("""INSERT INTO episodes
        (id,asset_id,kind,start_ms,end_ms,score,event_ids_json,evidence_json,origin,version)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("ep-x",aid,"sustained_motion_candidate",0,30000,.99,
         "[]","{}","derived","test"))
    with connect(work) as db:
        rows=[dict(r) for r in db.execute("SELECT * FROM episodes")]
        candidates,_=editorial_candidates(db,work,rows,policy="suggest")
        assert candidates
        assert all("low_value_scene_hypothesis" in c["reasonCodes"] for c in candidates)
        assert all(c["lowValueContext"]["supportedFraction"]>.25 for c in candidates)
        assert all(c["requiresReview"] for c in candidates)
    assert p.read_bytes()==b"original"



def test_mark_appends_and_preserves_review_history(tmp_path,monkeypatch):
    from adventure_intelligence.curation import append_review_label
    from adventure_intelligence.cli import main
    folder=tmp_path/"original";folder.mkdir()
    src=folder/"DJI-0001.MP4"
    src.write_bytes(b"original bytes")
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda p:{"duration":25,"audio":False})
    work=tmp_path/"workspace"
    asset=index_folder(folder,work,activity="ski")["asset_ids"][0]
    first=append_review_label(work,asset,"ski_lift",2.,7.,note="on chairlift")
    second=append_review_label(work,asset,"fall",14.,15.5,note="fell after a turn")
    assert first["eventId"]!=second["eventId"]
    with connect(work) as db:
        events=list(db.execute("SELECT id,kind FROM events WHERE origin='manual'"))
        assert {r["kind"] for r in events}=={"ski_lift","fall"}
        assert {r["id"] for r in events}=={first["eventId"],second["eventId"]}
        assert db.execute("SELECT COUNT(*) FROM episodes WHERE kind='incident_review'").fetchone()[0]==1
    result=write_diagnostics(work,asset_id=asset)
    payload=json.loads(Path(result["paths"][0]["json"]).read_text())
    assert len(payload["reviewedIncidents"])==1
    assert any(o["category"]=="technique_review" for o in payload["overlays"])
    assert src.read_bytes()==b"original bytes"
    with pytest.raises(ValueError,match="already labeled"):
        append_review_label(work,asset,"fall",14.,15.5)
    assert main(["--workspace",str(work),"mark",asset,"--kind","crash",
                 "--start","-1","--end","2"])==2
    assert src.read_bytes()==b"original bytes"



def test_report_player_attaches_local_private_vtt_when_available(tmp_path,monkeypatch):
    from adventure_intelligence.report import write_report
    folder=tmp_path/"originals";folder.mkdir()
    source=folder/"SKI_DEMO.MP4";source.write_bytes(b"original")
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        lambda p:{"duration":12,"audio":False})
    work=tmp_path/"workspace"
    aid=index_folder(folder,work,activity="ski")["asset_ids"][0]
    write_diagnostics(work,asset_id=aid)
    path=work/"exports"/"review.html"
    write_report(work,path)
    html=path.read_text()
    assert "<video controls playsinline preload='none'>" in html
    assert "<track kind='subtitles'" in html
    assert f"diagnostics/{aid}-review.vtt" in html
    assert "SKI_DEMO.MP4" in html
    assert source.read_bytes()==b"original"



def test_acoustic_correspondence_supports_review_without_proving_crash():
    asset={"id":"example","activity":"ski","duration":11}
    signal=sample_series()
    with_sound=diagnostic_summary(asset,[],signal,[],acoustic_transients=[
        {"startMs":5000,"endMs":6000,"classification":"unknown_acoustic_cause"}])
    without_sound=diagnostic_summary(asset,[],signal,[],acoustic_transients=[])
    assert with_sound["cameraDisturbances"]
    assert with_sound["cameraDisturbances"][0]["evidence"]["acousticTransientNearJolt"]
    assert not without_sound["cameraDisturbances"][0]["evidence"]["acousticTransientNearJolt"]
    assert "Camera jolt with acoustic change" in [
        c["title"] for c in with_sound["reviewCues"]]
    assert all("injury" not in c["title"].lower()
               for c in with_sound["reviewCues"])

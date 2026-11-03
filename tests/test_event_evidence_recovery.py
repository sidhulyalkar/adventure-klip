"""Source-verified partial semantic recovery and event-centered embedding qualification."""
from io import BytesIO
import json
from pathlib import Path

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.adaptive_sampling import build_plan
from adventure_intelligence.cli import main
from adventure_intelligence.semantic_jobs import digest,encoded,completed_document,verified_document
from adventure_intelligence.scene_atlas import frames_for_asset
from adventure_intelligence.semantic_recovery import recover_semantic_frames
from adventure_intelligence.event_context import (
    _context_windows,_phase_times,_phase_pooled,build_event_context,
)


def _png():
    from PIL import Image
    image=Image.new("RGB",(384,216),(27,98,143))
    raw=BytesIO()
    image.save(raw,format="PNG")
    return raw.getvalue()


@pytest.fixture
def partial_job(tmp_path,monkeypatch):
    camera=tmp_path/"originals"
    camera.mkdir()
    source=camera/"DJI_TEST_RAY.MP4"
    raw=b"source-is-not-to-be-changed" * 1000
    source.write_bytes(raw)
    ws=tmp_path/"workspace"
    monkeypatch.setattr("adventure_intelligence.indexing.probe",
                        # Sub-millisecond durations expose the historical
                        # round-vs-truncate mismatch between planner and recovery.
                        lambda path:{"duration":90.0009,"audio":False,"capture_time":None})
    aid=index_folder(camera,ws,activity="snorkeling")["asset_ids"][0]
    plan=build_plan(ws,activity="snorkeling",limit=1,
                    coverage_seconds=15,interest_seconds=4,max_frames=24)
    assert len(plan["assets"])==1
    times=[f["tMs"] for f in plan["assets"][0]["frames"]]
    assert 3<=len(times)<=24
    planfile=ws/"exports"/"adaptive-recovery-plan.json"
    planfile.parent.mkdir(parents=True,exist_ok=True)
    planfile.write_text(json.dumps(plan))
    conf={
        "provider":"ecology-owlv2",
        "modelVersion":"mock-local-owlet",
        "providerConfig":{"resolvedRevision":"test-sha"},
        "adaptiveScheduleId":plan["planId"],
    }
    config_id=digest(encoded(conf))
    with connect(ws) as db:
        db.execute(
            "INSERT INTO semantic_jobs(asset_id,config_id,config_json,status,error) "
            "VALUES (?,?,?,'failed','FFmpeg failed to decode the requested video frame')",
            (aid,config_id,encoded(conf)))
        db.execute("INSERT INTO semantic_active VALUES (?,?,?)",
                   (aid,"ecology-owlv2",config_id))
        for t in times[:-1]:
            frame={
                "tMs":t,"requestedMs":t,"decodedPtsMs":t+20,
                "timestampBasis":"decoded_frame_pts",
                "objects":[{"label":"ray","score":.91,
                            "coordinateSpace":"sampled_image_pixels",
                            "bboxFormat":"xyxy",
                            "bbox":{"xmin":20,"ymin":20,"xmax":70,"ymax":70}}],
                "embedding":None,"keypoints":[],"notes":[],
            }
            payload=encoded(frame)
            db.execute("INSERT INTO semantic_frames VALUES(?,?,?,?,?)",
                       (aid,config_id,t,payload,digest(payload)))
        db.commit()
    return ws,aid,source,raw,planfile,times,config_id


def test_recover_19_of_20_semantics_and_do_not_mask_missing(partial_job):
    ws,aid,source,raw,planfile,times,cid=partial_job
    with connect(ws) as db:
        row=db.execute("SELECT * FROM semantic_jobs WHERE asset_id=?",(aid,)).fetchone()
        assert completed_document(ws,row) is None
    result=recover_semantic_frames(ws,aid,planfile)
    assert result["status"]=="partial_verified_source"
    assert result["availableFrames"]==len(times)-1
    assert result["missingRequestedTimesMs"]==[times[-1]]
    assert result["newInferenceCalls"]==0
    assert source.read_bytes()==raw
    with connect(ws) as db:
        job=db.execute("SELECT * FROM semantic_jobs WHERE asset_id=?",(aid,)).fetchone()
        assert job["status"]=="partial"
        assert completed_document(ws,job) is None
        doc=verified_document(ws,job,allow_partial=True)
        assert doc["coverageStatus"]=="partial_verified_source"
        assert doc["missingDoesNotMeanAbsent"] is True
        assert doc["availableFrames"]==len(times)-1
        assert frames_for_asset(db,ws,aid,"ecology-owlv2")[0]==[]
        frames,key=frames_for_asset(db,ws,aid,"ecology-owlv2",allow_partial=True)
        assert key is not None
        assert len(frames)==len(times)-1
    repeat=recover_semantic_frames(ws,aid,planfile)
    assert repeat["availableFrames"]==result["availableFrames"]


def test_no_recovery_with_unverified_or_below_threshold_evidence(partial_job):
    ws,aid,source,raw,planfile,times,cid=partial_job
    with pytest.raises(ValueError,match="maximum|coverage"):
        recover_semantic_frames(ws,aid,planfile,max_missing=1,min_fraction=.99)
    # Existing one-frame gap recovery is legal, but corrupt checkpoint is not.
    with connect(ws) as db:
        db.execute(
            "UPDATE semantic_frames SET sha256='bad' "
            "WHERE asset_id=? AND config_id=? AND t_ms=?",
            (aid,cid,times[0]))
        db.commit()
    with pytest.raises(ValueError,match="hash mismatch"):
        recover_semantic_frames(ws,aid,planfile)
    assert source.read_bytes()==raw


def test_changed_original_never_recovered(partial_job):
    ws,aid,source,raw,planfile,times,cid=partial_job
    source.write_bytes(raw+b"changed")
    with pytest.raises((OSError,RuntimeError,ValueError)):
        recover_semantic_frames(ws,aid,planfile)
    with connect(ws) as db:
        row=db.execute("SELECT * FROM semantic_jobs WHERE asset_id=?",(aid,)).fetchone()
        assert row["status"]=="failed"


def test_event_phase_pool_separates_event_and_context():
    v={
        "before":[1.,0.],
        "during":[0.,1.],
        "after":[.5,.5],
    }
    p=_phase_pooled(v)
    assert p["available"]
    assert p["eventVector"]==[0.,1.]
    assert abs(sum(x*x for x in p["contextVector"])-1)<1e-5
    assert p["visualChangeOnlyNotActionProof"]["beforeToEventCosineSimilarity"]==0
    assert _phase_pooled({"before":[1.,0.]})["available"] is False


def test_event_windows_merge_overlapping_cards_not_animal_identity():
    proposals=[{
        "id":f"ecology-owlv2:track-{i}",
        "provider":"ecology-owlv2",
        "biologicalGroup":"fish",
        "startMs":1000+i*500,"endMs":5000+i*500,
        "reviewPriority":.7,
        "evidenceRequestedTimesMs":[2000+i*500],
    } for i in range(3)]
    group=_context_windows(proposals,40000,max_events=4)
    assert len(group)==1
    assert len(group[0]["sourceProposalIds"])==3
    assert group[0]["group"]=="fish"
    assert _phase_times(group[0],40000)["during"] in {2000,2500,3000}


class FakeImageModel:
    def __init__(self,*,allow_model_download=False):
        self.revision="pinned-synthetic-revision"
        self.processor_config={"mock_image_processor":"v1"}
    def image_vectors(self,images):
        return [[1.,0.] for _ in images]


def test_context_workflow_is_bounded_and_source_read_only(partial_job,monkeypatch,capsys):
    ws,aid,source,raw,planfile,times,cid=partial_job
    recover_semantic_frames(ws,aid,planfile)
    monkeypatch.setattr("adventure_intelligence.event_context.SiglipActivityClassifier",
                        FakeImageModel)
    calls=[]
    def decode(path,t,width=384):
        assert path==source
        calls.append(t)
        return _png(),{"decodedPtsMs":t+20,"timestampBasis":"decoded_frame_pts"}
    monkeypatch.setattr("adventure_intelligence.event_context.extract_frame_png",decode)
    target=ws/"reports"/"event-evidence"
    assert main(["--workspace",str(ws),"event-context","--asset",aid,
                 "--output-dir",str(target),"--max-events","2"])==0
    report=json.loads(capsys.readouterr().out)
    assert report["events"]>=1
    assert report["decodedFrames"]<=6
    assert len(set(calls))<=6
    doc=json.loads((target/"manifest.json").read_text())
    assert doc["sourceIdentityVerifiedBeforeAndAfter"] is True
    assert doc["modelRevision"]=="pinned-synthetic-revision"
    assert all(not event["speciesVerified"] and event["animalCount"] is None
               for event in doc["events"])
    assert all(event["contextEmbedding"]["available"] for event in doc["events"])
    assert (target/"review.html").is_file()
    assert source.read_bytes()==raw
    with pytest.raises(ValueError,match="new private"):
        build_event_context(ws,aid,target,max_events=2)


def test_empty_cache_never_forces_new_video_decode(partial_job,monkeypatch):
    ws,aid,source,raw,planfile,times,cid=partial_job
    monkeypatch.setattr("adventure_intelligence.event_context.extract_frame_png",
                        lambda *a,**kw:pytest.fail("Do not decode absent object evidence"))
    target=ws/"reports"/"none"
    result=build_event_context(ws,aid,target)
    assert result["status"]=="abstained_no_cached_marine_candidate_events"
    assert not target.exists()
    assert source.read_bytes()==raw


def test_recovery_cli_with_signed_plan(partial_job,capsys):
    ws,aid,source,raw,planfile,times,cid=partial_job
    status=main(["--workspace",str(ws),"semantic-recover","--asset",aid,
                 "--plan",str(planfile),"--provider","ecology-owlv2"])
    assert status==0
    got=json.loads(capsys.readouterr().out)
    assert got["availableFrames"]==len(times)-1
    assert got["missingRequestedTimesMs"]==[times[-1]]

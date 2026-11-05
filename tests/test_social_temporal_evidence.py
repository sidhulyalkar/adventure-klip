"""Bounded Social Lens temporal evidence: synthetic and source-safe regression tests."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import shutil
import subprocess

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence import social_temporal_evidence as temporal
from adventure_intelligence.social_action_lens import (
    USER_VERSION, FEATURE_VERSION, FEATURES,
)
from adventure_intelligence.studio_preview_judge import preview_paths


def fake_frames(*,width=32,height=20,count=9):
    size=width*height
    return b"".join(bytes([(i*25+j%40)%256 for j in range(size)])
                    for i in range(count))


def test_temporal_grayscale_metrics_are_observations_not_human_quality():
    values=temporal.summarize_gray_frames(fake_frames(),width=32,height=20,fps=4)
    assert values["frameCount"]==9
    assert values["sampledDurationSec"]==2.25
    assert 0<=values["luminanceMedian"]<=1
    assert 0<=values["frameChangeMedian"]<=1
    assert len(values["changeMedianEarlyMiddleLate"])==3
    assert values["subjectOccupancy"] is None
    assert values["horizonRollDegrees"] is None
    assert values["absoluteSpeed"] is None
    assert values["temporalActionContinuityVerified"] is False
    assert values["sharpnessIsHumanVerified"] is False
    with pytest.raises(ValueError):
        temporal.summarize_gray_frames(fake_frames()[:-1],width=32,height=20)
    with pytest.raises(ValueError):
        temporal.summarize_gray_frames(fake_frames(count=4),width=32,height=20)
    with pytest.raises(ValueError):
        temporal.summarize_gray_frames(fake_frames(),width=1,height=20)
    with pytest.raises(ValueError):
        temporal.summarize_gray_frames(fake_frames(count=241),width=32,height=20)


def _workspace(tmp_path):
    ws=tmp_path/"workspace"
    source=tmp_path/"camera-original.MP4"
    source.write_bytes(b"untouched-original-binary-fixture"*20)
    aid=sha256(source.read_bytes()).hexdigest()
    with connect(ws) as db:
        db.execute("INSERT INTO assets(id,sha256,stored_path,size_bytes,suffix,"
                   "duration,activity) VALUES(?,?,?,?,?,?,?)",
                   (aid,aid,str(source),source.stat().st_size,".MP4",30.0,"ski"))
    manifest={
        "version":USER_VERSION,"featureVersion":FEATURE_VERSION,
        "items":[{"assetId":aid,"startMs":1000,"endMs":21000,
                  "sport":"ski","featureVersion":FEATURE_VERSION,
                  "featureMethod":"unassessed",
                  "features":{x:None for x in FEATURES}}],
    }
    folder=ws/"reports"/"social-rubric"
    folder.mkdir(parents=True)
    filename=folder/"feature-template.json"
    filename.write_text(json.dumps(manifest))
    return ws,source,aid,filename


def test_plan_and_run_without_previews_do_not_open_originals(tmp_path,monkeypatch):
    ws,source,aid,template=_workspace(tmp_path)
    before=sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(temporal,"decode_proxy_frames",
                        lambda _path:pytest.fail("No verified preview to decode"))
    plan=temporal.analyze_temporal_evidence(ws,template,limit=1,run=False)
    assert plan["mode"]=="cached_evidence_plan"
    assert plan["statusCounts"]=={"missing_verified_preview":1}
    assert plan["sourceOriginalReads"]==0
    assert plan["sourceOriginalReadAttempts"]==0
    assert plan["humanRubricFeaturesAutoFilled"]==0
    execution=temporal.analyze_temporal_evidence(ws,template,limit=1,run=True)
    assert execution["statusCounts"]=={"missing_verified_preview":1}
    assert execution["sourceOriginalReads"]==0
    assert execution["ffmpegVideoDecodeRequests"]==0
    assert execution["candidates"][0]["semanticTemporal"]["status"]=="unknown"
    assert execution["candidates"][0]["trueSubjectVisibility"] is None
    assert source.read_bytes() and sha256(source.read_bytes()).hexdigest()==before
    assert not (ws/"reports"/"studio-preview-cache-v1").exists()


def test_cached_verified_preview_is_analyzed_without_original_read(tmp_path,monkeypatch):
    ws,source,aid,template=_workspace(tmp_path)
    original_hash=sha256(source.read_bytes()).hexdigest()
    preview,receipt=preview_paths(ws,aid,1000,21000)
    preview.parent.mkdir(parents=True,exist_ok=True)
    video=b"valid cached preview receipt bytes"*8
    preview.write_bytes(video)
    receipt.write_text(json.dumps({
        "version":"studio-preview-local-judge-v1",
        "assetId":aid,"startMs":1000,"endMs":21000,
        "previewSha256":sha256(video).hexdigest(),
        "originalSha256Verified":True,
    }))
    recorded=[]
    def fake_decode(path):
        recorded.append(path)
        return temporal.summarize_gray_frames(
            fake_frames(width=32,height=20,count=8),width=32,height=20)
    monkeypatch.setattr(temporal,"decode_proxy_frames",fake_decode)
    result=temporal.analyze_temporal_evidence(ws,template,run=True)
    assert result["statusCounts"]=={"temporal_visual_metrics_observed":1}
    assert recorded==[preview]
    assert result["candidates"][0]["visualTemporal"]["frameCount"]==8
    assert result["sourceOriginalReads"]==0
    assert result["sourceOriginalReadAttempts"]==0
    assert result["generatedVerifiedPreviews"]==0
    assert result["humanRubricFeaturesAutoFilled"]==0
    assert sha256(source.read_bytes()).hexdigest()==original_hash
    # Tampering the preview must refuse FFmpeg rather than trusting its path.
    preview.write_bytes(video+b"bad")
    recorded.clear()
    blocked=temporal.analyze_temporal_evidence(ws,template,run=True)
    assert recorded==[]
    assert blocked["statusCounts"]=={"missing_verified_preview":1}


def test_semantic_temporal_cache_model_key_and_sparse_evidence(tmp_path,monkeypatch):
    frames=[
        {"decodedPtsMs":1200,"embedding":[1.,0.]},
        {"decodedPtsMs":2500,"embedding":[.8,.6]},
        {"decodedPtsMs":12500,"embedding":[0.,1.]},
        {"decodedPtsMs":23000,"embedding":[-1.,0.]},
    ]
    monkeypatch.setattr(temporal,"frames_for_asset",
                        lambda db,root,aid,provider:(frames,"frozen-model-key"))
    d=temporal.semantic_temporal_evidence(None,tmp_path,"a"*64,1000,21000)
    assert d["samples"]==3
    assert d["largestObservedSampleGapMs"]==10000
    assert d["minimumAdjacentCosineSimilarity"]<1
    assert d["embeddingNoveltyOrWildlifeVerified"] is False
    sparse=temporal.semantic_temporal_evidence(None,tmp_path,"a"*64,18000,26000)
    assert sparse["status"]=="insufficient_temporal_samples"
    monkeypatch.setattr(temporal,"frames_for_asset",
                        lambda db,root,aid,provider:(frames,None))
    assert temporal.semantic_temporal_evidence(
        None,tmp_path,"a"*64,1000,21000)["status"]=="unknown"


def test_invalid_templates_and_unrequested_preview_opt_in(tmp_path):
    ws,source,aid,file=_workspace(tmp_path)
    doc=json.loads(file.read_text())
    changed=json.loads(json.dumps(doc))
    changed["items"].append(changed["items"][0])
    with pytest.raises(ValueError,match="Duplicated"):
        temporal._validate_candidate(changed)
    changed=json.loads(json.dumps(doc))
    changed["items"][0]["endMs"]=99999
    with pytest.raises(ValueError):
        temporal._validate_candidate(changed)
    changed=json.loads(json.dumps(doc))
    changed["items"][0]["sport"]="alien"
    with pytest.raises(ValueError):
        temporal._validate_candidate(changed)
    with pytest.raises(ValueError,match="requires --run"):
        temporal.analyze_temporal_evidence(ws,file,prepare_previews=True)
    with pytest.raises(ValueError):
        temporal.analyze_temporal_evidence(ws,tmp_path/"outside.json")
    with pytest.raises(ValueError):
        temporal.analyze_temporal_evidence(ws,file,limit=0)


def test_save_produces_private_report_and_remains_non_destructive(tmp_path):
    ws,source,aid,file=_workspace(tmp_path)
    before=sha256(source.read_bytes()).hexdigest()
    target=ws/"reports"/"temporal-plan"
    result=temporal.save_temporal_evidence(ws,file,target,run=False)
    assert result["status"]=="temporal_evidence_report_created"
    assert result["measured"]==0
    assert result["sourceOriginalReads"]==0
    assert result["externalUploads"]==0
    assert result["humanRubricFeaturesAutoFilled"]==0
    document=json.loads(Path(result["json"]).read_text())
    assert document["requested"]==1
    assert document["externalUploads"]==0
    assert document["candidates"][0]["featureVersion"]==FEATURE_VERSION
    assert "humanRubricFieldsStillUnassessed" in document["candidates"][0]
    assert "Social Lens" in Path(result["html"]).read_text()
    with pytest.raises(ValueError,match="NEW"):
        temporal.save_temporal_evidence(ws,file,target,run=False)
    assert sha256(source.read_bytes()).hexdigest()==before


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="FFmpeg required for bounded real-preview integration")
def test_opt_in_prepares_verified_preview_from_synthetic_video_only(tmp_path):
    ws=tmp_path/"real-video-workspace"
    source=tmp_path/"generated-synthetic.MP4"
    ffmpeg=shutil.which("ffmpeg")
    generation=subprocess.run([
        ffmpeg,"-hide_banner","-loglevel","error","-nostdin","-y",
        "-f","lavfi","-i","testsrc2=size=320x180:rate=12",
        "-t","7","-c:v","libx264","-preset","ultrafast",
        "-pix_fmt","yuv420p",str(source)],
        stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=45,check=False)
    if generation.returncode:
        pytest.skip("CI FFmpeg build cannot generate synthetic H264 test")
    original_hash=sha256(source.read_bytes()).hexdigest()
    with connect(ws) as db:
        db.execute("INSERT INTO assets(id,sha256,stored_path,size_bytes,suffix,"
                   "duration,activity) VALUES(?,?,?,?,?,?,?)",
                   (original_hash,original_hash,str(source),source.stat().st_size,
                    ".MP4",7.0,"bike"))
    feature_file=ws/"reports"/"rubric"/"feature-template.json"
    feature_file.parent.mkdir(parents=True)
    feature_file.write_text(json.dumps({
        "version":USER_VERSION,"featureVersion":FEATURE_VERSION,
        "items":[{
            "assetId":original_hash,"startMs":500,"endMs":5500,
            "sport":"bike","featureVersion":FEATURE_VERSION,
            "featureMethod":"unassessed",
            "features":{x:None for x in FEATURES},
        }],
    }))
    result=temporal.analyze_temporal_evidence(ws,feature_file,limit=1,
                                              run=True,prepare_previews=True)
    assert result["failed"]==0
    assert result["statusCounts"]=={"temporal_visual_metrics_observed":1}
    assert result["generatedVerifiedPreviews"]==1
    assert result["sourceOriginalReads"]==1
    assert result["sourceOriginalReadAttempts"]==1
    assert result["humanRubricFeaturesAutoFilled"]==0
    visual=result["candidates"][0]["visualTemporal"]
    assert 5<=visual["frameCount"]<=24
    assert 0<=visual["frameChangeMedian"]<=1
    assert visual["horizonRollDegrees"] is None
    assert sha256(source.read_bytes()).hexdigest()==original_hash
    again=temporal.analyze_temporal_evidence(ws,feature_file,limit=1,run=True)
    assert again["failed"]==0
    assert again["sourceOriginalReads"]==0
    assert again["sourceOriginalReadAttempts"]==0
    assert again["generatedVerifiedPreviews"]==0


def test_offline_report_displays_only_checksum_verified_local_video(tmp_path,monkeypatch):
    ws,source,aid,feature_file=_workspace(tmp_path)
    preview,receipt=preview_paths(ws,aid,1000,21000)
    preview.parent.mkdir(parents=True,exist_ok=True)
    original=b"not an actual mp4 but receipt verified preview cache"
    preview.write_bytes(original*4)
    receipt.write_text(json.dumps({
        "version":"studio-preview-local-judge-v1",
        "assetId":aid,"startMs":1000,"endMs":21000,
        "previewSha256":sha256(preview.read_bytes()).hexdigest(),
        "originalSha256Verified":True,
    }))
    monkeypatch.setattr(temporal,"decode_proxy_frames",
                        lambda path:temporal.summarize_gray_frames(
                            fake_frames(width=32,height=20,count=8),
                            width=32,height=20))
    target=ws/"reports"/"with-local-playback"
    saved=temporal.save_temporal_evidence(ws,feature_file,target,run=True)
    html=Path(saved["html"]).read_text()
    assert "Play verified interval" in html
    assert "controls preload=" in html
    assert "../studio-preview-cache-v1/" in html
    assert "http://" not in html and "https://" not in html
    assert str(source) not in html
    assert "No calibrated" not in html
    preview.write_bytes(b"tampered preview"*8)
    no_video=temporal.save_temporal_evidence(
        ws,feature_file,ws/"reports"/"without-local-playback",run=False)
    blocked_html=Path(no_video["html"]).read_text()
    assert "Play verified interval" not in blocked_html


def _second_candidate(template: Path):
    doc=json.loads(template.read_text())
    other=dict(doc["items"][0])
    other["startMs"]=3000
    other["endMs"]=16000
    doc["items"].append(other)
    template.write_text(json.dumps(doc))
    return doc


def test_interrupt_then_resume_skips_in_flight_source_without_reopening(tmp_path,monkeypatch):
    ws,original,aid,template=_workspace(tmp_path)
    _second_candidate(template)
    saved=sha256(original.read_bytes()).hexdigest()
    target=ws/"reports"/"temporal-interrupted"
    calls=[]
    def simulate(_db,_root,c,**kwargs):
        calls.append(c["startMs"])
        if c["startMs"]==3000:
            raise KeyboardInterrupt("simulate stuck removable media")
        return {
            "assetId":c["assetId"],"sport":c["sport"],
            "startMs":c["startMs"],"endMs":c["endMs"],
            "previewVerified":False,"sourceOriginalRead":False,
            "previewPreparedThisRun":False,"status":"missing_verified_preview",
        }
    monkeypatch.setattr(temporal,"_analysis_row",simulate)
    with pytest.raises(KeyboardInterrupt):
        temporal.save_temporal_evidence(ws,template,target,limit=2,run=True,
                                        prepare_previews=True)
    assert calls==[1000,3000]
    assert not (target/"temporal-evidence.json").exists()
    journal=list((target/"checkpoints").glob("*.json"))
    assert len(journal)==2
    assert {json.loads(x.read_text())["state"] for x in journal}=={
        "success","in_progress"}
    def never_reopen(*args,**kwargs):
        pytest.fail("Safe resume must not reopen either source or completed item")
    monkeypatch.setattr(temporal,"_analysis_row",never_reopen)
    resumed=temporal.save_temporal_evidence(ws,template,target,limit=2,
                                           run=True,prepare_previews=True,
                                           resume=True)
    result=json.loads(Path(resumed["json"]).read_text())
    assert result["requested"]==2
    assert result["reported"]==1
    assert result["failed"]==1
    assert result["checkpoint"]["resumed"] is True
    assert result["checkpoint"]["reusedSuccessfulCandidates"]==1
    assert result["checkpoint"]["interruptedCandidatesSkipped"]==1
    assert result["failures"][0]["errorType"]=="InterruptedBeforeCheckpoint"
    assert result["sourceOriginalReadAttempts"]==2
    assert result["externalUploads"]==0
    assert sha256(original.read_bytes()).hexdigest()==saved
    with pytest.raises(ValueError,match="Completed report exists"):
        temporal.save_temporal_evidence(ws,template,target,limit=2,
                                       run=True,prepare_previews=True,resume=True)


def test_explicit_retry_interrupted_runs_only_unfinished_candidate(tmp_path,monkeypatch):
    ws,original,aid,template=_workspace(tmp_path)
    _second_candidate(template)
    target=ws/"reports"/"retry-interrupted"
    attempts=[]
    def fake(_db,_root,c,**kwargs):
        attempts.append(c["startMs"])
        if c["startMs"]==3000 and attempts.count(3000)==1:
            raise KeyboardInterrupt()
        return {"assetId":c["assetId"],"sport":c["sport"],
                "startMs":c["startMs"],"endMs":c["endMs"],
                "previewVerified":False,"sourceOriginalRead":False,
                "previewPreparedThisRun":False,"status":"missing_verified_preview"}
    monkeypatch.setattr(temporal,"_analysis_row",fake)
    with pytest.raises(KeyboardInterrupt):
        temporal.save_temporal_evidence(ws,template,target,limit=2,
                                       run=True,prepare_previews=True)
    result=temporal.save_temporal_evidence(ws,template,target,limit=2,
                    run=True,prepare_previews=True,resume=True,retry_interrupted=True)
    assert result["failed"]==0
    assert result["checkpoint"]["reusedSuccessfulCandidates"]==1
    assert attempts==[1000,3000,3000]
    assert result["sourceOriginalReadAttempts"]==2


def test_resume_rejects_changed_template_limit_or_flags_and_retains_journal(tmp_path,monkeypatch):
    ws,_,_,template=_workspace(tmp_path)
    _second_candidate(template)
    target=ws/"reports"/"frozen-run"
    def aborted(*args,**kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(temporal,"_analysis_row",aborted)
    with pytest.raises(KeyboardInterrupt):
        temporal.save_temporal_evidence(ws,template,target,limit=2,run=True)
    original=(target/"checkpoint.json").read_bytes()
    with pytest.raises(ValueError,match="differ"):
        temporal.save_temporal_evidence(ws,template,target,limit=1,run=True,
                                       resume=True)
    with pytest.raises(ValueError,match="differ"):
        temporal.save_temporal_evidence(ws,template,target,limit=2,run=False,
                                       resume=True)
    doc=json.loads(template.read_text())
    doc["items"][0]["sport"]="bike"
    template.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match="differ"):
        temporal.save_temporal_evidence(ws,template,target,limit=2,run=True,
                                       resume=True)
    assert original==(target/"checkpoint.json").read_bytes()
    assert not (target/"temporal-evidence.json").exists()
    with pytest.raises(ValueError,match="require --resume"):
        temporal.save_temporal_evidence(ws,template,ws/"reports"/"oops",
                                       retry_interrupted=True)


def test_resume_does_not_reuse_completed_metrics_from_tampered_preview(tmp_path,monkeypatch):
    ws,source,aid,template=_workspace(tmp_path)
    _second_candidate(template)
    preview,receipt=preview_paths(ws,aid,1000,21000)
    preview.parent.mkdir(parents=True,exist_ok=True)
    data=b"valid-h264-placeholder"*20
    preview.write_bytes(data)
    receipt.write_text(json.dumps({
        "version":"studio-preview-local-judge-v1",
        "assetId":aid,"startMs":1000,"endMs":21000,
        "previewSha256":sha256(data).hexdigest(),
        "originalSha256Verified":True,
    }))
    monkeypatch.setattr(temporal,"decode_proxy_frames",
                        lambda path:{"frameCount":8})
    real=temporal._analysis_row
    def interrupt_second(db,root,c,**kwargs):
        if c["startMs"]==3000:
            raise KeyboardInterrupt()
        return real(db,root,c,**kwargs)
    monkeypatch.setattr(temporal,"_analysis_row",interrupt_second)
    target=ws/"reports"/"tamper-check"
    with pytest.raises(KeyboardInterrupt):
        temporal.save_temporal_evidence(ws,template,target,limit=2,run=True)
    preview.write_bytes(data+b"tamper")
    results=[]
    def record_reanalyze(db,root,c,**kwargs):
        results.append(c["startMs"])
        return real(db,root,c,**kwargs)
    monkeypatch.setattr(temporal,"_analysis_row",record_reanalyze)
    resumed=temporal.save_temporal_evidence(ws,template,target,limit=2,
                                            run=True,resume=True)
    assert resumed["measured"]==0
    assert resumed["checkpoint"]["reusedSuccessfulCandidates"]==0
    assert resumed["checkpoint"]["interruptedCandidatesSkipped"]==1
    assert results==[1000]
    assert json.loads(Path(resumed["json"]).read_text())["candidates"][0][
        "status"]=="missing_verified_preview"


def test_explicit_retry_failures_is_separate_from_stalled_source_retry(tmp_path,monkeypatch):
    ws,_,_,template=_workspace(tmp_path)
    target=ws/"reports"/"record-failure"
    counter={"n":0}
    def one_failure(*args,**kwargs):
        counter["n"]+=1
        raise OSError("synthetic failure")
    monkeypatch.setattr(temporal,"_analysis_row",one_failure)
    with pytest.raises(KeyboardInterrupt):
        # Save a failure checkpoint, then interrupt before the final report.
        original=temporal._atomic_json
        def interrupt_final(path,body):
            if path.name=="temporal-evidence.json":
                raise KeyboardInterrupt()
            return original(path,body)
        monkeypatch.setattr(temporal,"_atomic_json",interrupt_final)
        temporal.save_temporal_evidence(ws,template,target,run=True)
    assert counter["n"]==1
    monkeypatch.setattr(temporal,"_atomic_json",original)
    no_retry=temporal.save_temporal_evidence(ws,template,target,run=True,
                                             resume=True)
    assert no_retry["failed"]==1
    assert counter["n"]==1
    assert no_retry["checkpoint"]["retainedPreviousFailures"]==1
    assert not Path(no_retry["json"]).read_text().find("synthetic failure")==-1

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

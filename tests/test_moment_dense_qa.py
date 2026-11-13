"""Regression contracts for private dense-frame QA and visual-change baselines.

Small JPEGs are generated in a temporary workspace. No original video,
model download, GPU, network request or catalog write is needed.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from PIL import Image
import pytest

from adventure_intelligence.moment_adaptive_inspection import VERSION as PLAN_VERSION
from adventure_intelligence.moment_dense_decode import (
    VERSION as DECODE_VERSION,_id,_sha,
)
from adventure_intelligence.moment_dense_qa import (
    VERSION,analyze_dense,_visual_change,
)
from adventure_intelligence.workspace import init


@pytest.fixture
def derived(tmp_path):
    root=tmp_path/"workspace"
    init(root)
    (root/"reports").mkdir()
    source="b"*64
    times=[0,250,500,750,1000,1250,1500,1750]
    request={
        "status":"inspection_requested",
        "sourceSha256":source,
        "expandedStartMs":0,"expandedEndMs":2000,
        "coreStartMs":250,"coreEndMs":1750,
        "selectionType":"ranked","sportHint":"ski",
        "requestedTimesMs":times,"plannedFrameCount":len(times),
    }
    plan={"version":PLAN_VERSION,"plannedClips":1,
          "requestedFrames":len(times),"inspections":[request]}
    plan_file=root/"reports"/"kmoment-dense-inspection-plan-v1.json"
    plan_file.write_text(json.dumps(plan))
    import hashlib
    plan_sha=hashlib.sha256(plan_file.read_bytes()).hexdigest()
    folder=root/"reports"/"kmoment-dense-test-001"
    cid=_id(request)
    dest=folder/"clips"/cid
    (dest/"frames").mkdir(parents=True)
    entries=[]
    for i,t in enumerate(times):
        img=Image.new("RGB",(160,90),
                      (230,40,40) if i<4 else (40,180,220))
        name=f"frame-{i:04d}.jpg"
        img.save(dest/"frames"/name)
        path=dest/"frames"/name
        entries.append({
            "requestedSourceRelativeMs":t,
            "sampledFilterSourceRelativeMs":t,
            "gridOffsetFromRequestMs":0,
            "relativePath":"frames/"+name,
            "sha256":_sha(path),
            "bytes":path.stat().st_size,
            "nativeDecodedFramePtsAvailable":False,
        })
    receipt={
        "version":DECODE_VERSION,
        "planSha256":plan_sha,
        "sourceSha256":source,
        "clipId":cid,
        "expandedStartMs":0,"expandedEndMs":2000,
        "coreStartMs":250,"coreEndMs":1750,
        "requestedFrames":len(times),"observedFrames":len(times),
        "unobservedRequestedTimesMs":[],
        "frames":entries,
        "sourceVerifiedBeforeAndAfter":True,
        "nativeOriginalFramePtsMeasured":False,
    }
    (dest/"receipt.json").write_text(json.dumps(receipt))
    run={
        "version":DECODE_VERSION,
        "planSha256":plan_sha,
        "maxClips":1,"maxFrames":20,"width":384,
        "requestedFrames":len(times),
        "clips":[{"clipId":cid,"sourceSha256":source,
                 "receiptRelativePath":"clips/"+cid+"/receipt.json"}],
        "failures":[],
    }
    (folder/"run.json").write_text(json.dumps(run))
    return root,plan_file,cid,dest


def go(root,plan_file,backend="storyboards",*,run=False,encoder=None,
       name="kmoment-qa-test-001"):
    return analyze_dense(root,plan_file,decode_name="kmoment-dense-test-001",
                         analysis_name=name,backend=backend,
                         batch_size=3,run=run,encoder=encoder)


def test_preflight_no_creation_and_contact_sheet_is_source_free(derived):
    root,plan,cid,source=derived
    original=[p.read_bytes() for p in sorted((source/"frames").glob("*.jpg"))]
    qa=go(root,plan)
    assert qa["plannedClips"]==1
    assert qa["decodedFrames"]==8
    assert qa["preflightOnly"] is True
    assert qa["sourceVideoRead"] is False
    assert not (root/"reports"/"kmoment-qa-test-001").exists()
    output=go(root,plan,run=True)
    assert output["completedClips"]==1
    assert output["connectedPairsAt7000Ms"]==7
    assert output["modelInferencePerformed"] is False
    dest=root/"reports"/"kmoment-qa-test-001"/cid
    assert (dest/"storyboard.jpg").is_file()
    doc=json.loads((dest/"analysis.json").read_text())
    assert doc["storyboard"]["framesDisplayed"]==8
    assert doc["temporal"]["visualChangeCandidates"] is None
    assert doc["temporal"]["clipBoundaryReady"] is False
    assert [p.read_bytes() for p in sorted((source/"frames").glob("*.jpg"))]==original
    again=go(root,plan,run=True)
    assert again["results"][0]["status"]=="reused_verified_analysis"


def test_visual_change_proposals_are_temporally_connected_not_events():
    times=[0,250,500,750,1000]
    vectors=[
        [1.,0,0,0,0,0,0,0],
        [1.,0,0,0,0,0,0,0],
        [0.,1.,0,0,0,0,0,0],
        [0.,1.,0,0,0,0,0,0],
        [0.,1.,0,0,0,0,0,0],
    ]
    report=_visual_change(times,vectors)
    assert report["connectedPairsAt7000Ms"]==4
    assert report["visualChangeCandidates"][0]["atSourceRelativeMs"]==500
    assert report["actionAndEventPhasesVerified"] is False
    assert report["clipBoundaryReady"] is False
    sparse=_visual_change([0,8000,16000],vectors[:3])
    assert sparse["connectedPairsAt7000Ms"]==0
    assert sparse["visualChangeCandidates"]==[]
    with pytest.raises(ValueError,match="Invalid temporal"):
        _visual_change([250,100],vectors[:2])


class FakeEncoder:
    def __init__(self):
        self.calls=0
    def infer(self,paths):
        self.calls+=1
        vectors=[]
        for path in paths:
            n=int(path.stem.split("-")[-1])
            vectors.append([1.]+[0.]*7 if n<4 else [0.,1.]+[0.]*6)
        return vectors
    def model_key(self,dim):
        assert dim==8
        return "synthetic-shared-frozen-v1"


def test_batched_opt_in_fake_encoder_and_derived_frozen_vectors(derived):
    root,plan,cid,_=derived
    encoder=FakeEncoder()
    qa=go(root,plan,backend="siglip2",run=True,encoder=encoder,
          name="kmoment-qa-features-001")
    assert not qa["errors"]
    assert qa["modelInferencePerformed"] is True
    assert encoder.calls==3 # batches of 3,3,2
    assert qa["connectedPairsAt7000Ms"]==7
    folder=root/"reports"/"kmoment-qa-features-001"/cid
    doc=json.loads((folder/"analysis.json").read_text())
    assert doc["frozenModelKey"]=="synthetic-shared-frozen-v1"
    assert doc["temporal"]["visualChangeCandidates"]
    assert doc["temporal"]["clipBoundaryReady"] is False
    feats=json.loads((folder/"frozen_features.json").read_text())
    assert feats["frameCount"]==8
    assert len(feats["observations"])==8
    encoder2=FakeEncoder()
    cache=go(root,plan,backend="siglip2",run=True,encoder=encoder2,
             name="kmoment-qa-features-001")
    assert cache["results"][0]["status"]=="reused_verified_analysis"
    assert encoder2.calls==0


def test_bad_derived_image_and_mutated_qa_cache_fail_closed(derived):
    root,plan,cid,source=derived
    (source/"frames"/"frame-0002.jpg").write_bytes(b"tampered")
    with pytest.raises(ValueError,match="receipt or JPEG checksum"):
        go(root,plan)
    # Restore receipt digest only after reverting original synthetic frame.
    im=Image.new("RGB",(160,90),(230,40,40))
    im.save(source/"frames"/"frame-0002.jpg")
    assert go(root,plan,run=True)["completedClips"]==1
    sheet=root/"reports"/"kmoment-qa-test-001"/cid/"storyboard.jpg"
    sheet.write_bytes(b"tampered")
    after=go(root,plan,run=True)
    assert after["errors"][0]["reason"]=="existing_derived_artifact_invalid_refuse_overwrite"


def test_no_untrusted_manifest_run_or_symlink(derived):
    root,plan,cid,source=derived
    receipt=source/"receipt.json"
    data=json.loads(receipt.read_text())
    data["planSha256"]="f"*64
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        go(root,plan)
    data["planSha256"]=__import__("hashlib").sha256(plan.read_bytes()).hexdigest()
    receipt.write_text(json.dumps(data))
    summary=root/"reports"/"kmoment-dense-test-001"/"run.json"
    summary.unlink()
    outside=root.parent/"bad.json"
    outside.write_text(json.dumps({"version":DECODE_VERSION}))
    summary.symlink_to(outside)
    with pytest.raises(ValueError,match="Missing or unsafe"):
        go(root,plan)


def test_cli_storyboard_run(derived):
    root,plan,_,_=derived
    project=Path(__file__).resolve().parents[1]
    script=project/"scripts"/"klip_dense_visual_qa.py"
    result=subprocess.run([
        sys.executable,str(script),
        "--workspace",str(root),
        "--plan",str(plan),
        "--decode-name","kmoment-dense-test-001",
        "--name","kmoment-qa-test-002",
        "--backend","storyboards","--run",
    ],capture_output=True,text=True,cwd=project)
    assert result.returncode==0,result.stderr
    output=json.loads(result.stdout)
    assert output["completedClips"]==1
    assert output["sourceVideoRead"] is False

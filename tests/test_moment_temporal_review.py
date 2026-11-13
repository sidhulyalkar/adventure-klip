"""Offline K-MOMENT review UI: verified JPEG-only inputs and human labels.

Uses synthetic stills and a fake encoder. Neither original media,
pretrained model, network or external storage are needed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

from PIL import Image
import pytest

from adventure_intelligence.moment_adaptive_inspection import VERSION as PLAN_VERSION
from adventure_intelligence.moment_dense_decode import VERSION as DECODE_VERSION, _id, _sha
from adventure_intelligence.moment_dense_qa import analyze_dense
from adventure_intelligence.moment_temporal_review import (
    VERSION, build_temporal_review,
)
from adventure_intelligence.workspace import init


class FakeEncoder:
    def infer(self,paths):
        answer=[]
        for p in paths:
            idx=int(p.stem.split("-")[-1])
            answer.append(([1.,0.]+[0.]*6) if idx<3 else ([0.,1.]+[0.]*6))
        return answer

    def model_key(self,dim):
        assert dim==8
        return "local-test-model"


@pytest.fixture
def inputs(tmp_path):
    root=tmp_path/"workspace"
    init(root)
    reports=root/"reports"
    reports.mkdir()
    source_id="b"*64
    times=list(range(1000,3000,250))
    row={
        "status":"inspection_requested",
        "sourceSha256":source_id,
        "expandedStartMs":1000,"expandedEndMs":3000,
        "coreStartMs":1250,"coreEndMs":2750,
        "selectionType":"ranked","sportHint":"ski",
        "requestedTimesMs":times,"plannedFrameCount":len(times),
    }
    plan={"version":PLAN_VERSION,"plannedClips":1,
          "requestedFrames":len(times),"inspections":[row]}
    plan_file=reports/"kmoment-dense-inspection-plan-v1.json"
    plan_file.write_text(json.dumps(plan))
    plan_sha=hashlib.sha256(plan_file.read_bytes()).hexdigest()
    cid=_id(row)
    folder=reports/"kmoment-dense-test-001"/"clips"/cid
    (folder/"frames").mkdir(parents=True)
    frames=[]
    for index,t in enumerate(times):
        path=folder/"frames"/f"frame-{index:04d}.jpg"
        Image.new("RGB",(160,90),(15,170,110) if index<3
                  else (180,40,70)).save(path)
        frames.append({
            "requestedSourceRelativeMs":t,
            "sampledFilterSourceRelativeMs":t,
            "gridOffsetFromRequestMs":0,
            "relativePath":"frames/"+path.name,
            "sha256":_sha(path),"bytes":path.stat().st_size,
        })
    receipt={
        "version":DECODE_VERSION,"planSha256":plan_sha,
        "sourceSha256":source_id,"clipId":cid,
        "expandedStartMs":1000,"expandedEndMs":3000,
        "coreStartMs":1250,"coreEndMs":2750,
        "requestedFrames":len(times),"observedFrames":len(times),
        "unobservedRequestedTimesMs":[],
        "frames":frames,"sourceVerifiedBeforeAndAfter":True,
    }
    (folder/"receipt.json").write_text(json.dumps(receipt))
    manifest={
        "version":DECODE_VERSION,"planSha256":plan_sha,
        "maxClips":1,"maxFrames":24,"width":384,
        "requestedFrames":len(times),
        "clips":[{"clipId":cid,"sourceSha256":source_id,
                  "receiptRelativePath":"clips/"+cid+"/receipt.json"}],
        "failures":[],
    }
    (reports/"kmoment-dense-test-001"/"run.json").write_text(json.dumps(manifest))
    qa=analyze_dense(
        root,plan_file,
        decode_name="kmoment-dense-test-001",
        analysis_name="kmoment-qa-test-001",
        backend="siglip2",run=True,encoder=FakeEncoder(),batch_size=3)
    assert not qa["errors"]
    assert qa["completedClips"]==1
    return root,plan_file,cid,folder


def build(inputs, name="kmoment-review-test-001"):
    root,plan,_,_=inputs
    return build_temporal_review(
        root,plan,
        decode_name="kmoment-dense-test-001",
        qa_name="kmoment-qa-test-001",
        review_name=name)


def test_review_build_creates_local_html_with_verified_relative_jpegs(inputs):
    root,plan,cid,images=inputs
    source_values=[f.read_bytes() for f in sorted((images/"frames").glob("*.jpg"))]
    source_db=root/"catalog.sqlite3"
    assert not source_db.exists()
    result=build(inputs)
    assert result["status"]=="created"
    assert result["clips"]==1
    assert result["observedFrames"]==8
    assert result["sourceVideoRead"] is False
    assert result["modelInferencePerformed"] is False
    assert result["localCatalogModified"] is False
    assert result["humanReviewDownloadOnly"] is True
    page=Path(result["reviewPage"])
    assert page.is_file()
    html=page.read_text()
    assert '<script id="klip-review-data" type="application/json">' in html
    assert "<!--REVIEW_DATA-->" not in html
    match=re.search(r'<script id="klip-review-data" type="application/json">(.*?)</script>',
                    html,re.DOTALL)
    assert match
    data=json.loads(match.group(1))
    assert data["version"]==VERSION
    assert data["observedFrames"]==8
    assert data["clips"][0]["clipId"]==cid
    assert data["clips"][0]["sourceSha256"]=="b"*64
    assert data["clips"][0]["modelKey"]=="local-test-model"
    assert len(data["clips"][0]["visualChangeCandidates"])>=1
    assert len(data["clips"][0]["frames"])==8
    assert all(f["source"].startswith("../kmoment-dense-test-001/clips/")
               for f in data["clips"][0]["frames"])
    assert all("/original" not in f["source"] for f in data["clips"][0]["frames"])
    assert (page.parent/"temporal_review.js").is_file()
    assert not source_db.exists()
    assert [f.read_bytes() for f in sorted((images/"frames").glob("*.jpg"))]==source_values
    again=build(inputs)
    assert again["status"]=="reused_unchanged_page"


def test_corrupted_derived_jpeg_fails_review_before_html_creation(inputs):
    root,_,_,images=inputs
    image=images/"frames"/"frame-0001.jpg"
    image.write_bytes(b"corrupt-jpeg")
    with pytest.raises(ValueError,match="checksum"):
        build(inputs)
    assert not (root/"reports"/"kmoment-review-test-001").exists()


def test_unexpected_source_model_and_existing_page_mutation_fail_closed(inputs):
    root,_,cid,_=inputs
    qa=root/"reports"/"kmoment-qa-test-001"/cid/"analysis.json"
    data=json.loads(qa.read_text())
    data["sourceSha256"]="c"*64
    qa.write_text(json.dumps(data))
    with pytest.raises(ValueError,match="identity"):
        build(inputs)
    data["sourceSha256"]="b"*64
    qa.write_text(json.dumps(data))
    assert build(inputs)["status"]=="created"
    html=root/"reports"/"kmoment-review-test-001"/"review.html"
    html.write_text("overwritten")
    with pytest.raises(ValueError,match="differs"):
        build(inputs)
    assert html.read_text()=="overwritten"


def test_wrong_plan_or_uncertified_qa_rejected(inputs):
    root,plan,cid,_=inputs
    alt=root/"reports"/"other-plan.json"
    data=json.loads(plan.read_text())
    data["inspections"][0]["sourceSha256"]="d"*64
    alt.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        build_temporal_review(
            root,alt,decode_name="kmoment-dense-test-001",
            qa_name="kmoment-qa-test-001")
    summary=root/"reports"/"kmoment-qa-test-001"/"summary.json"
    val=json.loads(summary.read_text())
    val["errors"]=[{"reason":"bad"}]
    summary.write_text(json.dumps(val))
    with pytest.raises(ValueError,match="does not match"):
        build(inputs)


def test_cli_builds_review_page(inputs):
    root,plan,_,_=inputs
    project=Path(__file__).resolve().parents[1]
    script=project/"scripts"/"klip_build_temporal_review.py"
    p=subprocess.run([
        sys.executable,str(script),
        "--workspace",str(root),
        "--plan",str(plan),
        "--decode-name","kmoment-dense-test-001",
        "--qa-name","kmoment-qa-test-001",
        "--name","kmoment-review-cli-001",
    ],cwd=project,capture_output=True,text=True)
    assert p.returncode==0,p.stderr
    result=json.loads(p.stdout)
    assert result["clips"]==1
    assert Path(result["reviewPage"]).is_file()

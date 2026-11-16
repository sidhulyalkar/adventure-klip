"""Synthetic verification for expanding one 20-second preview to full ski-run context.

No user footage, external SSD or network is required. FFmpeg test is optional.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from adventure_intelligence import auto_scout_v2 as scout
from adventure_intelligence import full_archive_campaign as campaign
from adventure_intelligence import ski_run_context as run
from adventure_intelligence.ski_run_context_studio import page
from adventure_intelligence.moment_edit_v2 import VERSION as MOMENT_VERSION


SOURCE = "2efff9077808" + "a" * 52
RECORD = "c" * 24
PROXY_SHA = "b" * 64


def anchor():
    return {
        "recordId": RECORD, "sourceSha256": SOURCE,
        "proxySha256": PROXY_SHA, "sourceStartMs": 16000, "sourceEndMs": 36000,
    }


def test_hard_coded_twenty_second_discovery_root_cause():
    old = campaign._proposals(
        [{"t_ms":25000,"change":0.9},
         {"t_ms":95000,"change":0.7}], 220000
    )
    assert old and all(x["endMs"]-x["startMs"]<=20000 for x in old)
    assert any(x["role"]=="motion_change_hypothesis" for x in old)
    # No existing 20s window contains all jumps in the synthetic 90s line.
    assert not any(x["startMs"]<=25000 and x["endMs"]>=95000 for x in old)


def test_source_context_preserves_third_jump_beyond_old_clip():
    a=anchor()
    ctx=run.plan_context(a,230000,before_s=40,after_s=180,max_s=240)
    assert ctx["contextSourceStartMs"]==0
    assert ctx["contextSourceEndMs"]==216000
    assert ctx["anchorSourceStartMs"]==16000
    assert ctx["anchorSourceEndMs"]==36000
    assert ctx["extensionAfterMs"]==180000
    assert ctx["continuousAthleticRunVerified"] is False
    assert ctx["nativePtsAlignmentVerified"] is False
    annotation=run.owner_annotation_template(ctx)
    annotation.update(
        runStartMs=5000,runEndMs=182000,
        runKind="terrain_park",preference="keep",
        reviewerVerifiedContinuousRun=True,
        featureMarkers=[
            {"kind":"jump","sourceMs":26000},
            {"kind":"box","sourceMs":65000},
            {"kind":"jump","sourceMs":138000},
        ],
    )
    assert run.validate_owner_run(annotation,ctx)==annotation
    assert annotation["featureMarkers"][-1]["sourceMs"]>a["sourceEndMs"]


def test_context_clamps_to_source_boundary_and_pins_original_anchor():
    plan=run.plan_context(anchor(),44000,before_s=40,after_s=180)
    assert plan["contextSourceEndMs"]==44000
    assert plan["contextDurationMs"]==44000
    with pytest.raises(ValueError,match="exceeds context budget"):
        run.plan_context(
            {**anchor(), "sourceStartMs": 1000, "sourceEndMs": 32000},
            120000, max_s=30
        )


def test_context_fails_on_ambiguous_or_mismatched_anchors():
    rows=[anchor(),dict(anchor(),recordId="d"*24)]
    with pytest.raises(ValueError,match="found 2"):
        run.match_anchor(rows,"2efff9077808",16000,36000)
    with pytest.raises(ValueError,match="found 0"):
        run.match_anchor(rows[:1],"2efff9077808",22000,42000)
    with pytest.raises(ValueError,match="prefix"):
        run.match_anchor(rows[:1],"foo",16000,36000)


@pytest.mark.parametrize("change",[
    lambda r:r.update(runStartMs=12000,runEndMs=None),
    lambda r:r.update(runStartMs=0,runEndMs=245000),
    lambda r:r.update(reviewerVerifiedContinuousRun=True),
    lambda r:r.update(runKind="successful_backflip"),
    lambda r:r.update(featureMarkers=[{"kind":"jump","sourceMs":300000}]),
    lambda r:r.update(featureMarkers=[{"kind":"magic_trick","sourceMs":35000}]),
    lambda r:r.update(sourceAssetSha256="f"*64),
])
def test_bad_run_annotation_is_not_trusted(change):
    ctx=run.plan_context(anchor(),230000)
    doc=run.owner_annotation_template(ctx)
    change(doc)
    with pytest.raises(ValueError):
        run.validate_owner_run(doc,ctx)


def test_review_html_avoids_model_claims_and_external_network():
    ctx=run.plan_context(anchor(),230000)
    html=page(ctx)
    assert "connect-src 'none'" in html
    assert 'media/run-context.mp4' in html
    assert "Mark run end at playhead" in html
    assert "Add at playhead" in html
    assert "native" not in html.lower() or "approximate" in html.lower()
    assert "216.000" not in html # derived at runtime
    assert '"contextA":0' in html
    assert "successfully landed" not in html.lower()


@pytest.mark.skipif(not shutil.which("node"),reason="Node.js unavailable")
def test_local_run_review_script_has_valid_javascript(tmp_path):
    import re
    html=page(run.plan_context(anchor(),230000))
    snippet=re.search(r"<script>(.*?)</script>",html,re.S)
    assert snippet
    code=tmp_path/"run.js";code.write_text(snippet.group(1))
    res=subprocess.run(["node","--check",str(code)],
                       text=True,capture_output=True,timeout=12)
    assert res.returncode==0,res.stderr


def fixture(tmp_path, *, real_source=False):
    ws=tmp_path/"workspace"
    pilot=ws/"reports"/"verified-pilot"
    audit_dir=ws/"reports"/"campaign"/"audit"
    scan=ws/"reports"/"campaign"/"coarse-scan"
    (pilot/"media").mkdir(parents=True)
    audit_dir.mkdir(parents=True)
    (scan/"per-source").mkdir(parents=True)
    video=pilot/"media"/(RECORD+".mp4")
    video.write_bytes(b"synthetic-proxy-frames"*30)
    source_root=tmp_path/"fake_ssd"/"DCIM"
    source_root.mkdir(parents=True)
    original=source_root/"synthetic-camera.mp4"
    if real_source:
        ffmpeg=shutil.which("ffmpeg")
        if not ffmpeg:
            pytest.skip("FFmpeg unavailable")
        proc=subprocess.run([
            ffmpeg,"-hide_banner","-loglevel","error","-nostdin",
            "-f","lavfi","-i","color=c=blue:s=320x180:r=30:d=12",
            "-an","-c:v","libx264","-pix_fmt","yuv420p",str(original),
        ],text=True,capture_output=True,timeout=30)
        assert proc.returncode==0,proc.stderr
    else:
        original.write_bytes(b"one-source-only-fake-media"*90)
    original_sha=sha256(original.read_bytes()).hexdigest()
    moments=[{
        "momentId": RECORD,"sourceAssetSha256": original_sha,
        "sourceProxySha256":sha256(video.read_bytes()).hexdigest(),
        "sourceStartMs":2000,"sourceEndMs":4000,
        "sportHint":"unknown","selectionRole":"uniform_timeline_control",
    }]
    (pilot/"moment-plan.json").write_text(json.dumps({
        "version":MOMENT_VERSION,"manifestSha256":"a"*64,"moments":moments,
    }))
    asset={
        "sourceAssetSha256":original_sha,
        "storedPathAsCatalogued":str(original),
        "sourceBytesFromCatalog":original.stat().st_size,
        "durationMsFromCatalog":12000,
        "sourceLocationClass":"mounted_external_candidate",
    }
    audit={"version":campaign.AUDIT_VERSION,"workspace":str(ws),
           "assets":[asset]}
    audit_path=audit_dir/"campaign-audit.json"
    audit_path.write_text(json.dumps(audit))
    report={
        "version":campaign.SCAN_VERSION,
        "sourceAssetSha256":original_sha,"sourceDurationMs":12000,
        "verifiedOriginalSha256":original_sha,"shaVerifiedDuringThisScan":True,
    }
    report_path=scan/"per-source"/(original_sha+".json")
    report_path.write_text(json.dumps(report))
    state={
        "version":campaign.SCAN_VERSION,
        "campaignAuditSha256":sha256(audit_path.read_bytes()).hexdigest(),
        "assets":{original_sha:{
            "status":"coarse_completed",
            "reportSha256":sha256(report_path.read_bytes()).hexdigest(),
        }},
    }
    (scan/"scan-state.json").write_text(json.dumps(state))
    return ws,pilot,original,source_root,original_sha


def cli():
    script=Path(__file__).resolve().parents[1]/"scripts"/"klip_ski_run_context.py"
    spec=importlib.util.spec_from_file_location("klip_run_context_test",script)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_metadata_inspection_does_not_open_original(tmp_path,monkeypatch):
    ws,pilot,original,source_root,source_sha=fixture(tmp_path)
    runner=cli()
    # Fail if SHA verification or video probing mistakenly happens in dry run.
    monkeypatch.setattr(runner,"_hash_verified",
                        lambda *args,**kwargs:(_ for _ in ()).throw(
                            AssertionError("Original opened during inspect")))
    args=argparse.Namespace(
        workspace=ws,pilot=pilot,source_prefix=source_sha[:12],
        anchor_start_ms=2000,anchor_end_ms=4000,audit=None,
        before_s=3,after_s=8,max_s=30,
        output=None,render_context=False,trusted_source_root=None,
        allow_mounted_ssd=False,
    )
    result=runner.execute(args)
    assert result["status"]=="source_safe_run_context_plan_only"
    assert result["freshOriginalReads"]==0
    assert result["expandedEndSeconds"]==12
    assert original.is_file()


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="FFmpeg/ffprobe unavailable")
def test_renders_only_approved_source_and_keeps_original_immutable(tmp_path):
    ws,pilot,original,source_root,source_sha=fixture(tmp_path,real_source=True)
    old_sha=sha256(original.read_bytes()).hexdigest()
    output=ws/"reports"/"run-context-study"
    args=argparse.Namespace(
        workspace=ws,pilot=pilot,source_prefix=source_sha[:12],
        anchor_start_ms=2000,anchor_end_ms=4000,audit=None,
        before_s=3,after_s=8,max_s=30,
        output=output,render_context=True,trusted_source_root=source_root,
        allow_mounted_ssd=True,
    )
    result=cli().execute(args)
    assert result["originalSourceSHAReverified"] is True
    assert result["ownerRunBoundariesDetected"] is False
    assert (output/"media"/"run-context.mp4").is_file()
    assert (output/"run-review.html").is_file()
    assert (output/"owner-annotation-template.json").is_file()
    assert sha256(original.read_bytes()).hexdigest()==old_sha
    with pytest.raises(ValueError,match="already exists"):
        cli().execute(args)

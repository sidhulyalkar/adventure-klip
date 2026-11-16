"""Synthetic tests for event-cadence scaling and non-destructive ski edit previews."""
from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from adventure_intelligence import ski_event_scaling as scaling
from adventure_intelligence import ski_run_context as context
from adventure_intelligence.auto_scout_v2 import save_json


def _fixtures():
    anchor={
        "recordId":"c"*24,"sourceSha256":"a"*64,"proxySha256":"b"*64,
        "sourceStartMs":16000,"sourceEndMs":36000,
    }
    plan=context.plan_context(anchor,48128,before_s=40,after_s=180,max_s=240)
    owner=context.owner_annotation_template(plan)
    owner.update(
        runStartMs=5558,runEndMs=39270,
        runKind="terrain_park",preference="keep",
        reviewerVerifiedContinuousRun=True,
        featureMarkers=[
            {"kind":"jump","sourceMs":11846},
            {"kind":"jump","sourceMs":17960},
            {"kind":"jump","sourceMs":23714},
            {"kind":"jump","sourceMs":37639},
        ],
    )
    return owner,plan


def test_four_jumps_become_three_feature_line_and_standalone():
    owner,context_plan=_fixtures()
    plan=scaling.plan_event_lines(owner,context_plan)
    assert plan["consecutiveFeatureGapsMs"]==[6114,5754,13925]
    assert plan["shortGapCadenceBaselineMs"]==5934
    balanced=plan["alternatives"]["balanced"]
    assert balanced["cadenceJoinThresholdMs"]==10681
    assert balanced["groupCount"]==2
    assert [[x["sourceMs"] for x in g["features"]] for g in balanced["groups"]]==[
        [11846,17960,23714],[37639],
    ]
    assert balanced["gapDecisions"][-1]["reason"]=="gap_exceeds_local_cadence"
    assert balanced["groups"][0]["role"]=="main_line_candidate"
    assert balanced["groups"][0]["sourceStartMs"]==5558
    assert balanced["groups"][0]["sourceEndMs"]==26714
    assert balanced["groups"][1]["role"]=="single_feature_candidate"
    assert balanced["groups"][1]["sourceStartMs"]==34639
    assert balanced["groups"][1]["sourceEndMs"]==39270
    assert balanced["groups"][1]["postRollShortfallMs"]==1369
    assert plan["nativePtsAlignmentVerified"] is False
    assert plan["videoEventsDecodedForThisPlan"] is False
    assert plan["automaticFinalMasterAuthorization"] is False


def test_multiscale_inclusive_retains_option_to_include_fourth_jump():
    review,source=_fixtures()
    p=scaling.plan_event_lines(review,source)
    assert p["alternatives"]["tight"]["groupCount"]==2
    assert p["alternatives"]["immersive"]["groupCount"]==1
    assert p["alternatives"]["balanced"]["previewMontageOrder"]==[
        "balanced-01","balanced-02"
    ]


def test_owner_can_force_last_extra_and_override_importance():
    review,source=_fixtures()
    hints=scaling.hints_template()
    hints["forcedBreakAfterMs"]=[23714]
    hints["secondaryFeatureMs"]=[37639]
    plan=scaling.plan_event_lines(review,source,hints=hints)
    assert all(x["groupCount"]==2 for x in plan["alternatives"].values())
    assert plan["alternatives"]["balanced"]["groups"][1]["role"]=="secondary_feature_candidate"
    assert plan["alternatives"]["immersive"]["gapDecisions"][-1]["reason"]=="owner_forced_break"


def test_owner_forced_join_supported_but_break_marker_never_overridden():
    review,source=_fixtures()
    hints=scaling.hints_template()
    hints["forcedJoinAfterMs"]=[23714]
    out=scaling.plan_event_lines(review,source,hints=hints)
    assert out["alternatives"]["balanced"]["groupCount"]==1
    review["featureMarkers"].append({"kind":"stopped","sourceMs":30000})
    out=scaling.plan_event_lines(review,source,hints=hints)
    assert out["alternatives"]["balanced"]["groupCount"]==2
    assert out["alternatives"]["balanced"]["gapDecisions"][-1]["reason"]=="verified_stop_or_lift_marker"


def test_manual_split_close_features_never_overlaps_edits():
    review,source=_fixtures()
    review["featureMarkers"]=[
        {"kind":"jump","sourceMs":11846},
        {"kind":"jump","sourceMs":12700},
    ]
    review["runEndMs"]=23000
    hints=scaling.hints_template()
    hints["forcedBreakAfterMs"]=[11846]
    out=scaling.plan_event_lines(review,source,hints=hints)
    g=out["alternatives"]["balanced"]["groups"]
    assert g[0]["sourceEndMs"] <= g[1]["sourceStartMs"]
    assert g[0]["sourceStartMs"] < g[0]["sourceEndMs"]
    assert g[1]["sourceStartMs"] < g[1]["sourceEndMs"]


def test_unverified_owner_run_and_unrecognized_feature_hints_fail_closed():
    review,source=_fixtures()
    review["reviewerVerifiedContinuousRun"]=False
    with pytest.raises(ValueError,match="reviewed boundaries"):
        scaling.plan_event_lines(review,source)
    review["reviewerVerifiedContinuousRun"]=True
    hints=scaling.hints_template()
    hints["forcedBreakAfterMs"]=[12345]
    with pytest.raises(ValueError,match="distinct known feature"):
        scaling.plan_event_lines(review,source,hints=hints)


def test_one_feature_yields_single_highlight_not_fictional_line():
    review,source=_fixtures()
    review["featureMarkers"]=[{"kind":"jump","sourceMs":11846}]
    out=scaling.plan_event_lines(review,source)
    assert out["shortGapCadenceBaselineMs"] is None
    assert out["alternatives"]["balanced"]["groups"][0]["role"]=="single_feature_candidate"
    assert out["alternatives"]["balanced"]["groupCount"]==1


def _cli():
    file=Path(__file__).resolve().parents[1]/"scripts"/"klip_plan_ski_event_edits.py"
    spec=importlib.util.spec_from_file_location("klip_run_edits_test",file)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workspace(tmp_path, *, real_video):
    ws=tmp_path/"ws"
    context_dir=ws/"reports"/"run"
    media=context_dir/"media";media.mkdir(parents=True)
    owner,ctx=_fixtures()
    if not real_video:
        video=media/"run-context.mp4"
        video.write_bytes(b"fake-test-proxy"*100)
    else:
        ffmpeg=shutil.which("ffmpeg")
        if not ffmpeg:
            pytest.skip("ffmpeg unavailable")
        # Use a short synthetic clip (16s) to keep CI fast.
        ctx["contextSourceStartMs"]=0
        ctx["contextSourceEndMs"]=16000
        owner["contextSourceStartMs"]=0
        owner["contextSourceEndMs"]=16000
        owner["runStartMs"]=500
        owner["runEndMs"]=15000
        owner["featureMarkers"]=[
            {"kind":"jump","sourceMs":1000},
            {"kind":"jump","sourceMs":2200},
            {"kind":"jump","sourceMs":3900},
            {"kind":"jump","sourceMs":13500},
        ]
        video=media/"run-context.mp4"
        result=subprocess.run([
            ffmpeg,"-hide_banner","-loglevel","error","-nostdin",
            "-f","lavfi","-i","color=c=cyan:s=320x180:r=24:d=16",
            "-f","lavfi","-i","sine=frequency=440:duration=16",
            "-shortest","-c:v","libx264","-pix_fmt","yuv420p",
            "-c:a","aac","-b:a","64k",str(video),
        ],capture_output=True,timeout=50)
        assert result.returncode==0,result.stderr.decode(errors="replace")
    ctx["previewRender"]={
        "sourceMediaChanged":False,
        "videoSha256":sha256(video.read_bytes()).hexdigest(),
    }
    save_json(context_dir/"run-context-plan.json",ctx)
    owner_file=ws/"reports"/"owner.json"
    save_json(owner_file,owner)
    args=argparse.Namespace(
        workspace=ws,context_dir=context_dir,owner_json=owner_file,
        output=ws/"reports"/"clips",force_break_after_ms=[],
        force_join_after_ms=[],secondary_feature_ms=[],
        pre_roll_ms=500 if real_video else 3000,
        post_roll_ms=500 if real_video else 3000,
        render_preview=False,
    )
    return args,video


def test_cli_dry_run_has_no_side_effects(tmp_path):
    args,video=_workspace(tmp_path,real_video=False)
    original=sha256(video.read_bytes()).hexdigest()
    doc=_cli().execute(args)
    assert doc["status"]=="validated_multiscale_dry_run"
    assert len(doc["balancedGroups"])==2
    assert doc["filesCreated"]==0
    assert doc["sourceOriginalRead"] is False
    assert not args.output.exists()
    assert sha256(video.read_bytes()).hexdigest()==original


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="FFmpeg unavailable")
def test_real_synthetic_proxy_renders_two_edits_and_montage(tmp_path):
    args,video=_workspace(tmp_path,real_video=True)
    args.render_preview=True
    old=sha256(video.read_bytes()).hexdigest()
    result=_cli().execute(args)
    assert result["status"]=="non_destructive_multi_scale_preview_ready"
    assert result["balancedGroups"]==2
    assert result["montageCreated"] is True
    assert result["gapReviewClips"]==1
    assert result["tailReviewClips"]==0
    for name in ("balanced-01.mp4","balanced-02.mp4","balanced-montage.mp4","balanced-gap-review-01.mp4"):
        assert (args.output/"media"/name).stat().st_size>100
    plan=json.loads((args.output/"temporal-edit-plan.json").read_text())
    assert len(plan["renderedSegments"])==2
    assert plan["originalDJIVideoReads"]==0
    assert "context" in plan["ownerReviewMediaSource"]
    assert (args.output/"review.html").is_file()
    assert sha256(video.read_bytes()).hexdigest()==old
    with pytest.raises(ValueError,match="new private output"):
        _cli().execute(args)

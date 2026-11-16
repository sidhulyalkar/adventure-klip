"""Synthetic contracts for replayable Top Clips extraction and safe ranking."""
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
from adventure_intelligence import complete_highlight_rerank as ranker
from adventure_intelligence import ski_run_context as run
from adventure_intelligence.moment_edit_v2 import VERSION as MOMENT_VERSION


def p(activity,tags,*,quality=()):
    return scout.validate_proposal({
        "activity":activity,
        "activityFrames":[0] if activity not in ("unknown","other") else [],
        "categories":[{
            "tag":tag,"frames":[0,2],"observation":"Visible tentative object."
        } for tag in tags],
        "qualityFlags":[{
            "kind":item,"frames":[3],"observation":"Tentative image quality concern."
        } for item in quality],
        "eventContinuity":"unresolved",
        "reviewPriority":"investigate",
        "summary":"Model saw a possible activity and scene.",
        "uncertainty":"Specific events are not verified.",
    })


def row(i,sport,tags,quality=()):
    rid=f"{i:024x}"
    source=f"{i:064x}"
    return (
        {"recordId":rid,"sourceSha256":source,"proxySha256":f"{i+20:064x}",
         "sourceStartMs":2000,"sourceEndMs":8000,
         "selectionRole":"uniform_timeline_control"},
        {"status":"done","analysis":{"proposal":p(sport,tags,quality=quality)}},
    )


def test_failure_controls_are_not_called_bad_clips():
    a,r1=row(1,"ski",["ski_park"])
    b,r2=row(2,"snorkeling",["marine_ray"])
    r2={"status":"failed","failureStage":"model_schema"}
    items=ranker.candidates([a,b],{a["recordId"]:r1,b["recordId"]:r2})
    assert len(items)==1
    assert items[0]["sourceSha256"]==a["sourceSha256"]


def test_unreliable_blur_is_never_an_automatic_score_penalty():
    a,r1=row(1,"ski",["ski_jump"],quality=[])
    b,r2=row(2,"ski",["ski_jump"],quality=["blur","shake"])
    items=ranker.candidates([a,b],{a["recordId"]:r1,b["recordId"]:r2})
    assert items[0]["heuristic"]["reviewOpportunityScore"]==items[1]["heuristic"]["reviewOpportunityScore"]
    assert all(not x["heuristic"]["qualityFlagsUsedToPenalize"] for x in items)


def test_rare_specific_tags_rank_higher_than_ubiquitous_park():
    rows=[];recs={}
    for i in range(1,11):
        a,r=row(i,"ski",["ski_park"] if i<=9 else ["ski_jump"])
        rows.append(a);recs[a["recordId"]]=r
    scored=ranker.candidates(rows,recs)
    ordered=sorted(scored,key=lambda x:-x["heuristic"]["reviewOpportunityScore"])
    assert ordered[0]["categoryProposals"]==["ski_jump"]
    assert all(x["ownerReviewed"] is False for x in scored)


def test_balanced_source_diverse_queue_respects_owner_ski_run():
    a,r1=row(1,"ski",["ski_jump"])
    b,r2=row(2,"snorkeling",["marine_ray"])
    c,r3=row(3,"bike",["riding_general"])
    cands=ranker.candidates(
        [a,b,c],{a["recordId"]:r1,b["recordId"]:r2,c["recordId"]:r3},
    )
    owner=[{
        "id":"owner-test","origin":"human_reviewed_run_tentative_edit",
        "ownerReviewed":True,"humanKeepConfirmed":True,"activity":"ski",
        "sourceSha256":a["sourceSha256"],"sourceStartMs":500,"sourceEndMs":6500,
        "role":"main_line_candidate","featureTimesMs":[1000,2000,3000],
    }]
    out=ranker.rank_review_queue(cands+owner,top=5)
    assert out["top"][0]["id"]=="owner-test"
    assert out["top"][0]["rankClass"]=="reviewed_owner_event"
    assert not any(x["id"]==a["recordId"] for x in out["top"])
    assert {x["activity"] for x in out["top"]}=={"ski","snorkeling","bike"}


def test_gallery_only_references_explicit_staged_media():
    raw={
        "topRankedClips":[{
            "rank":1,"ownerReviewed":False,
            "candidateTitle":"Possible ski jump",
            "mediaFile":"01-test.mp4","sourceStartMs":0,
            "sourceEndMs":12000,"sourceSha256":"a"*64,
            "categoryProposals":["ski_jump"],
            "rankExplanation":"Model hypothesis, not confirmed.",
        }]
    }
    html=ranker.gallery_html(raw)
    assert "media/01-test.mp4" in html
    assert "connect-src 'none'" in html
    assert "full-library quality ranking" in html


def _cli():
    path=Path(__file__).resolve().parents[1]/"scripts"/"klip_rank_complete_clips_v1.py"
    spec=importlib.util.spec_from_file_location("klip_top_extract",path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture(tmp_path, video=False):
    ws=tmp_path/"workspace"
    pilot=ws/"reports"/"pilot"
    exp=ws/"reports"/"scout"
    context=ws/"reports"/"context"
    for pth in (pilot/"media",exp/"receipts",context/"media"):
        pth.mkdir(parents=True)
    source="a"*64
    rows=[]
    model="gemma3:4b"
    for i in range(1,6):
        rid=f"{i:024x}"
        clip=pilot/"media"/(rid+".mp4")
        clip.write_bytes((b"cached-small-proxy-"+bytes([i]))*50)
        sport,tags=(
            ("ski",["ski_park"]) if i==1 else
            ("ski",["ski_jump"]) if i==2 else
            ("snorkeling",["marine_ray"]) if i==3 else
            ("bike",["riding_general"])
        )
        rows.append({
            "momentId":rid,
            "sourceAssetSha256":source if i==1 else f"{i:064x}",
            "sourceProxySha256":sha256(clip.read_bytes()).hexdigest(),
            "sourceStartMs":2000,"sourceEndMs":8000,
            "sportHint":"unknown","selectionRole":"uniform_timeline_control",
        })
    (pilot/"moment-plan.json").write_text(json.dumps({
        "version":MOMENT_VERSION,"manifestSha256":"b"*64,
        "moments":rows,
    }))
    _,plan_sha=scout.load_pilot(ws,pilot)
    scout.save_json(exp/"experiment-state.json",{
        "version":scout.VERSION,"pilot":str(pilot),
        "inputPlanSha256":plan_sha,"model":model,
        "policyFingerprint":scout.fingerprint(model),
    })
    for i,item in enumerate(rows,1):
        rec={
            "version":scout.VERSION,
            "recordId":item["momentId"],
            "sourceSha256":item["sourceAssetSha256"],
            "proxySha256":item["sourceProxySha256"],
            "inputPlanSha256":plan_sha,
            "policyFingerprint":scout.fingerprint(model),
        }
        if i==5:
            rec.update(status="failed",failureStage="model_schema")
        else:
            sport,tags=(
                ("ski",["ski_park"]) if i==1 else
                ("ski",["ski_jump"]) if i==2 else
                ("snorkeling",["marine_ray"]) if i==3 else
                ("bike",["riding_general"])
            )
            rec.update(status="done",analysis={"proposal":p(sport,tags)})
        scout.save_json(exp/"receipts"/(item["momentId"]+".json"),rec)
    context_plan=run.plan_context({
        "recordId":rows[0]["momentId"],
        "sourceSha256":source,"proxySha256":rows[0]["sourceProxySha256"],
        "sourceStartMs":2000,"sourceEndMs":8000,
    },12000,before_s=2,after_s=4,max_s=30)
    preview=context/"media"/"run-context.mp4"
    if video:
        ffmpeg=shutil.which("ffmpeg")
        if not ffmpeg:
            pytest.skip("FFmpeg unavailable")
        result=subprocess.run([
            ffmpeg,"-nostdin","-hide_banner","-loglevel","error",
            "-f","lavfi","-i","color=c=blue:s=320x180:r=24:d=12",
            "-an","-c:v","libx264","-pix_fmt","yuv420p",str(preview),
        ],timeout=50,capture_output=True)
        assert result.returncode==0,result.stderr.decode(errors="replace")
    else:
        preview.write_bytes(b"fake-offline-run-context-bytes"*100)
    context_plan["previewRender"]={
        "sourceMediaChanged":False,
        "videoSha256":sha256(preview.read_bytes()).hexdigest(),
    }
    scout.save_json(context/"run-context-plan.json",context_plan)
    owner=run.owner_annotation_template(context_plan)
    owner.update(
        runStartMs=500,runEndMs=11000,
        runKind="terrain_park",preference="keep",
        reviewerVerifiedContinuousRun=True,
        featureMarkers=[
            {"kind":"jump","sourceMs":1500},
            {"kind":"jump","sourceMs":3000},
            {"kind":"jump","sourceMs":4500},
            {"kind":"jump","sourceMs":9500},
        ],
    )
    owner_path=ws/"reports"/"owner.json"
    scout.save_json(owner_path,owner)
    args=argparse.Namespace(
        workspace=ws,pilot=pilot,autoscout=exp,
        output=ws/"reports"/"top",context_dir=context,
        owner_run=owner_path,force_break_after_ms=[4500],
        secondary_feature_ms=[9500],top=4,per_sport=None,
        render=False,
    )
    return args,preview


def test_cli_read_only_rerank_does_not_create_media(tmp_path):
    args,preview=_fixture(tmp_path,video=False)
    before=sha256(preview.read_bytes()).hexdigest()
    out=_cli().execute(args)
    assert out["status"]=="validated_dry_run_no_files_created"
    assert out["ownerGroups"]==2
    assert out["modelFailed"]==1
    assert out["mediaOpened"] is False
    assert out["outputsCreated"]==0
    assert not args.output.exists()
    assert sha256(preview.read_bytes()).hexdigest()==before


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="FFmpeg and ffprobe unavailable")
def test_cli_outputs_real_top_clips_and_checked_checksums(tmp_path):
    args,preview=_fixture(tmp_path,video=True)
    args.render=True
    old=sha256(preview.read_bytes()).hexdigest()
    report=_cli().execute(args)
    assert report["status"]=="playable_top_review_ready"
    assert report["ownerBackedClips"]==2
    assert report["modelFailed"]==1
    assert report["originalSourceReads"]==0
    folder=args.output
    assert (folder/"review.html").is_file()
    assert (folder/"top-ranked.csv").is_file()
    ranked=json.loads((folder/"ranked-clips.json").read_text())
    assert ranked["fullLibraryRankingCompleted"] is False
    assert ranked["modelFailed"]==1
    assert len(ranked["topRankedClips"])==4
    assert all(x["renderReceipt"]["originalCameraVideoRead"] is False for x in ranked["topRankedClips"])
    for clip in ranked["topRankedClips"]:
        path=folder/"media"/clip["mediaFile"]
        assert path.is_file()
        assert sha256(path.read_bytes()).hexdigest()==clip["renderReceipt"]["mediaSha256"]
    assert sha256(preview.read_bytes()).hexdigest()==old
    with pytest.raises(ValueError,match="new private output"):
        _cli().execute(args)

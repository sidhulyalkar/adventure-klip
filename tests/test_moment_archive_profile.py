"""Synthetic-only K-MOMENT archive characterization and pilot selection tests."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.moment_archive_profile import (
    VERSION, _fps, audit_archive,
)
from adventure_intelligence.workspace import init


def fixture(tmp_path):
    ws=tmp_path/"workspace"
    init(ws)
    (ws/"reports").mkdir()
    sources=[]
    for i in range(12):
        aid=f"{i+1:064x}"
        sources.append(aid)
        stamp=(f"202{4+i//4}-0{1+i%4}-12T12:00:00Z"
               if i != 11 else "2026-04-01T11:00:00")
        activity=("ski" if i%4==0 else "bike" if i%4==1
                  else "snorkeling" if i%4==2 else "unknown")
        dur=(120.0*(i+1) if i != 10 else None)
        probe={
            "width":3840, "height":2160, "codec":"hevc",
            "avg_frame_rate":"30000/1001", "audio":i%2==0,
            # Freeform probe data must not leak in profiling output.
            "private_gps":"secret-evidence-never-export",
            "probe_error":"/Volumes/hidden/user/source-path.mp4",
        }
        if i==9:
            probe["avg_frame_rate"]="0/0"
            probe["width"]=0
        with connect(ws) as db:
            db.execute(
                "INSERT INTO assets"
                "(id,sha256,stored_path,size_bytes,suffix,duration,"
                "capture_time,activity,metadata_json)"
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (aid,aid,f"/Volumes/UNMOUNTED/{i}.MP4",
                 (i+1)*10_000_000,".mp4",dur,stamp,activity,
                 json.dumps(probe)),
            )
    with connect(ws) as db:
        db.execute(
            "INSERT INTO example_revisions "
            "(revision_id,example_id,asset_id,start_ms,end_ms,label,"
            "decision,highlight,reviewer_note,reviewer)"
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("example1-r1","example1",sources[0],0,1000,"synthetic",
             "confirmed","keep","","local_user"))
        db.execute(
            "INSERT INTO example_revisions "
            "(revision_id,example_id,previous_revision_id,asset_id,"
            "start_ms,end_ms,label,decision,highlight,reviewer_note,reviewer)"
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("example1-r2","example1","example1-r1",sources[0],
             0,1000,"synthetic","rejected","skip","","local_user"))
        db.execute(
            "INSERT INTO pairwise_revisions "
            "(revision_id,comparison_id,left_asset_id,right_asset_id,"
            "left_start_ms,left_end_ms,right_start_ms,right_end_ms,"
            "preference,reason,reviewer_note)"
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("pair1","compare1",sources[1],sources[2],
             0,1000,0,1000,"left","synthetic",""))
        db.execute(
            "INSERT INTO semantic_jobs"
            "(asset_id,config_id,config_json,status,filename,sha256)"
            "VALUES(?,?,?,?,?,?)",
            (sources[0],"a"*64,'{"provider":"histogram"}',
             "completed","synthetic.json","b"*64))
        db.execute(
            "INSERT INTO semantic_active(asset_id,provider,config_id)"
            "VALUES(?,?,?)",
            (sources[0],"histogram","a"*64))
    return ws,sources


def test_metadata_audit_actual_scale_no_source_video_access(tmp_path):
    ws,ids=fixture(tmp_path)
    before=(ws/"catalog.sqlite3").stat().st_mtime_ns
    report=audit_archive(ws,pilot_sources=8,seed=7)
    assert report["version"] == VERSION
    assert report["archiveSourceCount"]==12
    assert report["duration"]["sourcesWithValidDuration"]==11
    assert report["duration"]["unknownDurationSources"]==1
    assert report["duration"]["totalHoursKnown"] > 0
    assert report["sourceStorage"]["totalDecimalGBKnown"] > 0
    assert report["captureMetadata"]["sourcesWithTimezoneAwareDates"]==11
    assert report["captureMetadata"]["distinctUtcCaptureDaysNotVerifiedOutings"]==11
    assert report["captureMetadata"]["sourcesByCaptureYearUtc"]=={
        "2024":4,"2025":4,"2026":3}
    assert report["metadataGaps"]["frameRateUnavailable"]==1
    assert report["metadataGaps"]["missingOrInvalidResolution"]==1
    assert report["catalogActivityUnverifiedCounts"]=={
        "bike":3,"ski":3,"snorkeling":3,"unknown":3}
    assert report["existingEvidenceCoverage"]["sourcesWithAnyPriorOwnerReview"]==3
    assert report["existingEvidenceCoverage"]["latestExampleJudgments"]==1
    assert report["existingEvidenceCoverage"]["latestPairwiseJudgments"]==1
    assert report["existingEvidenceCoverage"][
        "activeSemanticProviderSourceCountsNotReceiptVerified"]=={"histogram":1}
    assert report["existingEvidenceCoverage"]["independentlyVerifiedOutings"] is None
    assert report["pilotPlan"]["selectedSources"]==8
    assert set(report["pilotPlan"]["pilotCandidateCountsBySport"])=={
        "ski","bike","snorkeling","unknown"}
    assert all(not r["verifiedIndependentOuting"]
               for r in report["pilotPlan"]["candidateSources"])
    assert report["illustrativeComputeBudgets"][
        "frameReadsIfAllValidSourcesUniformlySampled"] > 0
    assert report["illustrativeComputeBudgets"][
        "maximumPlannedDensePilotFrames"] <= 8 * 30 * 8
    assert report["sourceMediaRead"] is False
    assert report["videoDecoded"] is False
    assert report["catalogModified"] is False
    assert not report["editorialScoresComputed"]
    result=json.dumps(report)
    assert "secret-evidence-never-export" not in result
    assert "/Volumes/" not in result
    assert (ws/"catalog.sqlite3").stat().st_mtime_ns == before
    assert set(r["sourceSha256"] for r in report["sources"])==set(ids)


def test_same_seed_is_stable_no_trip_inference(tmp_path):
    ws,ids=fixture(tmp_path)
    a=audit_archive(ws,pilot_sources=6,seed=15)
    b=audit_archive(ws,pilot_sources=6,seed=15)
    assert a==b
    assert not a["pilotPlan"]["independentTripSplitEligible"]
    assert all(x["captureStyle"]=="unknown_not_observed" for x in a["sources"])
    assert a["existingEvidenceCoverage"]["priorJudgeSuccessfulSourcesPresent"]==0


def test_private_judge_hypotheses_are_never_verified_labels(tmp_path):
    ws,ids=fixture(tmp_path)
    root=ws/"reports"/"ktide-judge-original"
    root.mkdir()
    rows=[
        {"sourceSha256":ids[3], "judgeStatus":"model_hypothesis",
         "sourceShaVerifiedForJudge":True,
         "observedFrames":[
             {"frameIndex":i,"requestedMs":(i+1)*1000,
              "imageSha256":f"{i+1:064x}"}
             for i in range(4)],
         "judgeProposal":{"subsport":"skiing_moguls",
                          "sceneSummary":"NEVER PRINT OR LEAK SECRET"}},
        {"sourceSha256":ids[7], "judgeStatus":"failed",
         "judgeProposal":None},
    ]
    p=root/"proposals.json"
    p.write_text(json.dumps({
        "version":"klip-ktide-local-vision-judge-v1",
        "sourceCount":len(rows),
        "successes":1,
        "proposals":rows,
    }))
    result=audit_archive(ws,pilot_sources=8,judge_report=p)
    lookup={x["sourceSha256"]:x for x in result["sources"]}
    assert lookup[ids[3]]["candidateSport"]=="ski"
    assert lookup[ids[3]]["sportEvidence"]=="prior_local_model_hypothesis_not_verified"
    assert lookup[ids[7]]["candidateSport"]=="unknown"
    assert result["existingEvidenceCoverage"]["priorJudgeSuccessfulSourcesPresent"]==1
    assert "NEVER PRINT" not in json.dumps(result)
    assert all(not x["ownerConfirmedActivity"] for x in result["pilotPlan"]["candidateSources"])


def test_invalid_input_and_tampered_judge_do_not_silently_produce_plan(tmp_path):
    ws,ids=fixture(tmp_path)
    with pytest.raises(ValueError,match="exceeds audit limit"):
        audit_archive(ws,limit=8)
    for kwargs in ({"coarse_seconds":float("nan")},{"dense_fps":0.},
                   {"pilot_sources":-1},{"seed":-2}):
        with pytest.raises(ValueError,match="Invalid"):
            audit_archive(ws,**kwargs)
    root=ws/"reports"/"ktide-judge-original"
    root.mkdir()
    p=root/"proposals.json"
    p.write_text(json.dumps({"version":"bad","proposals":[],"sourceCount":0}))
    with pytest.raises(ValueError,match="Unsupported judge"):
        audit_archive(ws,judge_report=p)
    p.write_text(json.dumps({"version":"klip-ktide-local-vision-judge-v1",
                             "proposals":[
                                {"sourceSha256":ids[0],
                                 "judgeStatus":"model_hypothesis",
                                 "sourceShaVerifiedForJudge":False,
                                 "judgeProposal":{"subsport":"skiing_speed"}}],
                             "sourceCount":1,"successes":1}))
    with pytest.raises(ValueError,match="Unreceipted"):
        audit_archive(ws,judge_report=p)
    doc=json.loads(p.read_text())
    doc["proposals"][0]["sourceShaVerifiedForJudge"]=True
    doc["proposals"][0]["observedFrames"]=[
        {"frameIndex":0,"requestedMs":1000,"imageSha256":"bad"},
        {"frameIndex":1,"requestedMs":2000,"imageSha256":"a"*64},
    ]
    p.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match="frame evidence"):
        audit_archive(ws,judge_report=p)


def test_frame_rate_validation_and_cli_no_workspace_writes(tmp_path):
    assert _fps("30000/1001")==pytest.approx(29.97)
    assert _fps("0/0") is None
    assert _fps("25000/0") is None
    assert _fps("99999999/1") is None
    assert _fps("24.000")==24.0
    ws,_=fixture(tmp_path)
    project=Path(__file__).resolve().parents[1]
    cmd=[sys.executable,str(project/"scripts/klip_moment_archive_profile.py"),
         "--workspace",str(ws),"--pilot-sources","7"]
    result=subprocess.run(cmd,capture_output=True,text=True,cwd=project)
    assert result.returncode == 0, result.stderr
    report=json.loads(result.stdout)
    assert report["pilotPlan"]["selectedSources"]==7
    assert report["sourceMediaRead"] is False
    assert not list((ws/"reports").iterdir())

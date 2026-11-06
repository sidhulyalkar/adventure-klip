"""Synthetic tests: human Keep/Skip is about cuts, not source deletion."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json

import pytest

from adventure_intelligence.cli import main
from adventure_intelligence.catalog import connect
from adventure_intelligence.highlight_curation_pilot import save_curation_pilot
from adventure_intelligence.highlight_feedback_discovery import (
    plan_review_discovery, save_review_discovery, _signals, _clip_context,
)
from adventure_intelligence.highlight_curation_review import REVIEW_VERSION
from adventure_intelligence.social_action_lens import USER_VERSION, FEATURE_VERSION
from adventure_intelligence.studio_preview_judge import preview_paths


@pytest.fixture
def frozen(tmp_path):
    root=tmp_path/"ws"
    ids=[sha256(f"synthetic-recording-{i}".encode()).hexdigest()
         for i in range(9)]
    with connect(root) as db:
        for i,aid in enumerate(ids):
            db.execute(
                "INSERT INTO assets(id,sha256,stored_path,size_bytes,suffix,duration,activity) "
                "VALUES(?,?,?,?,?,?,?)",
                (aid,aid,f"/Volumes/UNMOUNTED_DJI/DCIM/{i:04}.MP4",
                 8_000_000_000,".MP4",80.0,
                 ("ski","snorkeling","unknown")[i%3]))
        db.commit()
    study=root/"reports"/"baseline"/"study.json"
    features=root/"reports"/"features"/"feature-template.json"
    study.parent.mkdir(parents=True)
    features.parent.mkdir(parents=True)
    ranked=[]
    selected=[]
    for i,aid in enumerate(ids[:7]):
        sport=("ski","snorkeling","unresolved")[i%3]
        ranked.append({
            "sourceAssetId":aid,"startMs":1000,"endMs":21000,
            "activityGroup":sport,"score":round(.9-.1*i,2)
        })
        if i<4:
            selected.append({
                "assetId":aid,"startMs":1000,"endMs":21000,
                "sport":"other" if sport=="unresolved" else sport,
            })
    study.write_text(json.dumps({"version":"archive-value-study-v1",
                                 "rankedCandidates":ranked}))
    features.write_text(json.dumps({"version":USER_VERSION,
                                   "featureVersion":FEATURE_VERSION,
                                   "items":selected}))
    for aid in ids[:7]:
        video,receipt=preview_paths(root,aid,1000,21000)
        video.parent.mkdir(parents=True,exist_ok=True)
        blob=(b"synthetic-derivative-only-"+aid.encode())*8
        video.write_bytes(blob)
        receipt.write_text(json.dumps({
            "version":"studio-preview-local-judge-v1",
            "assetId":aid,"startMs":1000,"endMs":21000,
            "originalSha256Verified":True,
            "previewSha256":sha256(blob).hexdigest()
        }))
    packet=root/"reports"/"pilot"
    result=save_curation_pilot(root,study,features,packet,limit=12)
    manifest=json.loads(Path(result["manifest"]).read_text())
    presentation=json.loads((packet/"presentation.json").read_text())
    ready=[r for r in manifest["records"]
           if presentation["records"][r["recordId"]]["verifiedProxyReady"]]
    assert len(ready)>=4
    return root,packet,result,ready


def feedback(rid,name,choice,note):
    return {
        "recordId":rid,"annotatorId":name,"role":"casual_spectator",
        "goal":"nature_encounter","choice":choice,
        "outcome":"unknown","outcomeVisible":"ambiguous",
        "technicalQuality":None,"aestheticQuality":None,
        "eventReadability":None,"rationale":note,
        "eventPeakMs":None,"resolutionMs":None
    }


def export(path,digest,rows):
    path.write_text(json.dumps({"version":REVIEW_VERSION,
                                "manifestSha256":digest,
                                "reviews":rows}))
    return path


def test_notes_cues_are_explicit_and_never_deletion_authorization():
    preserve=_signals("A deep-water encounter but blurry, worth saving the video")
    assert preserve["explicitPreserveLanguage"]
    assert preserve["visualLimitationMentioned"]
    assert not preserve["deleteLanguagePresent"]
    run=_signals("Start of a steeper blue run; rest of the complete run worth keeping")
    assert run["explicitRunContinuation"]
    assert run["explicitPreserveLanguage"]
    delete=_signals("DELETE this waiting ski line")
    assert delete["deleteLanguagePresent"]
    assert delete["allAreTextCuesNeedingHumanConfirmation"]


def test_builds_context_routing_from_exact_frozen_manifest(frozen,tmp_path):
    root,packet,result,ready=frozen
    keep,blurry_ray,run_start,queue=ready[:4]
    rows=[
        feedback(keep["recordId"],"reviewer-001","keep",
                 "Exceptional encounter worth highlighting"),
        feedback(blurry_ray["recordId"],"reviewer-001","skip",
                 "Eagle ray in deep water blurry but worth saving the video"),
        feedback(run_start["recordId"],"reviewer-001","skip",
                 "Start of a steeper blue run. The rest of the complete run is worth keeping"),
        feedback(queue["recordId"],"reviewer-001","skip",
                 "DELETE: waiting in a boring ski line"),
    ]
    path=export(tmp_path/"four.json",result["manifestSha256"],rows)
    plan=plan_review_discovery(root,packet,[path])
    assert plan["reviewedWindows"]==4
    assert plan["choices"]=={"keep":1,"skip":3}
    assert plan["provisionalHighlightCount"]==1
    assert plan["sourceVideosRead"]==0
    assert plan["newPreviewDecodes"]==0
    assert plan["filesDeleted"]==0
    by_id={d["recordId"]:d for d in plan["decisions"]}
    a,b,c,d=(by_id[row["recordId"]] for row in rows)
    assert a["route"]=="provisional_highlight"
    assert a["sourceAssetSha256"]==keep["source"]["assetSha256"]
    assert a["preserveSourceSuggestedNotExecuted"]
    assert a["contextSearchProposals"]
    assert b["route"]=="exclude_this_window"
    assert b["preserveSourceSuggestedNotExecuted"]
    assert "preserve_original_seek_better_window" in b["followUpActions"]
    assert "inspect_alternate_clearer_source_window" in b["followUpActions"]
    assert b["contextSearchProposals"]
    assert all(not x["sourceOriginalReadAuthorized"] for x in b["contextSearchProposals"])
    assert c["route"]=="exclude_this_window"
    assert "inspect_longer_same_source_sequence" in c["followUpActions"]
    assert len(c["contextSearchProposals"])>=2
    assert all(not x["sourceOriginalReadAuthorized"] for x in c["contextSearchProposals"])
    assert d["deleteRequestReviewOnlyNeverExecuted"]
    assert "manual_deletion_request_hold_no_file_operation" in d["followUpActions"]
    assert plan["preservationIndicators"]["manual_deletion_language_only"]==1
    output=save_review_discovery(root,packet,[path],root/"reports"/"plan")
    assert output["status"]=="review_discovery_plan_created"
    assert output["manualDeletionRequests"]==1
    shortlist=json.loads(Path(output["highlightsFile"]).read_text())
    assert len(shortlist["highlights"])==1
    assert shortlist["clipsRendered"]==0
    next_plan=json.loads(Path(output["sourceContextPlan"]).read_text())
    assert next_plan["sourceMediaReadAuthorized"] is False
    assert next_plan["proposals"]
    deletion=json.loads(Path(output["manualDeletionHolds"]).read_text())
    assert deletion["filesDeleted"]==0
    assert deletion["requiresExplicitFutureConfirmation"] is True
    assert len(deletion["requests"])==1
    report=json.loads(Path(output["report"]).read_text())
    assert report["decisions"]==plan["decisions"]
    html=Path(output["html"]).read_text()
    assert "Human highlight feedback" in html
    assert "DELETE" in html
    assert "/Volumes/UNMOUNTED_DJI" not in html
    with pytest.raises(ValueError,match="NEW"):
        save_review_discovery(root,packet,[path],root/"reports"/"plan")


def test_multi_rater_disagreement_preserves_distinct_goals(frozen,tmp_path):
    root,packet,result,ready=frozen
    rid=ready[0]["recordId"]
    a=export(tmp_path/"a.json",result["manifestSha256"],[
        feedback(rid,"reviewer-a","keep","Clear exceptional encounter seen")])
    b=export(tmp_path/"b.json",result["manifestSha256"],[
        feedback(rid,"reviewer-b","skip","Visually weak and not worthwhile")])
    plan=plan_review_discovery(root,packet,[a,b])
    row=plan["decisions"][0]
    assert row["route"]=="human_disagreement"
    assert row["voteCounts"]=={"keep":1,"skip":1}
    assert row["groundTruthEventAdjudicated"] is False
    assert row["trainingExampleApproved"] is False
    assert row["contextSearchProposals"]==[]
    with pytest.raises(ValueError,match="Duplicate reviewer"):
        plan_review_discovery(root,packet,[a,a])


def test_forged_media_or_mismatched_pilot_is_not_accepted(frozen,tmp_path):
    root,packet,result,ready=frozen
    rid=ready[0]["recordId"]
    wrong=export(tmp_path/"wrong.json","0"*64,
                 [feedback(rid,"reviewer-001","keep","Great footage of skiing")])
    with pytest.raises(ValueError,match="frozen pilot"):
        plan_review_discovery(root,packet,[wrong])
    missing=next(r["recordId"] for r in
                 json.loads((packet/"curation-manifest.json").read_text())["records"]
                 if r["recordId"] not in {x["recordId"] for x in ready})
    invalid=export(tmp_path/"missing.json",result["manifestSha256"],
                   [feedback(missing,"reviewer-001","keep",
                             "Pretend source media was available")])
    with pytest.raises(ValueError,match="unavailable"):
        plan_review_discovery(root,packet,[invalid])
    with pytest.raises(ValueError,match="1..25"):
        plan_review_discovery(root,packet,[])


def test_cli_plan_is_metadata_only(frozen,tmp_path,capsys,monkeypatch):
    root,packet,result,ready=frozen
    path=export(tmp_path/"cli.json",result["manifestSha256"],
                [feedback(ready[0]["recordId"],"reviewer-001",
                          "keep","I would keep this beautiful action event")])
    old_is_file,old_stat,old_resolve=Path.is_file,Path.stat,Path.resolve
    def no_sdcall(func):
        def wrapped(self,*args,**kwargs):
            if str(self).startswith("/Volumes/UNMOUNTED_DJI"):
                pytest.fail("No original source-media I/O permitted")
            return func(self,*args,**kwargs)
        return wrapped
    monkeypatch.setattr(Path,"is_file",no_sdcall(old_is_file))
    monkeypatch.setattr(Path,"stat",no_sdcall(old_stat))
    monkeypatch.setattr(Path,"resolve",no_sdcall(old_resolve))
    out=root/"reports"/"cli-plan"
    assert main(["--workspace",str(root),"highlight-feedback-plan",
                 "--pilot-dir",str(packet),"--reviews",str(path),
                 "--output-dir",str(out)])==0
    printed=json.loads(capsys.readouterr().out)
    assert printed["sourceVideoReads"]==0
    assert printed["provisionalHighlights"]==1
    assert printed["filesDeleted"]==0


def test_altered_report_local_preview_rejects_human_source_link(frozen,tmp_path):
    root,packet,result,ready=frozen
    rid=ready[0]["recordId"]
    path=export(tmp_path/"tamper.json",result["manifestSha256"],[
        feedback(rid,"reviewer-001","keep","A very nice action highlight")])
    proxy=packet/"media"/(rid+".mp4")
    proxy.write_bytes(b"tampered local preview"*20)
    with pytest.raises(ValueError,match="derivative missing/altered"):
        plan_review_discovery(root,packet,[path])


def test_report_media_symlink_never_dereferences_original(frozen,tmp_path):
    root,packet,result,ready=frozen
    rid=ready[0]["recordId"]
    path=export(tmp_path/"symlink.json",result["manifestSha256"],[
        feedback(rid,"reviewer-001","keep","Excellent natural encounter here")])
    proxy=packet/"media"/(rid+".mp4")
    proxy.unlink()
    proxy.symlink_to("/Volumes/UNMOUNTED_DJI/DCIM/evil.MP4")
    with pytest.raises(ValueError,match="derivative missing/altered"):
        plan_review_discovery(root,packet,[path])


def _make_studio(frozen,tmp_path,*,workers=2,scout_limit=6):
    from adventure_intelligence.highlight_feedback_discovery import save_review_discovery
    from adventure_intelligence.highlight_reel_studio import assemble_reel_studio
    root,packet,pilot,ready=frozen
    nature=feedback(ready[0]["recordId"],"reviewer-001","keep",
                    "Beautiful natural encounter worth highlighting")
    athletics=feedback(ready[1]["recordId"],"reviewer-001","keep",
                       "This is a great and readable action moment")
    athletics["goal"]="athletic_skill"
    exclude=feedback(ready[2]["recordId"],"reviewer-001","skip",
                     "Not a highlight but the whole run may be useful")
    review_file=export(tmp_path/"selected.json",pilot["manifestSha256"],
                       [nature,athletics,exclude])
    plan_dir=root/"reports"/"feedback-for-reels"
    created=save_review_discovery(root,packet,[review_file],plan_dir)
    studio_dir=root/"reports"/"interactive-reels"
    result=assemble_reel_studio(
        root,packet,Path(created["report"]),studio_dir,workers=workers,
        scout_limit=scout_limit)
    return root,packet,ready,result


def test_interactive_reel_studio_reuses_verified_local_proxy_and_runs_parallel(
        frozen,tmp_path,monkeypatch):
    from adventure_intelligence.highlight_reel_feedback import qualify_reel_feedback
    root,packet,ready,result=_make_studio(frozen,tmp_path)
    assert result["status"]=="interactive_reel_studio_created"
    assert result["localCacheWorkers"]==2
    assert result["humanSelectedAvailable"]==2
    assert result["sourceOriginalReads"]==0
    assert result["sourceFilesModified"] is False
    assert result["modelInferenceCalls"]==0
    manifest=json.loads(Path(result["manifest"]).read_text())
    assert manifest["sourceFilesModified"] is False
    assert manifest["trainingPerformed"] is False
    assert manifest["clipsRenderedToNewSingleMp4"]==0
    reels={x["reelId"]:x for x in manifest["reels"]}
    assert len(reels["human-selected"]["clipIds"])==2
    assert len(reels["nature"]["clipIds"])==1
    assert len(reels["athletic"]["clipIds"])==1
    assert all(x["provenance"]=="unreviewed_scout_window"
               for x in manifest["clips"] if not x["humanApproved"])
    for c in manifest["clips"]:
        if c["videoUrl"]:
            file=Path(result["directory"])/c["videoUrl"]
            assert file.is_file()
            assert file.read_bytes()==(packet/"media"/(c["clipId"]+".mp4")).read_bytes()
            assert file.stat().st_nlink==1  # independent private copy
    html=Path(result["index"]).read_text()
    assert "Reel Studio" in html
    assert "Mark selection in" in html
    assert "Mark at playhead" in html
    assert "Export annotations" in html
    assert "/Volumes/UNMOUNTED_DJI" not in html
    with pytest.raises(ValueError,match="NEW"):
        from adventure_intelligence.highlight_reel_studio import assemble_reel_studio
        assemble_reel_studio(root,packet,
            root/"reports"/"feedback-for-reels"/"review-discovery-plan.json",
            Path(result["directory"]),workers=2)


def test_reel_feedback_grounding_and_exact_trim_proposals(frozen,tmp_path):
    from adventure_intelligence.highlight_reel_feedback import (
        qualify_reel_feedback,save_reel_feedback
    )
    root,packet,ready,result=_make_studio(frozen,tmp_path,scout_limit=0)
    studio_dir=Path(result["directory"])
    manifest=json.loads(Path(result["manifest"]).read_text())
    assert all(x["humanApproved"] for x in manifest["clips"])
    clip=next(x for x in manifest["clips"] if x["videoUrl"])
    a=clip["startMs"]+2000
    b=clip["startMs"]+4000
    review={
        "version":"klip-reel-timed-reactions-v1",
        "studioManifestSha256":result["manifestSha256"],
        "reviewer":"reviewer-001",
        "notes":[{
            "id":"note-0001","clipId":clip["clipId"],
            "annotatorId":"reviewer-001","kind":"retrim",
            "startMs":a,"endMs":b,
            "text":"Start here for the setup and end just after the payoff",
        },{
            "id":"note-0002","clipId":clip["clipId"],
            "annotatorId":"reviewer-001","kind":"standout",
            "startMs":b,"endMs":b,"text":"This frame is really interesting",
        }],
    }
    path=tmp_path/"timed-annotations.json"
    path.write_text(json.dumps(review))
    report=qualify_reel_feedback(root,studio_dir,path)
    assert report["humanMarkerCount"]==2
    assert report["annotatedClips"]==1
    assert len(report["humanProposedRetrims"])==1
    assert len(report["humanInterestDiscoveryProposals"])==1
    interest=report["humanInterestDiscoveryProposals"][0]
    assert interest["sourceMediaReadAuthorized"] is False
    assert interest["humanHighlightApprovedAutomatically"] is False
    assert interest["startMs"]<=b<interest["endMs"]
    assert report["humanProposedRetrims"][0]["startMs"]==a
    assert report["humanProposedRetrims"][0]["endMs"]==b
    assert report["sourceOriginalReads"]==0
    assert report["trainingRightsVerified"] is False
    dest=root/"reports"/"timed-feedback"
    saved=save_reel_feedback(root,studio_dir,path,dest)
    assert saved["proposedRetrims"]==1
    assert saved["interestWindowProposals"]==1
    assert Path(saved["interestWindows"]).is_file()
    assert saved["sourceOriginalReads"]==0
    assert Path(saved["retrims"]).is_file()
    review["notes"][0]["endMs"]=clip["endMs"]
    path.write_text(json.dumps(review))
    with pytest.raises(ValueError,match="outside the source interval"):
        qualify_reel_feedback(root,studio_dir,path)
    review["notes"][0]["endMs"]=b
    review["studioManifestSha256"]="0"*64
    path.write_text(json.dumps(review))
    with pytest.raises(ValueError,match="exact frozen reel"):
        qualify_reel_feedback(root,studio_dir,path)


def test_reel_studio_scout_limit_zero_and_tampered_derivative(frozen,tmp_path):
    from adventure_intelligence.highlight_reel_feedback import qualify_reel_feedback
    root,packet,ready,result=_make_studio(frozen,tmp_path,workers=1,scout_limit=0)
    manifest=json.loads(Path(result["manifest"]).read_text())
    assert result["sportScoutingAvailable"]==0
    assert all(x["humanApproved"] for x in manifest["clips"])
    clip=next(x for x in manifest["clips"] if x["videoUrl"])
    blob={
        "version":"klip-reel-timed-reactions-v1",
        "studioManifestSha256":result["manifestSha256"],
        "notes":[{"id":"note-1","clipId":clip["clipId"],
                  "annotatorId":"reviewer-001","kind":"keep",
                  "startMs":clip["startMs"],"endMs":clip["startMs"],
                  "text":"Great moment"}],
    }
    notes=tmp_path/"valid.json";notes.write_text(json.dumps(blob))
    local=Path(result["directory"])/clip["videoUrl"]
    local.write_bytes(b"tampered report-only derivative"*4)
    with pytest.raises(ValueError,match="missing, changed"):
        qualify_reel_feedback(root,Path(result["directory"]),notes)


def test_offline_reel_javascript_syntax_and_privacy(frozen,tmp_path):
    import shutil,subprocess
    root,packet,ready,result=_make_studio(frozen,tmp_path)
    html=Path(result["index"]).read_text()
    assert "connect-src 'none'" in html
    assert "Training" not in html
    assert "__KLIP_STUDIO_PAYLOAD__" not in html
    node=shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable for static offline review script lint")
    script=html.split("<script>",1)[1].split("</script>",1)[0]
    check=subprocess.run([node,"--check","-"],input=script.encode(),
                         stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                         timeout=25,check=False)
    assert check.returncode==0,check.stderr.decode("utf-8","replace")


def test_reel_work_is_local_only_even_when_sdcard_unmounted(frozen,tmp_path,monkeypatch):
    from adventure_intelligence.highlight_reel_studio import assemble_reel_studio
    root,packet,ready,result=_make_studio(frozen,tmp_path)
    plan=root/"reports"/"feedback-for-reels"/"review-discovery-plan.json"
    old_stat,old_resolve,old_is_file=Path.stat,Path.resolve,Path.is_file
    def forbid_external(old):
        def wrapped(self,*a,**kw):
            if str(self).startswith("/Volumes/UNMOUNTED_DJI"):
                pytest.fail("No original removable video access!")
            return old(self,*a,**kw)
        return wrapped
    monkeypatch.setattr(Path,"stat",forbid_external(old_stat))
    monkeypatch.setattr(Path,"resolve",forbid_external(old_resolve))
    monkeypatch.setattr(Path,"is_file",forbid_external(old_is_file))
    more=assemble_reel_studio(
        root,packet,plan,root/"reports"/"read-only-again",
        workers=4,scout_limit=4)
    assert more["sourceOriginalReads"]==0
    assert more["localCacheWorkers"]==4


def test_optional_preview_reel_render_uses_only_valid_local_proxy(frozen,tmp_path):
    import shutil,subprocess
    from adventure_intelligence.highlight_reel_render import render_local_reels
    from adventure_intelligence.studio_preview_judge import preview_paths
    ffmpeg=shutil.which("ffmpeg")
    ffprobe=shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg and FFprobe are required for a real preview render test")
    root,packet,pilot,ready=frozen
    rid=ready[0]["recordId"]
    aid=ready[0]["source"]["assetSha256"]
    real=tmp_path/"real-valid-source-preview.mp4"
    ff=subprocess.run([
        ffmpeg,"-hide_banner","-loglevel","error","-nostdin","-y",
        "-f","lavfi","-i","color=c=blue:s=128x72:r=10",
        "-t","1.5","-an","-c:v","libx264","-pix_fmt","yuv420p",str(real)
    ],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
    assert ff.returncode==0,ff.stderr.decode("utf-8","replace")
    # Only synthetic derivative fixture bytes are replaced. No real
    # source media or user's archive is accessed in CI.
    preview,receipt=preview_paths(root,aid,1000,21000)
    blob=real.read_bytes()
    preview.write_bytes(blob)
    digest=sha256(blob).hexdigest()
    old=json.loads(receipt.read_text())
    old["previewSha256"]=digest
    receipt.write_text(json.dumps(old))
    view=json.loads((packet/"presentation.json").read_text())
    view["records"][rid]["proxySha256"]=digest
    (packet/"presentation.json").write_text(json.dumps(view))
    root,packet,ready,result=_make_studio(frozen,tmp_path,scout_limit=0)
    studio=Path(result["directory"])
    output=root/"reports"/"rendered-nature"
    rendered=render_local_reels(root,studio,output,reel_id="nature",workers=2)
    assert rendered["renderedReels"]==1
    assert rendered["originalSourceReads"]==0
    manifest=json.loads(Path(rendered["report"]).read_text())
    assert manifest["sourcePreviewOnly"] is True
    assert manifest["fullResolutionSourceRender"] is False
    assert manifest["audioPreserved"] is False
    assert manifest["reels"][0]["status"]=="rendered_preview_mp4"
    produced=output/"nature.mp4"
    assert produced.is_file() and produced.stat().st_size>100
    probe=subprocess.run([
        ffprobe,"-v","error","-show_entries","stream=codec_name,width,height",
        "-select_streams","v:0","-of","json",str(produced)
    ],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=25,check=True)
    stream=json.loads(probe.stdout)["streams"][0]
    assert stream["codec_name"]=="h264"
    assert (stream["width"],stream["height"])==(960,540)
    with pytest.raises(ValueError,match="NEW"):
        render_local_reels(root,studio,output,reel_id="nature")
    with pytest.raises(ValueError,match="unreviewed"):
        render_local_reels(root,studio,root/"reports"/"unapproved",
                           reel_id="sport-scout")
    assert not (root/"reports"/"unapproved").exists()


def test_preview_renderer_fails_closed_if_cached_reel_was_tampered(frozen,tmp_path):
    import shutil
    from adventure_intelligence.highlight_reel_render import render_local_reels
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg unavailable")
    root,packet,ready,result=_make_studio(frozen,tmp_path,scout_limit=0)
    studio=Path(result["directory"])
    manifest=json.loads(Path(result["manifest"]).read_text())
    nature=next(c for c in manifest["clips"] if c["category"]=="nature")
    local=studio/nature["videoUrl"]
    local.write_bytes(b"tampered"+local.read_bytes())
    exported=render_local_reels(
        root,studio,root/"reports"/"fail-closed-render",reel_id="nature")
    assert exported["renderedReels"]==0
    assert exported["failedReels"]==1
    report=json.loads(Path(exported["report"]).read_text())
    assert report["reels"][0]["status"]=="not_rendered_missing_or_invalid_proxy"
    assert not (root/"reports"/"fail-closed-render"/"nature.mp4").exists()


def test_next_pass_prioritizes_human_requests_and_binds_cached_source_time(frozen,tmp_path):
    from adventure_intelligence.highlight_adaptive_queue import save_adaptive_queue
    from adventure_intelligence.highlight_reel_feedback import save_reel_feedback
    root,packet,ready,studio=_make_studio(frozen,tmp_path,workers=2,scout_limit=6)
    manifest=json.loads(Path(studio["manifest"]).read_text())
    clip=next(c for c in manifest["clips"] if c["videoUrl"] and c["humanApproved"])
    source_start=clip["startMs"]
    annotations={
        "version":"klip-reel-timed-reactions-v1",
        "studioManifestSha256":studio["manifestSha256"],
        "notes":[{
            "id":"synthetic-retrim-1","clipId":clip["clipId"],
            "annotatorId":"reviewer-001","kind":"retrim",
            "startMs":source_start+3000,"endMs":source_start+8000,
            "text":"This is the best complete section",
        },{
            "id":"synthetic-standout-2","clipId":clip["clipId"],
            "annotatorId":"reviewer-001","kind":"standout",
            "startMs":source_start+6500,"endMs":source_start+6500,
            "text":"A decisive, clearly visible moment",
        }],
    }
    export_path=tmp_path/"timed.json"
    export_path.write_text(json.dumps(annotations))
    feedback=save_reel_feedback(root,Path(studio["directory"]),export_path,
                                root/"reports"/"timed-for-queue")
    assert feedback["interestWindowProposals"]==1
    output=root/"reports"/"adaptive-queue"
    result=save_adaptive_queue(
        root,packet,root/"reports"/"feedback-for-reels"/"review-discovery-plan.json",
        Path(studio["directory"]),output,
        timed_feedback=Path(feedback["report"]),limit=30,per_source=3)
    assert result["originalSourceReads"]==0
    assert result["modelsTrained"]==0
    assert result["candidates"]>=2
    queue=json.loads(Path(result["queue"]).read_text())
    assert queue["trainingPerformed"] is False
    assert queue["originalMediaReads"]==0
    assert queue["selectionIsReviewPriorityNotPredictedQualityScore"]
    selected=queue["selected"]
    assert selected[0]["kind"]=="retrim"
    trim=selected[0]
    assert trim["cachedReviewCoverage"]=="full"
    assert trim["cachedReviewCoverageFraction"]==1.
    assert trim["linkedStudioClipId"]==clip["clipId"]
    assert trim["seekSourceMs"]>=source_start+3000
    assert trim["eventExistenceOrSuccessVerified"] is False
    assert trim["originalSourceReadAuthorized"] is False
    assert any(c["kind"]=="marked_interest" for c in selected)
    assert any(c["kind"]=="prior_context" for c in selected)
    html=Path(result["html"]).read_text()
    assert "Next review pass" in html
    assert "Inspect available footage in Reel Studio" in html
    assert "?clip=" in html and "&amp;t=" in html
    assert "/Volumes/UNMOUNTED_DJI" not in html
    assert "Your timecoded interest" in html
    with pytest.raises(ValueError,match="NEW"):
        save_adaptive_queue(
            root,packet,root/"reports"/"feedback-for-reels"/"review-discovery-plan.json",
            Path(studio["directory"]),output,timed_feedback=Path(feedback["report"]))


def test_next_pass_is_deterministic_and_limits_per_source(frozen,tmp_path):
    from adventure_intelligence.highlight_adaptive_queue import save_adaptive_queue
    root,packet,ready,studio=_make_studio(frozen,tmp_path,scout_limit=6)
    plan=root/"reports"/"feedback-for-reels"/"review-discovery-plan.json"
    first=save_adaptive_queue(
        root,packet,plan,Path(studio["directory"]),
        root/"reports"/"queue-one",limit=50,per_source=1)
    second=save_adaptive_queue(
        root,packet,plan,Path(studio["directory"]),
        root/"reports"/"queue-two",limit=50,per_source=1)
    a=json.loads(Path(first["queue"]).read_text())
    b=json.loads(Path(second["queue"]).read_text())
    assert a==b
    sources=[c["sourceAssetSha256"] for c in a["selected"]]
    assert len(sources)==len(set(sources))
    assert all(c["selectionPriorityTier"] in (0,1,2) for c in a["selected"])
    assert all(c["editorialApprovalInferred"] is False for c in a["selected"])
    assert all(c["newMediaPrepared"] is False for c in a["selected"])
    assert a["originalMediaDecodes"]==0
    assert a["trainingPerformed"] is False


def test_next_pass_rejects_mismatched_studio_and_fabricated_timing(frozen,tmp_path):
    from adventure_intelligence.highlight_adaptive_queue import save_adaptive_queue
    root,packet,ready,studio=_make_studio(frozen,tmp_path,scout_limit=0)
    plan=root/"reports"/"feedback-for-reels"/"review-discovery-plan.json"
    corrupted=json.loads(plan.read_text())
    corrupted["frozenPilotManifestSha256"]="0"*64
    bad=root/"reports"/"bad-plan.json"
    bad.write_text(json.dumps(corrupted))
    with pytest.raises(ValueError,match="share one frozen source manifest"):
        save_adaptive_queue(root,packet,bad,Path(studio["directory"]),
                            root/"reports"/"bad-queue")
    valid=json.loads(plan.read_text())
    if valid["sourceContextProposals"]:
        valid["sourceContextProposals"][0]["endMs"]=99_999_999_999
        bad.write_text(json.dumps(valid))
        with pytest.raises(ValueError,match="outside catalog-backed"):
            save_adaptive_queue(root,packet,bad,Path(studio["directory"]),
                                root/"reports"/"impossible-queue")
    assert not (root/"reports"/"impossible-queue").exists()


def test_next_pass_is_metadata_only_on_unmounted_dji(frozen,tmp_path,monkeypatch):
    from adventure_intelligence.highlight_adaptive_queue import save_adaptive_queue
    root,packet,ready,studio=_make_studio(frozen,tmp_path)
    old_stat,old_resolve,old_is_file=Path.stat,Path.resolve,Path.is_file
    def forbid_external(old):
        def check(self,*args,**kwargs):
            if str(self).startswith("/Volumes/UNMOUNTED_DJI"):
                pytest.fail("Adaptive queue must never probe external original media")
            return old(self,*args,**kwargs)
        return check
    monkeypatch.setattr(Path,"stat",forbid_external(old_stat))
    monkeypatch.setattr(Path,"resolve",forbid_external(old_resolve))
    monkeypatch.setattr(Path,"is_file",forbid_external(old_is_file))
    result=save_adaptive_queue(
        root,packet,root/"reports"/"feedback-for-reels"/"review-discovery-plan.json",
        Path(studio["directory"]),root/"reports"/"no-media-queue",per_source=2)
    assert result["originalSourceReads"]==0


def test_diagnose_missing_presentation_then_recover_exact_frozen_human_pilot(
        frozen,tmp_path,monkeypatch):
    from adventure_intelligence.highlight_pilot_recovery import (
        diagnose_local_pilot,recover_local_pilot)
    from adventure_intelligence.highlight_reel_studio import assemble_reel_studio
    from adventure_intelligence.highlight_feedback_discovery import save_review_discovery
    root,packet,original,ready=frozen
    human=export(tmp_path/"human.json",original["manifestSha256"],[
        feedback(ready[0]["recordId"],"reviewer-001","keep",
                 "The footage contains a beautiful visible encounter")])
    plan_root=root/"reports"/"human-feedback"
    plan=save_review_discovery(root,packet,[human],plan_root)
    original_bytes=Path(original["manifest"]).read_bytes()
    (packet/"presentation.json").unlink()
    result=diagnose_local_pilot(root,packet,Path(plan["report"]))
    assert result["files"]["curationManifest"]["status"]=="ready"
    assert result["files"]["presentation"]["status"]=="missing"
    assert result["studioMetadataPreflightReady"] is False
    assert result["originalMediaReads"]==0
    with pytest.raises(ValueError,match=r"presentation\.json"):
        assemble_reel_studio(root,packet,Path(plan["report"]),
                             root/"reports"/"cannot-start")
    assert not (root/"reports"/"cannot-start").exists()
    recovered_dir=root/"reports"/"recovered-pilot"
    recovered=recover_local_pilot(root,packet,Path(plan["report"]),recovered_dir)
    assert recovered["originalSourceReads"]==0
    assert recovered["originalPilotFilesModified"] is False
    assert recovered["frozenManifestSha256"]==original["manifestSha256"]
    assert (recovered_dir/"curation-manifest.json").read_bytes()==original_bytes
    assert recovered["verifiedReviewProxies"]>0
    assert recovered["missingReviewProxies"]>=0
    healthy=diagnose_local_pilot(root,recovered_dir,Path(plan["report"]))
    assert healthy["studioMetadataPreflightReady"]
    studio=assemble_reel_studio(root,recovered_dir,Path(plan["report"]),
                                root/"reports"/"studio-recovered",workers=2)
    assert studio["humanSelectedAvailable"]==1
    assert studio["sourceOriginalReads"]==0
    assert (packet/"presentation.json").exists() is False
    assert original_bytes==Path(original["manifest"]).read_bytes()


def test_pilot_recovery_rejects_mismatched_reviews_and_never_mutates_original(frozen,tmp_path):
    from adventure_intelligence.highlight_pilot_recovery import recover_local_pilot
    from adventure_intelligence.highlight_feedback_discovery import save_review_discovery
    root,packet,original,ready=frozen
    human=export(tmp_path/"human.json",original["manifestSha256"],[
        feedback(ready[0]["recordId"],"reviewer-001","keep",
                 "Beautiful scene with clear action")])
    plan=save_review_discovery(root,packet,[human],root/"reports"/"feedback-for-recovery")
    forged=json.loads(Path(plan["report"]).read_text())
    forged["frozenPilotManifestSha256"]="f"*64
    forged_path=root/"reports"/"wrong-frozen-feedback.json"
    forged_path.write_text(json.dumps(forged))
    original_bytes=Path(original["manifest"]).read_bytes()
    with pytest.raises(ValueError,match="NOT linked"):
        recover_local_pilot(root,packet,forged_path,root/"reports"/"must-not-create")
    assert not (root/"reports"/"must-not-create").exists()
    assert Path(original["manifest"]).read_bytes()==original_bytes


def test_pilot_recovery_never_follows_external_original_video_paths(frozen,tmp_path,monkeypatch):
    from adventure_intelligence.highlight_pilot_recovery import recover_local_pilot
    from adventure_intelligence.highlight_feedback_discovery import save_review_discovery
    root,packet,original,ready=frozen
    human=export(tmp_path/"human.json",original["manifestSha256"],[
        feedback(ready[0]["recordId"],"reviewer-001","keep",
                 "This scene has an interesting focal subject")])
    plan=save_review_discovery(root,packet,[human],root/"reports"/"recovery-with-io-guard")
    old_stat,old_is_file,old_resolve=Path.stat,Path.is_file,Path.resolve
    def guard(fn):
        def checked(self,*args,**kwargs):
            if str(self).startswith("/Volumes/UNMOUNTED_DJI"):
                pytest.fail("Never probe the unmounted original source card")
            return fn(self,*args,**kwargs)
        return checked
    monkeypatch.setattr(Path,"stat",guard(old_stat))
    monkeypatch.setattr(Path,"is_file",guard(old_is_file))
    monkeypatch.setattr(Path,"resolve",guard(old_resolve))
    result=recover_local_pilot(root,packet,Path(plan["report"]),
                               root/"reports"/"recovered-without-sd")
    assert result["originalSourceReads"]==0


def test_recovery_marks_unverifiable_media_unavailable_instead_of_relabeling(frozen,tmp_path):
    from adventure_intelligence.highlight_pilot_recovery import recover_local_pilot
    from adventure_intelligence.highlight_feedback_discovery import save_review_discovery
    from adventure_intelligence.studio_preview_judge import preview_paths
    root,packet,original,ready=frozen
    human=export(tmp_path/"human.json",original["manifestSha256"],[
        feedback(ready[0]["recordId"],"reviewer-001","keep",
                 "Wildlife encounter appears promising in this cut")])
    plan=save_review_discovery(root,packet,[human],root/"reports"/"recovery-with-missing-cache")
    rec=ready[0];rid=rec["recordId"];aid=rec["source"]["assetSha256"]
    cache,receipt=preview_paths(root,aid,rec["event"]["startMs"],rec["event"]["endMs"])
    receipt.unlink()
    (packet/"presentation.json").unlink()
    target=root/"reports"/"recovered-with-unknown"
    recovered=recover_local_pilot(root,packet,Path(plan["report"]),target)
    available=json.loads((target/"presentation.json").read_text())["records"][rid]
    assert available["verifiedProxyReady"] is False
    assert available["requiresSourceReadForPreview"] is True
    assert not (target/"media"/(rid+".mp4")).exists()
    assert recovered["originalSourceReads"]==0


def test_cached_secondlook_batches_checkpoint_and_completed_resume(tmp_path,monkeypatch):
    from adventure_intelligence import cached_secondlook_batches as module
    root=tmp_path/"workspace"
    assignments=root/"reports"/"review-assignments.json"
    assignments.parent.mkdir(parents=True)
    with connect(root):
        pass  # The output safety guard requires a real local catalog.
    assignments.write_text('{"version":"synthetic-frozen-fixture"}')
    calls=[]
    def fake_run(workspace,path,*,model,limit,run,exclude_review_ids):
        calls.append((model,limit,run,tuple(exclude_review_ids)))
        if len(calls)==1:
            assert limit==8 and run
            assert not exclude_review_ids
            return {
                "status":"completed","selected":2,"eligibleUnreviewed":2,
                "items":[{"reviewId":"synthetic-a","status":"suggested"},
                         {"reviewId":"synthetic-b","status":"failed"}],
                "newLocalModelCalls":1,"cachedSecondLooksReused":0,"failed":1,
            }
        assert set(exclude_review_ids)=={"synthetic-a","synthetic-b"}
        return {
            "status":"completed","selected":0,"eligibleUnreviewed":0,
            "items":[],"newLocalModelCalls":0,
            "cachedSecondLooksReused":0,"failed":0,
        }
    monkeypatch.setattr(module,"inspect_temporal_candidates",fake_run)
    output=root/"reports"/"checkpointed-secondlooks"
    first=module.run_cached_secondlook_batches(
        root,assignments,output,model="gemma3:4b",batches=1)
    assert first["status"]=="cached_secondlook_budget_reached"
    assert first["reviewIdsCovered"]==2
    assert first["failed"]==1
    assert (output/"secondlook-state.json").is_file()
    second=module.run_cached_secondlook_batches(
        root,assignments,output,model="gemma3:4b",batches=5,resume=True)
    assert second["status"]=="cached_secondlook_batches_complete"
    assert second["batchesRecorded"]==2
    assert len(calls)==2
    repeated=module.run_cached_secondlook_batches(
        root,assignments,output,model="gemma3:4b",batches=5,resume=True)
    assert repeated==second
    assert len(calls)==2
    with pytest.raises(ValueError,match="cannot change"):
        module.run_cached_secondlook_batches(
            root,assignments,output,model="different:4b",resume=True)


def test_overnight_script_bash_syntax():
    import shutil
    import subprocess
    shell=shutil.which("bash")
    if not shell:
        pytest.skip("bash unavailable")
    script=Path(__file__).resolve().parents[1]/"scripts"/"klip_overnight_cache_only.sh"
    assert script.is_file()
    check=subprocess.run([shell,"-n",str(script)],
                         stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                         timeout=15,check=False)
    assert check.returncode==0,check.stderr.decode("utf-8","replace")
    content=script.read_text()
    assert "highlight-pilot-recover" in content
    assert "highlight-next-pass" in content
    assert "social-lens-temporal" in content
    assert "archive-cached-secondlook-batches" in content
    assert "--prepare-previews" in content  # documented prohibition only
    assert "archive-local-triage " not in content  # never run source-reading triage


def test_pilot_recovery_can_precede_human_feedback_when_no_plan_exists(frozen,tmp_path):
    from adventure_intelligence.highlight_pilot_recovery import (
        recover_local_pilot, diagnose_local_pilot
    )
    from adventure_intelligence.highlight_feedback_discovery import save_review_discovery
    root,original,metadata,ready=frozen
    manifest_before=Path(metadata["manifest"]).read_bytes()
    (original/"presentation.json").unlink()
    (original/"selection-report.json").unlink()
    recovered=root/"reports"/"recovered-no-human-plan"
    result=recover_local_pilot(root,original,None,recovered)
    assert result["originalSourceReads"]==0
    assert recovered.joinpath("curation-manifest.json").read_bytes()==manifest_before
    assert not (recovered/"selection-report.json").exists()
    review_file=export(tmp_path/"real-human-review.json",metadata["manifestSha256"],[
        feedback(ready[0]["recordId"],"reviewer-001","keep",
                 "A nice visible highlight in this exact window"),
    ])
    planned=save_review_discovery(
        root,recovered,[review_file],root/"reports"/"newly-discovered-feedback")
    doc=json.loads(Path(planned["report"]).read_text())
    assert doc["selectionReportPresent"] is False
    assert doc["selectionRosterSource"]=="audited-frozen-manifest"
    assert doc["provisionalHighlightCount"]==1
    assert result["originalPilotFilesModified"] is False


def test_feedback_locator_matches_frozen_manifest_and_prefers_matching_plan(frozen,tmp_path):
    from adventure_intelligence.highlight_night_feedback import resolve_human_feedback
    from adventure_intelligence.highlight_feedback_discovery import save_review_discovery
    root,pilot,original,ready=frozen
    manifest=pilot/"curation-manifest.json"
    wrong=root/"reports"/"not-there"/"review-discovery-plan.json"
    none=resolve_human_feedback(root,manifest,preferred_plan=wrong)
    assert none["status"]=="no_matching_human_feedback"
    assert none["noSourceMediaReads"] is True
    reviews=export(root/"exports"/"my-curation-reviews.json",
                   original["manifestSha256"],[
        feedback(ready[0]["recordId"],"reviewer-001","keep",
                 "A noteworthy visible source event")
    ]) if (root/"exports").is_dir() else None
    if reviews is None:
        (root/"exports").mkdir(parents=True)
        reviews=export(root/"exports"/"my-curation-reviews.json",
                       original["manifestSha256"],[
            feedback(ready[0]["recordId"],"reviewer-001","keep",
                     "A noteworthy visible source event")
        ])
    found=resolve_human_feedback(root,manifest,preferred_plan=wrong,
                                  preferred_reviews=reviews)
    assert found["status"]=="matching_raw_review_export"
    assert found["reviewedWindows"]==1
    assert found["selectedPath"]==str(reviews)
    plan=save_review_discovery(
        root,pilot,[reviews],root/"reports"/"confirmed-human-plan")
    chosen=resolve_human_feedback(root,manifest,
                                  preferred_plan=Path(plan["report"]))
    assert chosen["status"]=="matching_existing_plan"
    assert chosen["selectedPath"]==str(Path(plan["report"]).resolve())


def test_overnight_heredoc_python_syntax():
    import ast
    from pathlib import Path
    script=Path(__file__).resolve().parents[1]/"scripts"/"klip_overnight_cache_only.sh"
    lines=script.read_text().splitlines()
    snippets=[]
    collecting=False
    fragment=[]
    for line in lines:
        if not collecting and line.endswith("<<'PY'"):
            collecting=True
            fragment=[]
        elif collecting and line=="PY":
            snippets.append("\\n".join(fragment)+"\\n")
            collecting=False
        elif collecting:
            fragment.append(line)
    assert not collecting
    assert len(snippets)>=5
    for code in snippets:
        ast.parse(code)
    assert "highlight-pilot-recover" in script.read_text()
    assert "highlight-feedback-plan" in script.read_text()
    assert "matching_raw_review_export" in script.read_text()

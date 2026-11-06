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

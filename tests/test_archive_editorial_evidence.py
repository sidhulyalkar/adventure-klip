"""Quality-defect review and conservative cached action-intensity analysis."""
from __future__ import annotations

from pathlib import Path

import pytest

from adventure_intelligence import archive_editorial_audit as audit
from adventure_intelligence.benchmark_feedback import validate_human_review
from adventure_intelligence.catalog import connect


def benchmark():
    asset="a"*64
    rows={}
    for i,(sport,role) in enumerate([
        ("ski","candidate_hypothesis"),("snorkeling","control_systematic")
    ]):
        rid=str(i+1)*20
        rows[rid]={"reviewId":rid,"assetId":asset,
                   "startMs":i*22000,"endMs":i*22000+14000,
                   "selectionGroup":sport,"role":role,
                   "basename":f"DJI_{i}.MP4"}
    return {"assignments":rows,"benchmarkId":"example-benchmark"}


def test_human_quality_flags_are_explicit_and_deletion_never_implicit():
    b=benchmark()
    rid=next(iter(b["assignments"]))
    base={"reviewId":rid,"reviewDecision":"skip"}
    result=validate_human_review(b,{
        **base,"qualityFlags":["off_center_subject","crooked_mount"],
        "deletionCandidate":True})
    assert result["qualityFlags"]==["crooked_mount","off_center_subject"]
    assert result["deletionCandidate"] is True
    assert result["deletionStatus"]=="interval_only_pending_full_source_review"
    assert validate_human_review(b,{
        **base,"qualityFlags":["crooked_mount"]})["deletionCandidate"] is False
    for payload in [
        {**base,"deletionCandidate":True},
        {**base,"qualityFlags":["unsupported_flag"]},
        {**base,"qualityFlags":["crooked_mount","crooked_mount"]},
        {**base,"qualityFlags":["crooked_mount"],"deletionCandidate":"yes"},
        {**base,"qualityFlags":["crooked_mount"],
         "reviewDecision":"keep","deletionCandidate":True},
        {**base,"qualityFlags":"crooked_mount"},
    ]:
        with pytest.raises(ValueError):
            validate_human_review(b,payload)


def test_relative_audio_plus_motion_can_propose_longer_ski_but_not_speed():
    motion=[{"t_ms":i*1000,"change":(.08 if i<30 else .32)}
            for i in range(70)]
    audio=[{"t_ms":i*1000,
            "rms":(.04 if i<30 else .22),
            "lowBandFraction":.65,"clippingFraction":0.0}
           for i in range(70)]
    # In the late high-motion, high-wind-like part of a ski descent.
    strong=audit.action_evidence(motion,audio,40000,54000,
                                 activity="ski",scene="ski_descent")
    assert strong["visualMotionPercentileWithinRecording"]>.70
    assert strong["audioRmsPercentileWithinRecording"]>.70
    assert strong["candidateHighActionWithCorroboration"] is True
    assert strong["longerSkiWindowSuggested"] is True
    assert strong["speedMph"] is None
    assert strong["speedKph"] is None
    assert audit.extended_window(40000,54000,70000)=={
        "startMs":34000,"endMs":60000,
        "note":"Edit proposal only; continuous action must be reviewed in playback"}

    # Loud sound on a stationary/motion-poor or non-ski scene is not speed.
    weak=audit.action_evidence(motion,audio,0,14000,
                               activity="ski",scene="ski_descent")
    assert weak["longerSkiWindowSuggested"] is False
    kayak=audit.action_evidence(motion,audio,40000,54000,
                                activity="snorkeling",scene="surface_or_boat")
    assert kayak["longerSkiWindowSuggested"] is False


def test_missing_or_clipped_sound_abstains_without_false_low_speed():
    motion=[{"t_ms":i*1000,"change":.12} for i in range(40)]
    missing=audit.action_evidence(motion,None,0,14000,
                                  activity="ski",scene="ski_descent")
    assert missing["audioRmsPercentileWithinRecording"] is None
    assert missing["candidateHighActionWithCorroboration"] is None
    assert missing["longerSkiWindowSuggested"] is False

    noisy=[{"t_ms":i*1000,"rms":.40,"clippingFraction":.12,
            "lowBandFraction":.9} for i in range(40)]
    clipped=audit.action_evidence(motion,noisy,0,14000,
                                  activity="ski",scene="ski_descent")
    assert clipped["audioClippingLikely"] is True
    assert clipped["longerSkiWindowSuggested"] is False


def test_window_bounds_are_clamped_and_source_read_only():
    assert audit.extended_window(0,10000,14000)["startMs"]==0
    assert audit.extended_window(0,10000,14000)["endMs"]==14000
    assert audit.extended_window(92000,99000,100000)["endMs"]==100000
    with pytest.raises(ValueError):
        audit.extended_window(1000,500,100000)


def test_cache_only_audit_and_explicit_full_source_deletion_review(
        tmp_path,monkeypatch):
    ws=tmp_path/"klip"
    ws.mkdir()
    aid="a"*64
    with connect(ws) as db:
        db.execute(
            "INSERT INTO assets "
            "(id,sha256,stored_path,size_bytes,suffix,duration,activity) "
            "VALUES (?,?,?,?,?,?,?)",
            (aid,aid,str(tmp_path/"external-original.MP4"),6400,
             ".mp4",45.0,"ski"))
    b=benchmark()
    rows=list(b["assignments"].values())
    review={
        rows[0]["reviewId"]:{"reviewDecision":"keep","qualityFlags":[],
                            "deletionCandidate":False},
        rows[1]["reviewId"]:{"reviewDecision":"skip",
                            "qualityFlags":["crooked_mount","off_center_subject"],
                            "deletionCandidate":True},
    }
    monkeypatch.setattr(audit,"load_benchmark",lambda *a:b)
    monkeypatch.setattr(audit,"benchmark_feedback_status",
                        lambda *a:{"reviewEntries":review})
    monkeypatch.setattr(audit,"raw_motion_for_asset",
                        lambda *a:[{"t_ms":i*1000,
                                    "change":(.08 if i<30 else .32)}
                                   for i in range(50)])
    monkeypatch.setattr(audit,"read_artifact",
                        lambda *a:{"series":[{"t_ms":i*1000,
                             "rms":(.04 if i<30 else .22),
                             "lowBandFraction":.7,"clippingFraction":0}
                             for i in range(50)]})
    monkeypatch.setattr(audit,"preview_paths",
                        lambda *a:(ws/"proxy.mp4",ws/"receipt.json"))
    monkeypatch.setattr(audit,"_check_cached",lambda *a,**k:True)
    monkeypatch.setattr(audit,"cached_adaptive_judgment",
                        lambda *a,**k:{"recommendation":"keep",
                                      "scene":"ski_descent"})
    report=audit.audit_editorial_evidence(ws,ws/"assignments.json",
                                          model="gemma3:4b")
    assert report["intervalCount"]==2
    assert report["reviewedHumanAlready"]==2
    assert report["originalVideosRead"]==0
    assert report["filesDeleted"]==0
    assert report["sourcesFlaggedForDeletionReview"]==1
    deletion=report["sourceDeletionReviewQueue"][0]
    assert deletion["mayDeleteOriginal"] is False
    assert deletion["sourceCoverageVerified"] is False
    assert deletion["status"]=="human_flagged_pending_full_source_review"
    assert deletion["intervals"][0]["reviewId"]==rows[1]["reviewId"]
    assert all(not x["originalSourceDeletionAuthorized"]
               for x in report["intervals"])

    destination=ws/"reports"/"quality-v1"
    saved=audit.write_editorial_audit(ws,ws/"assignments.json",destination,
                                     model="gemma3:4b")
    assert saved["sourceVideoReads"]==0
    assert (destination/"audit.json").exists()
    assert (destination/"index.html").exists()
    with pytest.raises(ValueError,match="new reports"):
        audit.write_editorial_audit(ws,ws/"assignments.json",destination,
                                   model="gemma3:4b")

"""Offline qualified packet -> evidence-ledger -> sport reel regression suite.

No test reads user's SSD or invents event truth. Dummy local preview bytes
exercise manifest and SHA checks; optional FFmpeg tests generate tiny synthetic
local frames and never touch originals.
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from adventure_intelligence import campaign_reels as cr
from adventure_intelligence.highlight_curation_pilot import (
    _record, VERSION as PILOT_VERSION, MANIFEST_VERSION
)
from adventure_intelligence.highlight_curation_review import REVIEW_VERSION


def make_packet(tmp_path: Path, sports=("ski", "bike", "snorkeling")):
    ws = tmp_path / "workspace"
    packet = ws / "reports" / "campaign" / "review-packet"
    (packet / "media").mkdir(parents=True)
    records = []
    selections = []
    presentations = {}
    for i, sport in enumerate(sports):
        aid = f"{i + 1:064x}"
        record = _record({"id":aid, "activity":sport, "duration":60.0},
                         10000, 30000)
        rid = record["recordId"]
        video = packet / "media" / f"{rid}.mp4"
        # Dummy bytes suffice for metadata-only planning.
        video.write_bytes(bytes([i + 3]) * 128)
        media_sha = sha256(video.read_bytes()).hexdigest()
        records.append(record)
        selections.append({
            "recordId":rid, "sourceAssetId":aid,
            "startMs":10000, "endMs":30000,
            "selectionRole":"uniform_timeline_control" if i % 2 else "motion_change_hypothesis",
            "heuristicScoreForAuditOnly":0.13 if i%2==0 else None
        })
        presentations[rid] = {
            "verifiedProxyReady":True,
            "proxyUrl":f"media/{rid}.mp4",
            "proxySha256":media_sha,
        }
    manifest = {"version":MANIFEST_VERSION, "records":records}
    body = (json.dumps(manifest,sort_keys=True)+"\n").encode()
    manifest_sha = sha256(body).hexdigest()
    (packet/"curation-manifest.json").write_bytes(body)
    (packet/"presentation.json").write_text(json.dumps({
        "version":PILOT_VERSION,
        "manifestSha256":manifest_sha,
        "records":presentations,
    }))
    (packet/"selection-report.json").write_text(json.dumps({
        "version":PILOT_VERSION,
        "manifestSha256":manifest_sha,
        "sourceScanFingerprint":"f"*64,
        "campaignAuditSha256":"e"*64,
        "selections":selections,
    }))
    progress={
        "scan":{"scanStateSha256":"f"*64},
        "reviewPacket":{"verifiedPlaybackClipsReported":len(records),
                        "matchesCurrentScanCheckpoint":True},
        "goNoGo":{"coarseScannedEveryCatalogSource":True},
    }
    return ws, packet, records, progress, manifest_sha


def test_packet_manifest_and_proxy_integrity(tmp_path):
    ws,packet,records,status,_=make_packet(tmp_path)
    rows,checks=cr._packet_candidates(ws,packet,status,None)
    assert len(rows)==3
    assert checks["verifiedPreviewCandidates"]==3
    assert set(x["sport"] for x in rows)==set(cr.SPORTS)
    assert all(x["verifiedProxy"] and not x["publicationApproved"] for x in rows)
    assert all(x["eventUnderstandingStatus"]=="unknown" for x in rows)
    assert all("not evidence" in x["why"][0]["text"]
               or "not a negative" in x["why"][0]["text"] for x in rows)


def test_reviewed_mode_preserves_human_reason_not_model_claim(tmp_path):
    ws,packet,records,status,manifest_sha=make_packet(tmp_path)
    review_path=ws/"reviews.json"
    keep=records[0]
    rid=keep["recordId"]
    review={
        "recordId":rid, "annotatorId":"reviewer-001",
        "role":"athlete","goal":"athletic_skill","choice":"keep",
        "outcome":"unknown","outcomeVisible":"ambiguous",
        "technicalQuality":3,"aestheticQuality":5,"eventReadability":4,
        "rationale":"Interesting progression, but landing is obscured",
        "eventPeakMs":21000,"resolutionMs":None,
    }
    review_path.write_text(json.dumps({
        "version":REVIEW_VERSION, "manifestSha256":manifest_sha,
        "reviews":[review],
    }))
    rows,_=cr._packet_candidates(ws,packet,status,review_path)
    selected=cr._choose(rows,"ski","reviewed",150,8)
    assert len(selected)==1
    why=selected[0]["why"]
    assert any(item["basis"]=="human_written_reason" and
               "landing is obscured" in item["text"] for item in why)
    assert selected[0]["humanGoal"]=="athletic_skill"
    assert selected[0]["humanPreferenceTieBreaker"]>3
    assert not selected[0]["publicationApproved"]
    assert cr._choose(rows,"bike","reviewed",150,8)==[]


def test_no_reviewed_keep_cannot_be_misreported_as_best(tmp_path):
    ws,packet,records,status,manifest_sha=make_packet(tmp_path)
    path=ws/"reviews.json"
    rid=records[0]["recordId"]
    path.write_text(json.dumps({
        "version":REVIEW_VERSION, "manifestSha256":manifest_sha,
        "reviews":[{
            "recordId":rid,"annotatorId":"reviewer-001",
            "role":"athlete","goal":"athletic_skill","choice":"skip",
            "outcome":"unknown","outcomeVisible":"ambiguous",
            "technicalQuality":3,"aestheticQuality":2,"eventReadability":2,
            "rationale":"Nothing is identifiable in this sequence",
        }],
    }))
    rows,_=cr._packet_candidates(ws,packet,status,path)
    assert cr._choose(rows,"ski","reviewed",150,8)==[]


def test_media_mutation_fails_verified_render_selection(tmp_path):
    ws,packet,records,status,_=make_packet(tmp_path)
    rid=records[0]["recordId"]
    (packet/"media"/(rid+".mp4")).write_bytes(b"modified"*30)
    rows,checks=cr._packet_candidates(ws,packet,status,None)
    assert checks["invalidProxyReceipts"]==1
    assert len(rows)==2


def test_packet_scan_fingerprint_mismatch_fails_closed(tmp_path):
    ws,packet,_,status,_=make_packet(tmp_path)
    status["scan"]["scanStateSha256"]="0"*64
    with pytest.raises(ValueError,match="fingerprint mismatch"):
        cr._packet_candidates(ws,packet,status,None)


def test_invalid_human_review_export_fails_closed(tmp_path):
    ws,packet,records,status,manifest_sha=make_packet(tmp_path)
    path=ws/"reviews.json"
    path.write_text(json.dumps({
        "version":REVIEW_VERSION,
        "manifestSha256":"a"*64,
        "reviews":[],
    }))
    with pytest.raises(ValueError,match="does not match"):
        cr._packet_candidates(ws,packet,status,path)


def test_diversity_before_repeated_source():
    a="1"*64
    b="2"*64
    rows=[]
    for aid,start in ((a,0),(a,20000),(b,0)):
        rows.append({
            "recordId":f"{aid[0]*22}{start//20000:02d}",
            "sourceAssetSha256":aid,"sourceStartMs":start,
            "sourceEndMs":start+20000,"sport":"ski",
            "selectionRole":"motion_change_hypothesis",
            "humanPreference":None,
        })
    selected=cr._choose(rows,"ski","exploratory",60,3)
    assert {x["sourceAssetSha256"] for x in selected}=={a,b}
    assert len(selected)==3


def test_full_plan_gated_on_SHA_status(monkeypatch,tmp_path):
    ws,packet,records,status,_=make_packet(tmp_path)
    audit=ws/"reports"/"campaign"/"audit"/"campaign-audit.json"
    audit.parent.mkdir()
    audit.write_text("{}")
    scan=ws/"reports"/"campaign"/"coarse-scan"
    scan.mkdir()
    monkeypatch.setattr(cr,"campaign_progress",lambda *a,**k:status)
    monkeypatch.setattr(cr,"_safe_dest",lambda root,path:Path(path))
    dest=ws/"reports"/"reel-experiment"
    summary=cr.plan_sport_reels(ws,audit,scan,packet,dest)
    assert summary["selected"]=={"ski":1,"bike":1,"snorkeling":1}
    assert summary["sourceOriginalReads"]==0
    assert summary["published"] is False
    plan=json.loads((dest/"reel-plan.json").read_text())
    assert plan["mode"]=="exploratory"
    assert all(not plan["reels"][s]["publicationApproved"] for s in cr.SPORTS)
    status["goNoGo"]["coarseScannedEveryCatalogSource"]=False
    with pytest.raises(ValueError,match="not SHA-qualified"):
        cr.plan_sport_reels(ws,audit,scan,packet,ws/"reports"/"blocked")


def test_symlinked_video_from_packet_does_not_resolve_to_original(tmp_path):
    ws,packet,records,status,_=make_packet(tmp_path)
    rid=records[0]["recordId"]
    video=packet/"media"/(rid+".mp4")
    video.unlink()
    video.symlink_to("/Volumes/DO_NOT_READ/recording.mp4")
    rows,counts=cr._packet_candidates(ws,packet,status,None)
    assert len(rows)==2
    assert counts["invalidProxyReceipts"]==1


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="FFmpeg/FFprobe unavailable")
def test_synthetic_proxy_reel_render_is_silent_and_has_valid_video(tmp_path):
    clips=[]
    for i,color in enumerate(("red","blue")):
        video=tmp_path/f"proxy-{i}.mp4"
        subprocess.run(["ffmpeg","-hide_banner","-loglevel","error",
                        "-f","lavfi","-i",f"color=c={color}:s=160x90:r=30",
                        "-t","1.2","-c:v","libx264","-pix_fmt","yuv420p",
                        "-y",str(video)],check=True,timeout=25)
        clips.append({
            "mediaPath":str(video),
            "proxySha256":sha256(video.read_bytes()).hexdigest(),
        })
    for style in ("hard_cut","short_crossfade"):
        output=tmp_path/(style+".mp4")
        result=cr.render_proxy_reel({
            "selected":clips,
            "transitionPolicy":style,
        },output)
        assert output.exists()
        assert result["segments"]==2
        assert result["originalVideoReads"]==0
        assert result["audio"] is False
        assert result["estimatedDurationSeconds"]>1.5

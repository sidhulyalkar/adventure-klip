"""Regression tests for retry isolation and bounded proxy-frame recovery."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from adventure_intelligence import archive_local_triage as triage
from adventure_intelligence import archive_temporal_look as temporal_runner
from adventure_intelligence import studio_preview_judge as preview
from adventure_intelligence import temporal_second_look as temporal
from adventure_intelligence import media


def assignments():
    rows=[]
    for i,(sport,role) in enumerate((
            ("snorkeling","candidate_hypothesis"),
            ("ski","candidate_hypothesis"),
            ("bike","candidate_hypothesis"),
            ("unresolved","control_systematic"),
            ("snorkeling","candidate_hypothesis"),
            ("bike","control_systematic"))):
        rows.append({
            "reviewId":str(i+1)*20,
            "assetId":"a"*64,
            "startMs":i*10000,
            "endMs":i*10000+8000,
            "selectionGroup":sport,
            "role":role,
            "basename":f"original-{i}.MP4",
        })
    return {row["reviewId"]:row for row in rows}


def test_triage_exclusions_are_temporary_and_do_not_label_anything(
        tmp_path,monkeypatch):
    rows=assignments()
    monkeypatch.setattr(triage,"load_benchmark",
                        lambda *a:{"assignments":rows})
    monkeypatch.setattr(triage,"benchmark_feedback_status",
                        lambda *a:{"reviewEntries":{}})
    excluded=(list(rows)[0],list(rows)[2])
    report=triage.run_local_triage(tmp_path,tmp_path/"assignments.json",
        model="gemma3:4b",policy="adaptive-v2",limit=4,
        exclude_review_ids=excluded)
    assert report["mode"]=="dry_run"
    assert report["selected"]==4
    assert report["excludedFromThisRun"]==2
    assert report["exclusionsAreOnlyForThisRun"] is True
    assert set(a["reviewId"] for a in report["items"]).isdisjoint(excluded)
    assert report["selectionRoles"]["control_systematic"]>=1
    assert report["humanLabelsWritten"]==0
    again=triage.run_local_triage(tmp_path,tmp_path/"assignments.json",
        model="gemma3:4b",limit=4)
    assert again["excludedFromThisRun"]==0
    assert list(rows)[0] in [a["reviewId"] for a in again["items"]]
    with pytest.raises(ValueError,match="Excluded review ID"):
        triage.run_local_triage(tmp_path,tmp_path/"assignments.json",
            model="gemma3:4b",exclude_review_ids=("f"*20,))
    with pytest.raises(ValueError,match="also be excluded"):
        triage.run_local_triage(tmp_path,tmp_path/"assignments.json",
            model="gemma3:4b",only_review_id=excluded[0],
            exclude_review_ids=(excluded[0],))


def test_three_frame_fast_seek_falls_back_on_same_short_proxy(
        tmp_path,monkeypatch):
    proxy=tmp_path/"short-proxy.mp4"
    proxy.write_bytes(b"proxy"*100)
    monkeypatch.setattr(media,"probe",lambda path:{"duration":8.0})
    monkeypatch.setattr(preview,"require_tool",lambda name:"ffmpeg")
    calls=[]
    def fake_run(cmd,**kw):
        class Done:
            returncode=0
        assert cmd[cmd.index("-i")+1]==str(proxy)
        calls.append(cmd)
        # First fast seek fails. Accurate seek on the same proxy succeeds.
        if len(calls)==1:
            class Failed:
                returncode=1
            return Failed()
        Path(cmd[-1]).write_bytes(b"J"*240)
        return Done()
    monkeypatch.setattr(preview.subprocess,"run",fake_run)
    images=preview._jpeg_samples(proxy)
    assert len(images)==3 and len(calls)==4
    assert calls[0].index("-ss")<calls[0].index("-i")
    assert calls[1].index("-ss")>calls[1].index("-i")


def test_three_frame_decoder_timeout_is_not_retried(
        tmp_path,monkeypatch):
    monkeypatch.setattr(media,"probe",lambda p:{"duration":10.0})
    monkeypatch.setattr(preview,"require_tool",lambda t:"ffmpeg")
    calls=[]
    def timed_out(cmd,**kwargs):
        calls.append(cmd)
        raise subprocess.TimeoutExpired(cmd,timeout=20)
    monkeypatch.setattr(preview.subprocess,"run",timed_out)
    with pytest.raises(subprocess.TimeoutExpired):
        preview._jpeg_samples(tmp_path/"short-proxy.mp4")
    assert len(calls)==1


def test_triage_classifies_model_timeout_separately_from_frame_timeout(
        tmp_path,monkeypatch):
    rows=assignments()
    monkeypatch.setattr(triage,"load_benchmark",
                        lambda *a:{"assignments":rows})
    monkeypatch.setattr(triage,"benchmark_feedback_status",
                        lambda *a:{"reviewEntries":{}})
    monkeypatch.setattr(triage,"SourceStream",lambda root:object())
    monkeypatch.setattr(triage,"preview_paths",
                        lambda *a:(tmp_path/"proxy.mp4",tmp_path/"receipt.json"))
    monkeypatch.setattr(triage,"_check_cached",lambda *a,**k:True)
    monkeypatch.setattr(triage,"cached_adaptive_judgment",
                        lambda *a,**k:None)
    def fail_model(*a,**k):
        raise RuntimeError("Local Ollama timed out")
    monkeypatch.setattr(triage,"adaptive_judgment",fail_model)
    out=triage.run_local_triage(tmp_path,tmp_path/"assignments.json",
        model="gemma3:4b",policy="adaptive-v2",only_review_id=list(rows)[0],
        run=True)
    assert out["items"][0]["errorCode"]=="judge_inference_timeout"
    monkeypatch.setattr(triage,"adaptive_judgment",
                        lambda *a,**k:(_ for _ in ()).throw(
                            subprocess.TimeoutExpired("ffmpeg",20)))
    out=triage.run_local_triage(tmp_path,tmp_path/"assignments.json",
        model="gemma3:4b",policy="adaptive-v2",only_review_id=list(rows)[0],
        run=True)
    assert out["items"][0]["errorCode"]=="judge_frame_sampling_timeout"


def test_temporal_exclusion_and_timeout_remain_independent(
        tmp_path,monkeypatch):
    rows=assignments()
    monkeypatch.setattr(temporal_runner,"load_benchmark",
                        lambda *a:{"assignments":rows})
    monkeypatch.setattr(temporal_runner,"benchmark_feedback_status",
                        lambda *a:{"reviewEntries":{}})
    monkeypatch.setattr(temporal_runner,"preview_paths",
                        lambda root,aid,start,end:(tmp_path/f"{start}.mp4",
                                                  tmp_path/f"{start}.json"))
    monkeypatch.setattr(temporal_runner,"_check_cached",lambda *a,**kw:True)
    monkeypatch.setattr(temporal_runner,"cached_adaptive_judgment",
                        lambda *a,**kw:{
                            "recommendation":"unsure",
                            "needsTemporalReview":True})
    monkeypatch.setattr(temporal_runner,"cached_second_look",lambda *a,**kw:None)
    excluded=list(rows)[0]
    dry=temporal_runner.inspect_temporal_candidates(
        tmp_path,tmp_path/"assignments.json",model="gemma3:4b",
        limit=4,exclude_review_ids=(excluded,))
    assert dry["excludedFromThisRun"]==1
    assert dry["selected"]==4
    assert excluded not in [a["reviewId"] for a in dry["items"]]
    def timeout(*a,**kw):
        raise subprocess.TimeoutExpired("ffmpeg",30)
    monkeypatch.setattr(temporal_runner,"second_look",timeout)
    failure=temporal_runner.inspect_temporal_candidates(
        tmp_path,tmp_path/"assignments.json",model="gemma3:4b",
        only_review_id=excluded,run=True)
    assert failure["status"]=="partial_failure"
    assert failure["items"][0]["errorCode"]=="frame_sampling_timeout"
    assert failure["humanReviewsModified"] is False


def test_seven_frame_failed_seek_recovers_without_source_file(
        tmp_path,monkeypatch):
    proxy=tmp_path/"verified-proxy.mp4"
    monkeypatch.setattr(temporal,"probe",lambda path:{"duration":14.0})
    monkeypatch.setattr(temporal,"require_tool",lambda name:"ffmpeg")
    commands=[]
    def fake_run(cmd,**kwargs):
        class OK:
            returncode=0
        commands.append(cmd)
        if len(commands)==1:
            class Failed:
                returncode=1
            return Failed()
        Path(cmd[-1]).write_bytes(b"J"*300)
        return OK()
    monkeypatch.setattr(temporal.subprocess,"run",fake_run)
    frames,times=temporal._samples(proxy)
    assert len(frames)==7 and len(times)==7
    assert len(commands)==8
    assert commands[0].index("-ss")<commands[0].index("-i")
    assert commands[1].index("-ss")>commands[1].index("-i")

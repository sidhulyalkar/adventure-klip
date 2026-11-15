"""Synthetic offline validation of the Klip V2 auto-scout experiment.

No actual user video, network traffic or unapproved model inference.
"""
from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from adventure_intelligence import auto_scout_v2 as scout
from adventure_intelligence.moment_edit_v2 import VERSION as MOMENT_VERSION


def row(i, source=None, start=1000, end=4000, hint="unknown"):
    rid = f"{i:024x}"
    src = source or f"{i:064x}"
    return {
        "recordId": rid, "sourceSha256": src,
        "sourceStartMs": start, "sourceEndMs": end,
        "sportHint": hint, "selectionRole": "uniform_timeline_control",
        "video": Path("/not-a-real-private-video"),
        "proxySha256": "b"*64,
    }


def proposal(activity="ski"):
    return {
        "activity": activity, "activityFrames": [0, 2],
        "categories": [{
            "tag": "ski_park", "frames": [1, 3],
            "observation": "A clearly visible terrain park feature.",
        }] if activity == "ski" else [],
        "qualityFlags": [{
            "kind": "water_drop", "frames": [4],
            "observation": "A rounded droplet is visible on the image.",
        }],
        "eventContinuity": "unresolved", "reviewPriority": "investigate",
        "summary": "Snow and a visible feature.",
        "uncertainty": "No continuous video or reliable action outcome.",
    }


def fixture_pilot(tmp_path, count=3):
    ws = tmp_path / "workspace"
    pilot = ws / "reports" / "v2-pilot"
    media = pilot / "media"
    media.mkdir(parents=True)
    rows = []
    for i in range(1, count+1):
        rid = f"{i:024x}"
        proxy = media / (rid + ".mp4")
        proxy.write_bytes((b"synthetic-proxy-" + bytes([i])) * 20)
        rows.append({
            "momentId": rid, "sourceAssetSha256": f"{i:064x}",
            "sourceProxySha256": sha256(proxy.read_bytes()).hexdigest(),
            "sourceStartMs": 10000, "sourceEndMs": 30000,
            "sportHint": "unknown", "selectionRole": "uniform_timeline_control",
        })
    plan = {
        "version": MOMENT_VERSION, "manifestSha256": "a"*64,
        "moments": rows,
    }
    (pilot / "moment-plan.json").write_text(json.dumps(plan, indent=2))
    return ws, pilot


def test_category_and_defect_proposals_are_evidence_limited():
    result = scout.validate_proposal(proposal())
    assert result["activity"] == "ski"
    assert result["automaticEditAuthorized"] is False
    assert result["trickOrLandingVerified"] is False
    assert result["humanApprovalRequired"] is True
    assert result["categories"][0]["tag"] == "ski_park"


@pytest.mark.parametrize("change", [
    lambda p: p.update(activity="snorkeling"),
    lambda p: p["categories"][0].update(tag="ski_nonexistent"),
    lambda p: p["categories"][0].update(frames=[7]),
    lambda p: p["categories"][0].update(frames=[0,0]),
    lambda p: p.update(activityFrames=[True]),
    lambda p: p["qualityFlags"][0].update(kind="made_up_defect"),
    lambda p: p.update(reviewPriority="definitely_best"),
    lambda p: p.pop("uncertainty"),
])
def test_bad_model_response_abstains(change):
    p = proposal()
    change(p)
    with pytest.raises(ValueError):
        scout.validate_proposal(p)


def test_round_robin_source_diversity():
    repeated = "a"*64
    rows = [row(3, source=repeated, start=3000),
            row(1, source=repeated, start=1000),
            row(2, source=repeated, start=2000),
            row(4, source="b"*64)]
    ordered = scout.diversify(rows)
    assert {x["sourceSha256"] for x in ordered[:2]} == {repeated, "b"*64}
    assert ordered[-1]["sourceStartMs"] == 3000
    assert scout.diversify(rows) == ordered


def test_private_pilot_receipts_rehashed_and_mismatch_rejected(tmp_path):
    ws, pilot = fixture_pilot(tmp_path)
    rows, digest = scout.load_pilot(ws, pilot)
    assert len(rows) == 3 and len(digest) == 64
    assert all(r["video"].is_file() for r in rows)
    (pilot/"media"/(rows[0]["recordId"]+".mp4")).write_bytes(b"changed"*25)
    with pytest.raises(ValueError, match="SHA mismatch"):
        scout.load_pilot(ws, pilot)


def test_pilot_symlink_refused(tmp_path):
    ws, pilot = fixture_pilot(tmp_path)
    proxy = next((pilot/"media").glob("*.mp4"))
    proxy.unlink()
    proxy.symlink_to("/Volumes/DO_NOT_OPEN/movie.mp4")
    with pytest.raises(ValueError, match="unsafe frozen proxy"):
        scout.load_pilot(ws, pilot)


def test_local_7_frame_model_hypothesis(monkeypatch, tmp_path):
    video = tmp_path/"synthetic.mp4"
    video.write_bytes(b"x"*128)
    monkeypatch.setattr(scout.temporal, "_samples",
                        lambda p: (["test-encoded-image"]*7, [100,200,300,400,500,600,700]))
    seen = []
    def transport(body):
        seen.append(body)
        return {"message": {"content": json.dumps(proposal())}}
    out = scout.infer_proxy(video, model="gemma3:4b", transport=transport)
    assert out["proposal"]["activity"] == "ski"
    assert out["sampledProxyTimeMs"] == [100,200,300,400,500,600,700]
    assert seen[0]["model"] == "gemma3:4b"
    assert seen[0]["messages"][0]["images"] == ["test-encoded-image"]*7
    assert seen[0]["stream"] is False
    assert out["proposal"]["automaticEditAuthorized"] is False


def test_report_retrieval_order_never_claims_accuracy():
    fake = []
    for i,p in enumerate(("routine", "review_first", "investigate")):
        record = {
            "recordId": f"{i+1:024x}", "sourceSha256": f"{i+1:064x}",
            "sourceStartMs": 0, "sourceEndMs": 20000, "status": "done",
            "analysis": {"proposal": {**scout.validate_proposal(proposal()), "reviewPriority": p},
                         "numericQuality": {"meanBrightness": .6}}
        }
        fake.append(record)
    report = scout.build_report(
        {r["recordId"]:r for r in fake},
        total=120, processed_limit=3, source_plan_sha="a"*64,
        model="gemma3:4b", pilot=Path("/private/pilot"))
    assert report["processed"] == 3
    assert report["remaining"] == 117
    assert report["reviewQueue"][0]["reviewPriority"] == "review_first"
    assert report["categoryProposals"]["ski_park"] == 3
    assert report["modelProposalsAreNotOwnerVerified"] is True
    html = scout.review_html(report)
    assert "connect-src 'none'" in html
    assert "media/000000000000000000000002.mp4" in html


def test_receipt_integrity_fails_on_changed_policy(tmp_path):
    ws, pilot = fixture_pilot(tmp_path, count=1)
    rows, plan_sha = scout.load_pilot(ws, pilot)
    out = ws/"reports"/"auto-scout"
    receipts = out/"receipts"
    receipts.mkdir(parents=True)
    record = {
        "version": scout.VERSION,
        "recordId": rows[0]["recordId"],
        "sourceSha256": rows[0]["sourceSha256"],
        "proxySha256": rows[0]["proxySha256"],
        "inputPlanSha256": plan_sha,
        "policyFingerprint": "a"*64,
        "status": "failed",
    }
    scout.save_json(receipts/(record["recordId"]+".json"), record)
    got = scout.read_receipts(out,rows,plan_sha,"a"*64)
    assert got[record["recordId"]]["status"] == "failed"
    with pytest.raises(ValueError, match="differs"):
        scout.read_receipts(out,rows,plan_sha,"b"*64)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg unavailable")
def test_proxy_signal_metrics_no_speed_claim(tmp_path):
    video = tmp_path / "synthetic.mp4"
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
         "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=30:d=2",
         "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=30,
        check=False)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    before = sha256(video.read_bytes()).hexdigest()
    measured = scout.quality_measurements(video)
    assert measured["sampledFrames"] >= 3
    assert "actual travel speed" in measured["interpretation"]
    assert sha256(video.read_bytes()).hexdigest() == before


def test_cli_dry_run_creates_nothing(tmp_path):
    ws,pilot = fixture_pilot(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "klip_auto_scout_experiment_v2.py"
    spec = importlib.util.spec_from_file_location("auto_scout_cli",script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    out = ws / "reports" / "proposed-output"
    args = type("Args",(),{"workspace":ws,"pilot":pilot,"output":out,
                           "model":"gemma3:4b","max_items":2,
                           "run":False,"resume":False,"retry_failures":False,
                           "report_every":2})()
    report = module.execute(args)
    assert report["requested"] == 2
    assert report["status"] == "validated_dry_run_no_local_model_calls"
    assert not out.exists()


def test_cli_checkpoint_resume_avoids_duplicate_inference(tmp_path,monkeypatch):
    ws,pilot = fixture_pilot(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "klip_auto_scout_experiment_v2.py"
    spec = importlib.util.spec_from_file_location("auto_scout_cli",script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module,"_ollama_preflight",lambda model:None)
    monkeypatch.setattr(module,"quality_measurements",lambda path:{"meanBrightness":.5})
    called=[]
    def judge(path,*,model):
        called.append(path.name)
        return {"proposal":scout.validate_proposal(proposal()),
                "sampledProxyTimeMs":[100,200,300,400,500,600,700],
                "backend":"ollama_loopback_only"}
    monkeypatch.setattr(module,"infer_proxy",judge)
    out=ws/"reports"/"auto-study"
    args=type("Args",(),{"workspace":ws,"pilot":pilot,"output":out,
                         "model":"gemma3:4b","max_items":2,"run":True,
                         "resume":False,"retry_failures":False,"report_every":1})()
    first=module.execute(args)
    assert first["scanned"]==2
    assert len(called)==2
    args.resume=True
    second=module.execute(args)
    assert second["scanned"]==2
    assert len(called)==2
    args.max_items=3
    third=module.execute(args)
    assert third["scanned"]==3
    assert len(called)==3
    assert (out/"review.html").is_file()
    assert (out/"media").is_dir()
    assert (out/"summary.json").is_file()


def test_autoscout_report_api_preserved_by_refactor():
    from adventure_intelligence import auto_scout_review
    assert scout.VERSION == auto_scout_review.VERSION
    assert scout.build_report is auto_scout_review.build_report
    assert scout.review_html is auto_scout_review.review_html
    assert scout.PRIORITIES == auto_scout_review.PRIORITIES


def test_failed_model_schema_has_safe_stage_detail(monkeypatch, tmp_path):
    video = tmp_path / "bad-schema.mp4"
    video.write_bytes(b"x" * 256)
    monkeypatch.setattr(scout.temporal, "_samples",
                        lambda _: (["encoded-image"] * 7, list(range(7))))
    bad = proposal()
    bad["categories"][0]["tag"] = "marine_ray"
    def transport(_):
        return {"message": {"content": json.dumps(bad)}}
    with pytest.raises(scout.ScoutStageError) as caught:
        scout.infer_proxy(video, model="gemma3:4b", transport=transport)
    assert caught.value.stage == "model_schema"
    assert caught.value.detail == "Invalid, duplicated or cross-sport category proposal"
    assert "marine_ray" not in str(caught.value)


def test_failed_sampler_stops_before_ollama(monkeypatch, tmp_path):
    video = tmp_path / "bad.mp4"
    video.write_bytes(b"x" * 256)
    def bad_sampler(_):
        raise ValueError("Bounded H264 preview duration invalid")
    monkeypatch.setattr(scout.temporal, "_samples", bad_sampler)
    with pytest.raises(scout.ScoutStageError) as caught:
        scout.infer_proxy(video, model="gemma3:4b", transport=lambda _: (_ for _ in ()).throw(AssertionError("called")))
    assert caught.value.stage == "frame_sampling"
    assert caught.value.detail == "invalid_preview_duration"


def test_failure_receipt_reports_stage_without_raw_model_response(tmp_path, monkeypatch):
    ws, pilot = fixture_pilot(tmp_path, count=1)
    script = Path(__file__).resolve().parents[1] / "scripts" / "klip_auto_scout_experiment_v2.py"
    spec = importlib.util.spec_from_file_location("auto_scout_cli_failures", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_ollama_preflight", lambda model: None)
    monkeypatch.setattr(module, "quality_measurements", lambda path: {"meanBrightness": 0.5})
    def failed_vision(*args, **kwargs):
        raise scout.ScoutStageError("model_schema", "Invalid category proposal list")
    monkeypatch.setattr(module, "infer_proxy", failed_vision)
    out = ws / "reports" / "auto-failure-study"
    args = type("Args", (), {"workspace": ws, "pilot": pilot, "output": out,
        "model": "gemma3:4b", "max_items": 1, "run": True,
        "resume": False, "retry_failures": False, "report_every": 1})()
    result = module.execute(args)
    assert result["failed"] == 1
    rid = "1" .zfill(24)
    failure = json.loads((out / "receipts" / (rid + ".json")).read_text())
    assert failure["failureStage"] == "model_schema"
    assert failure["failureCode"] == "Invalid category proposal list"
    report = json.loads((out / "summary.json").read_text())
    assert report["failureStages"]["model_schema"] == 1
    assert report["failureCodes"]["Invalid category proposal list"] == 1

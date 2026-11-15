"""Synthetic tests for the isolated model-category disagreement experiment."""
from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path

import pytest

from adventure_intelligence import auto_scout_v2 as scout
from adventure_intelligence import autoscout_disagreement as diag
from adventure_intelligence.moment_edit_v2 import VERSION as MOMENT_VERSION


def valid_model_response():
    return {
        "activity": "ski", "activityFrames": [0, 2],
        "categories": [{
            "tag": "ski_park", "frames": [1, 2],
            "observation": "Terrain feature visible."
        }],
        "qualityFlags": [],
        "eventContinuity": "unresolved",
        "reviewPriority": "investigate",
        "summary": "Snow and a terrain feature.",
        "uncertainty": "No continuous trick sequence visible.",
    }


def test_valid_on_repeat_is_still_not_a_verified_clip():
    d = diag.inspect_response(valid_model_response())
    assert d["status"] == "valid_on_repeat"
    assert d["validCategoryHints"] == ["ski_park"]
    assert d["hintsAuthorizedForReelRanking"] is False
    assert d["modelPredictionsHumanVerified"] is False


def test_cross_sport_is_explicit_not_silently_promoted():
    raw = valid_model_response()
    raw["categories"].append({
        "tag": "marine_ray", "frames": [4],
        "observation": "Possible animal form."
    })
    d = diag.inspect_response(raw)
    assert d["errorClass"] == "cross_sport"
    assert d["crossSportTags"] == ["marine_ray"]
    assert d["validCategoryHints"] == ["ski_park"]
    assert d["status"] == "still_invalid_on_repeat"


def test_duplicate_and_cross_sport_are_distinguished():
    raw = valid_model_response()
    raw["categories"].append(raw["categories"][0].copy())
    raw["categories"].append({
        "tag": "marine_fish", "frames": [4],
        "observation": "Fish shape visible."
    })
    d = diag.inspect_response(raw)
    assert d["duplicateTags"] == ["ski_park"]
    assert d["crossSportTags"] == ["marine_fish"]
    assert d["errorClass"] == "duplicate_and_cross_sport"


def test_duplicate_only_and_unknown_tags():
    raw = valid_model_response()
    raw["categories"].append(raw["categories"][0].copy())
    assert diag.inspect_response(raw)["errorClass"] == "duplicate"
    raw = valid_model_response()
    raw["categories"][0]["tag"] = "nonexistent"
    assert diag.inspect_response(raw)["errorClass"] == "unknown_tag"


def test_repeat_does_not_save_raw_text_or_mutate_original(monkeypatch, tmp_path):
    path = tmp_path/"synthetic-preview.mp4"
    path.write_bytes(b"synthetic-data")
    monkeypatch.setattr(diag.temporal, "_samples",
                        lambda _: (["fake-image"]*7, [0,100,200,300,400,500,600]))
    raw = valid_model_response()
    raw["summary"] = "Sensitive user words in raw response should not be copied."
    captured = []
    def transport(payload):
        captured.append(payload)
        return {"message": {"content": json.dumps(raw)}}
    result = diag.rerun_failed_preview(path, model="gemma3:4b", transport=transport)
    assert captured[0]["model"] == "gemma3:4b"
    assert result["status"] == "valid_on_repeat"
    assert result["originalModelResponseAvailable"] is False
    assert "Sensitive user words" not in json.dumps(result)
    assert path.read_bytes() == b"synthetic-data"


def _workspace(tmp_path):
    ws=tmp_path/"workspace"
    pilot=ws/"reports"/"pilot"
    original=ws/"reports"/"original-scout"
    (pilot/"media").mkdir(parents=True)
    (original/"receipts").mkdir(parents=True)
    model="gemma3:4b"
    rows=[]
    for i in range(1,4):
        rid=f"{i:024x}"
        p=pilot/"media"/(rid+".mp4")
        p.write_bytes((b"not-a-camera-video-"+bytes([i]))*18)
        rows.append({
            "momentId": rid, "sourceAssetSha256":f"{i:064x}",
            "sourceProxySha256":sha256(p.read_bytes()).hexdigest(),
            "sourceStartMs":10000, "sourceEndMs":30000,
            "sportHint":"unknown", "selectionRole":"uniform_timeline_control",
        })
    (pilot/"moment-plan.json").write_text(json.dumps({
        "version":MOMENT_VERSION,
        "manifestSha256":"a"*64,
        "moments":rows,
    }))
    _,plan_sha=scout.load_pilot(ws,pilot)
    policy=scout.fingerprint(model)
    scout.save_json(original/"experiment-state.json",{
        "version":scout.VERSION, "pilot":str(pilot),
        "inputPlanSha256":plan_sha, "model":model,
        "policyFingerprint":policy
    })
    for i,row in enumerate(rows,1):
        rec={
            "version":scout.VERSION,"recordId":row["momentId"],
            "sourceSha256":row["sourceAssetSha256"],
            "proxySha256":row["sourceProxySha256"],
            "inputPlanSha256":plan_sha,
            "policyFingerprint":policy
        }
        if i==1:
            rec["status"]="done"
            rec["analysis"]={"proposal":scout.validate_proposal(valid_model_response())}
        else:
            rec["status"]="failed"
            rec["failureStage"]="model_schema"
            rec["failureCode"]="Invalid, duplicated or cross-sport category proposal"
        scout.save_json(original/"receipts"/(row["momentId"]+".json"),rec)
    return ws,pilot,original


def _cli():
    filename=Path(__file__).resolve().parents[1]/"scripts"/"klip_autoscout_disagreement_probe.py"
    spec=importlib.util.spec_from_file_location("klip_disagreement_test",filename)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_probe_only_retries_failed_and_never_edits_source_receipts(tmp_path, monkeypatch):
    ws,pilot,original=_workspace(tmp_path)
    out=ws/"reports"/"diagnosis"
    original_bytes={
        x.name:x.read_bytes() for x in (original/"receipts").glob("*.json")
    }
    runner=_cli()
    counter=[]
    def model(video, *, model):
        counter.append(video.name)
        return diag.inspect_response(valid_model_response())
    monkeypatch.setattr(diag,"rerun_failed_preview",model)
    args=type("Args",(),{
        "workspace":ws,"pilot":pilot,"experiment":original,
        "output":out,"model":"gemma3:4b","max_items":2,
        "run":False,"resume":False,
    })()
    dry=runner.run(args)
    assert dry["originalSchemaFailures"]==2
    assert not out.exists()
    args.run=True
    result=runner.run(args)
    assert result["originalSuccessesKept"]==1
    assert result["diagnosed"]==2
    assert len(counter)==2
    report=json.loads((out/"summary.json").read_text())
    assert report["promotedToHumanLabels"]==0
    assert report["originalSuccessfulReceiptsPreserved"]==1
    assert (out/"review.html").is_file()
    assert all(x.read_bytes()==original_bytes[x.name]
               for x in (original/"receipts").glob("*.json"))
    args.resume=True
    again=runner.run(args)
    assert again["diagnosed"]==2
    assert len(counter)==2


def test_report_only_claims_new_reevaluation(tmp_path):
    records=[{
        "recordId":"1"*24, "sourceSha256":"a"*64,
        "status":"done", "diagnosis":diag.inspect_response(valid_model_response()),
    }]
    report=diag.summary_report(
        records,failed_population=12,model="gemma3:4b",
        source_plan_sha="b"*64,completed_source_receipts=108)
    assert report["remaining"]==11
    assert report["modelResponseDiffersPossibly"] is True
    assert report["priorCheckpointReceiptsUnmodified"] is True
    assert "cannot prove" in report["interpretation"]

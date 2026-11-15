"""Synthetic blind review and calibration tests. No user footage, GPU or Ollama."""
from __future__ import annotations
import argparse
from hashlib import sha256
import importlib.util
import json
from pathlib import Path

import pytest

from adventure_intelligence import auto_scout_v2 as scout
from adventure_intelligence import calibration_v2 as cal
from adventure_intelligence.calibration_studio_v2 import page
from adventure_intelligence.moment_edit_v2 import VERSION as MOMENT_VERSION


def rows(count=48):
    clips=[]
    for i in range(count):
        sport=("ski","snorkeling","bike","unknown")[i%4]
        tags=(["ski_rail"] if i%8==0 else ["ski_park"]) if sport=="ski" else (
            ["marine_ray"] if i%8==1 else ["marine_reef"]) if sport=="snorkeling" else (
            ["riding_general"] if sport=="bike" else []))
        clips.append({
            "recordId":f"{i+1:024x}","sourceSha256":f"{i+1:064x}",
            "proxySha256":f"{i+100:064x}",
            "sourceStartMs":10000,"sourceEndMs":30000,
            "catalogSportHint":"unknown",
            "modelStatus":"done" if i%11 else "failed",
            "modelActivity":sport if i%11 else "unavailable",
            "modelTags":tags if i%11 else [],"modelQuality":["blur"] if i%11 else [],
            "modelPriority":"investigate" if i%3 else "review_first",
            "stratum":"schema_failed" if i%11==0 else (
                "ski_special" if sport=="ski" and "ski_rail" in tags else
                "ski_other" if sport=="ski" else
                "marine_ray" if sport=="snorkeling" and "marine_ray" in tags else
                "marine_other" if sport=="snorkeling" else
                "bike" if sport=="bike" else "unknown"),
        })
    return clips


def manifest(clips):
    return {"sampleId":cal.sample_id("a"*64,clips), "clips":clips}


def review(clip, *, sport="ski", tags=None, quality=None, decision="skip"):
    return {
        "recordId":clip["recordId"], "sourceSha256":clip["sourceSha256"],
        "activity":sport,"decision":decision,"tags":tags or [],
        "quality":quality or [],"eventStartMs":None,"eventEndMs":None,
    }


def test_deterministic_balanced_sampling():
    selected=cal.select(rows(),count=36)
    assert len(selected)==36
    assert cal.select(rows(),count=36)==selected
    assert len({x["sourceSha256"] for x in selected})==36
    assert any(x["modelStatus"]=="failed" for x in selected)
    assert {"ski_other","marine_other","bike","schema_failed"} <= {x["stratum"] for x in selected}


def test_blind_page_does_not_embed_individual_model_predictions():
    clip=rows(2)
    html=page(manifest(clip))
    assert "modelPriority" not in html
    assert "modelActivity" not in html
    assert "modelQuality" not in html
    assert "connect-src 'none'" in html
    assert "Save reviewed clip" in html
    assert clip[0]["recordId"] in html


def test_explicit_negative_flags_overprediction_and_never_counts_unreviewed():
    c=rows(8)
    # select a ski successful record
    chosen=next(x for x in c if x["modelStatus"]=="done" and x["modelActivity"]=="ski")
    m=manifest(c)
    human={"version":cal.REVIEW_VERSION,"sampleId":m["sampleId"],
           "reviews":[review(chosen,sport="ski",tags=[],quality=[],decision="skip")]}
    result=cal.evaluate(m,human)
    assert result["ownerReviewed"]==1
    assert result["unreviewedNotAssumedNegative"]==7
    assert result["qualityMetrics"]["blur"]["fp"]==1
    assert result["tagMetrics"]["ski_park"]["fp"]==1
    assert result["keepSkipByModelPriority"][0]["humanDecision"]=="skip"


def test_unknown_human_activity_is_abstention_not_false_positive():
    c=rows(4)
    chosen=c[1]
    m=manifest(c)
    r=review(chosen,sport="unknown")
    result=cal.evaluate(m,{"version":cal.REVIEW_VERSION,
                           "sampleId":m["sampleId"],"reviews":[r]})
    assert result["ownerReviewed"]==1
    assert result["excludedFromTagMetrics"]==1
    assert result["tagMetrics"]=={}


def test_bad_source_identity_and_tags_rejected():
    c=rows(3);m=manifest(c)
    r=review(c[1],sport="ski",tags=["marine_ray"])
    with pytest.raises(ValueError,match="tags"):
        cal.validate_review(m,{"version":cal.REVIEW_VERSION,
                               "sampleId":m["sampleId"],"reviews":[r]})
    r=review(c[1]);r["sourceSha256"]="0"*64
    with pytest.raises(ValueError,match="mismatched"):
        cal.validate_review(m,{"version":cal.REVIEW_VERSION,
                               "sampleId":m["sampleId"],"reviews":[r]})


def test_duplicate_and_out_of_bounds_review_rejected():
    c=rows(3);m=manifest(c)
    r=review(c[1])
    with pytest.raises(ValueError,match="Duplicate"):
        cal.validate_review(m,{"version":cal.REVIEW_VERSION,
                               "sampleId":m["sampleId"],"reviews":[r,r]})
    r["eventStartMs"]=0;r["eventEndMs"]=15000
    with pytest.raises(ValueError,match="outside"):
        cal.validate_review(m,{"version":cal.REVIEW_VERSION,
                               "sampleId":m["sampleId"],"reviews":[r]})


def _make_full_fixture(tmp_path,count=12):
    ws=tmp_path/"workspace"
    pilot=ws/"reports"/"pilot"
    scout_run=ws/"reports"/"scout"
    (pilot/"media").mkdir(parents=True)
    (scout_run/"receipts").mkdir(parents=True)
    raw={
        "activity":"ski","activityFrames":[0,1],
        "categories":[{"tag":"ski_park","frames":[0],
                       "observation":"Visible snow terrain feature."}],
        "qualityFlags":[],"eventContinuity":"unresolved",
        "reviewPriority":"investigate",
        "summary":"Snow and mountain terrain.",
        "uncertainty":"No continuous maneuver observable.",
    }
    moments=[]
    for i in range(1,count+1):
        rid=f"{i:024x}";sha=f"{i:064x}"
        video=pilot/"media"/(rid+".mp4")
        video.write_bytes((b"synthetic-movie"+bytes([i]))*21)
        moments.append({
            "momentId":rid,"sourceAssetSha256":sha,
            "sourceProxySha256":sha256(video.read_bytes()).hexdigest(),
            "sourceStartMs":10000,"sourceEndMs":30000,
            "sportHint":"unknown","selectionRole":"uniform_timeline_control",
        })
    (pilot/"moment-plan.json").write_text(json.dumps({
        "version":MOMENT_VERSION,"manifestSha256":"a"*64,"moments":moments,
    }))
    _, plan_sha=scout.load_pilot(ws,pilot)
    ident={"version":scout.VERSION,"pilot":str(pilot),
           "inputPlanSha256":plan_sha,"model":"gemma3:4b",
           "policyFingerprint":scout.fingerprint("gemma3:4b")}
    scout.save_json(scout_run/"experiment-state.json",ident)
    for i,m in enumerate(moments):
        rec={
            "version":scout.VERSION,"recordId":m["momentId"],
            "sourceSha256":m["sourceAssetSha256"],
            "proxySha256":m["sourceProxySha256"],
            "inputPlanSha256":plan_sha,
            "policyFingerprint":ident["policyFingerprint"]
        }
        if i%4==0:
            rec["status"]="failed";rec["failureStage"]="model_schema"
        else:
            rec["status"]="done";rec["analysis"]={
                "proposal":scout.validate_proposal(raw)
            }
        scout.save_json(scout_run/"receipts"/(m["momentId"]+".json"),rec)
    return ws,pilot,scout_run


def _cli():
    script=Path(__file__).resolve().parents[1]/"scripts"/"klip_blind_calibration_v2.py"
    spec=importlib.util.spec_from_file_location("blind_calibration_test",script)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prep_and_evaluate_pure_local_and_originals_unmodified(tmp_path):
    ws,pilot,scout_run=_make_full_fixture(tmp_path)
    out=ws/"reports"/"blind-study"
    original={
        str(p):p.read_bytes()
        for p in (scout_run/"receipts").glob("*.json")
    }
    runner=_cli()
    args=argparse.Namespace(
        workspace=ws,pilot=pilot,experiment=scout_run,
        output=out,model="gemma3:4b",count=12,seed="test-seed",
    )
    result=runner.prepare(args)
    assert result["sampleSize"]==12
    assert result["modelCalls"]==0
    assert (out/"blind-review.html").is_file()
    assert len(list((out/"media").glob("*.mp4")))==12
    assert all(p.read_bytes()==original[str(p)]
               for p in (scout_run/"receipts").glob("*.json"))
    sealed=json.loads((out/"sample-manifest.json").read_text())
    chosen=sealed["clips"][0]
    r=review(chosen,sport="ski",decision="unsure")
    exported=ws/"reports"/"owner-reviews.json"
    exported.write_text(json.dumps({
        "version":cal.REVIEW_VERSION,"sampleId":sealed["sampleId"],"reviews":[r]
    }))
    scored=runner.evaluate(argparse.Namespace(study=out,reviews=exported))
    assert scored["reviewed"]==1
    assert (out/"calibration-metrics.json").is_file()

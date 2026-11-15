"""Synthetic behavioral and end-to-end checks for blinded Auto-Scout calibration.

No actual video, model requests, network access, or camera source reads.
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
from adventure_intelligence import blind_calibration_v2 as blind
from adventure_intelligence.blind_review_html_v2 import render_blind_review
from adventure_intelligence.moment_edit_v2 import VERSION as MOMENT_VERSION


def proposal(activity, tags=None, flags=None):
    return scout.validate_proposal({
        "activity": activity,
        "activityFrames": [0] if activity != "unknown" else [],
        "categories": [
            {"tag": t, "frames": [0], "observation": "Visible scene evidence."}
            for t in (tags or [])
        ],
        "qualityFlags": [
            {"kind": t, "frames": [0], "observation": "Visible image concern."}
            for t in (flags or [])
        ],
        "eventContinuity": "unresolved", "reviewPriority": "investigate",
        "summary": "Synthetic video content for testing.",
        "uncertainty": "Cannot infer unshown action or event.",
    })


def inputs(n=24):
    rows,receipts=[],{}
    for i in range(1,n+1):
        rid=f"{i:024x}"
        src=f"{i:064x}"
        row={"recordId":rid,"sourceSha256":src,
             "proxySha256":f"{i+100:064x}","sourceStartMs":10000,
             "sourceEndMs":30000}
        rows.append(row)
        if i <= 8:
            activity="ski"
            tags=["ski_park","ski_speed"] if i%2==0 else ["ski_park"]
        elif i <= 14:
            activity="snorkeling"
            tags=["marine_ray","marine_scenic"]
        elif i <= 18:
            activity="snorkeling"
            tags=["marine_scenic"]
        elif i <= 21:
            activity="bike"
            tags=["riding_general"]
        else:
            receipts[rid]={"status":"failed"}
            continue
        receipts[rid]={"status":"done","analysis":{
            "proposal":proposal(activity,tags,["blur"] if i%2==0 else [])
        }}
    return rows,receipts


def owner(manifest, *, count=None):
    rows=manifest["items"] if count is None else manifest["items"][:count]
    output=[]
    for r in rows:
        # Intentionally include incorrect labels and negatives to test scoring.
        actual="ski" if r["stratum"].startswith("ski") else (
            "snorkeling" if r["stratum"].startswith("snorkel") else "other")
        if r["stratum"]=="schema_failure":
            actual="ski"
        output.append({
            "recordId":r["recordId"],"sourceSha256":r["sourceSha256"],
            "activity":actual,
            "tags":["ski_park"] if actual=="ski" and int(r["recordId"],16)%2==0 else [],
            "qualityFlags":["blur"] if int(r["recordId"],16)%3==0 else [],
            "preference":"keep" if r["stratum"]=="schema_failure" else "skip",
            "certainty":"certain",
            "eventStartMs":None,"eventEndMs":None,
        })
    return {
        "version":blind.LABEL_VERSION,
        "manifestSha256":blind.manifest_hash(manifest),
        "reviews":output,
    }


def test_stratified_sample_has_controls_and_source_diversity():
    rows,receipts=inputs()
    chosen=blind.sample(rows,receipts,size=18,seed="fixed")
    assert len(chosen)==18
    assert len({r["recordId"] for r in chosen})==18
    assert len({r["sourceSha256"] for r in chosen})==18
    groups={r["stratum"] for r in chosen}
    assert "ski_park" in groups
    assert "snorkel_ray" in groups
    assert "snorkel_other" in groups
    assert "schema_failure" in groups
    assert blind.sample(rows,receipts,size=18,seed="fixed")==chosen


def test_empty_stratum_redistributes_without_overfilling():
    rows,receipts=inputs()
    chosen=blind.sample(rows,receipts,size=24)
    assert len(chosen)==24
    assert len({x["recordId"] for x in chosen})==24
    assert sum(r["stratum"]=="ski_other" for r in chosen)==0


def test_blind_page_has_no_inference_predictions():
    rows,receipts=inputs()
    m=blind.sample_manifest(rows,receipts,"a"*64,"b"*64,size=18)
    html=render_blind_review(m)
    assert "modelPredictionsHiddenInReview" not in html
    assert "reviewPriority" not in html
    assert "ski_park" in html  # taxonomy allowed, but not model predictions.
    assert '"stratum"' not in html
    assert '"sourceSha256"' not in html  # source is used only for identity.
    assert "connect-src 'none'" in html
    assert "Export private JSON labels" in html
    assert m["items"][0]["recordId"] in html


@pytest.mark.skipif(not shutil.which("node"), reason="Node unavailable")
def test_browser_script_syntax(tmp_path):
    rows,receipts=inputs()
    m=blind.sample_manifest(rows,receipts,"a"*64,"b"*64,size=18)
    html=render_blind_review(m)
    import re
    match=re.search(r"<script>(.*?)</script>",html,re.S)
    assert match
    code=tmp_path/"review.js"
    code.write_text(match.group(1))
    run=subprocess.run(["node","--check",str(code)],capture_output=True,text=True,timeout=15)
    assert run.returncode==0,run.stderr


def test_scoring_conservatively_excludes_unreviewed_and_uncertain():
    rows,receipts=inputs()
    m=blind.sample_manifest(rows,receipts,"a"*64,"b"*64,size=18)
    labels=owner(m,count=12)
    labels["reviews"][0]["certainty"]="uncertain"
    scored=blind.score(m,labels,receipts)
    assert scored["selectedCount"]==18
    assert scored["reviewedCount"]==12
    assert scored["unreviewedCount"]==6
    assert scored["uncertainReviewedClipsExcludedFromBinaryMetrics"]<=1
    assert scored["coverageOnlyNotPopulationEstimate"] is True
    assert scored["humanReviewLabelsNotAutomaticallyPromotedToCatalog"] is True
    assert scored["categoryMetrics"]["ski_park"]["n"]<=11


def test_failed_model_clips_are_reviewed_but_not_called_negatives():
    rows,receipts=inputs()
    m=blind.sample_manifest(rows,receipts,"a"*64,"b"*64,size=24)
    labels=owner(m)
    scored=blind.score(m,labels,receipts)
    assert scored["failedModelClipsReviewed"]==3
    assert scored["humanKeepInFailedModelClips"]==3
    assert scored["modelFailuresSelected"]==3
    assert scored["modelSuccessesSelected"]==21
    assert scored["reviewedCount"]==24


@pytest.mark.parametrize("mutator",[
    lambda x:x.update(manifestSha256="f"*64),
    lambda x:x["reviews"][0].update(sourceSha256="f"*64),
    lambda x:x["reviews"][0].update(tags=["marine_ray"]),
    lambda x:x["reviews"][0].update(eventStartMs=0,eventEndMs=1000),
    lambda x:x["reviews"][0].update(eventStartMs=20000,eventEndMs=None),
    lambda x:x["reviews"][0].update(preference="maybe"),
    lambda x:x["reviews"][0].update(certainty="very sure"),
])
def test_reject_wrong_source_or_human_labels(mutator):
    rows,receipts=inputs()
    m=blind.sample_manifest(rows,receipts,"a"*64,"b"*64,size=18)
    labels=owner(m)
    mutator(labels)
    with pytest.raises(ValueError):
        blind.validate_labels(labels,m)


def _private_fixture(tmp_path):
    ws=tmp_path/"workspace"
    pilot=ws/"reports"/"pilot"
    experiment=ws/"reports"/"scout"
    (pilot/"media").mkdir(parents=True)
    (experiment/"receipts").mkdir(parents=True)
    rows=[]
    policy=scout.fingerprint("gemma3:4b")
    for i in range(1,13):
        rid=f"{i:024x}"
        p=pilot/"media"/(rid+".mp4")
        p.write_bytes((b"offline-fake-h264-"+bytes([i]))*25)
        row={"momentId":rid,"sourceAssetSha256":f"{i:064x}",
             "sourceProxySha256":sha256(p.read_bytes()).hexdigest(),
             "sourceStartMs":10000,"sourceEndMs":30000,
             "sportHint":"unknown","selectionRole":"uniform_timeline_control"}
        rows.append(row)
    (pilot/"moment-plan.json").write_text(json.dumps({
        "version":MOMENT_VERSION,"manifestSha256":"a"*64,"moments":rows,
    }))
    _,plan_sha=scout.load_pilot(ws,pilot)
    scout.save_json(experiment/"experiment-state.json",{
        "version":scout.VERSION,"pilot":str(pilot),
        "inputPlanSha256":plan_sha,"model":"gemma3:4b",
        "policyFingerprint":policy,
    })
    for i,r in enumerate(rows,1):
        obj={"version":scout.VERSION,"recordId":r["momentId"],
             "sourceSha256":r["sourceAssetSha256"],
             "proxySha256":r["sourceProxySha256"],
             "inputPlanSha256":plan_sha,"policyFingerprint":policy}
        if i>=11:
            obj["status"]="failed"
            obj["failureStage"]="model_schema"
        else:
            obj["status"]="done"
            obj["analysis"]={"proposal":proposal(
                "ski" if i<=5 else "snorkeling",
                ["ski_park"] if i<=5 else ["marine_ray"],
                ["blur"] if i%2==0 else []
            )}
        scout.save_json(experiment/"receipts"/(r["momentId"]+".json"),obj)
    return ws,pilot,experiment


def _cli():
    file=Path(__file__).resolve().parents[1]/"scripts"/"klip_blind_calibration_v2.py"
    spec=importlib.util.spec_from_file_location("klip_blind_cli",file)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_end_to_end_prepare_score_originals_unmodified(tmp_path):
    ws,pilot,exp=_private_fixture(tmp_path)
    before={p.name:p.read_bytes() for p in (exp/"receipts").glob("*.json")}
    module=_cli()
    out=ws/"reports"/"blind"
    args=type("Args",(),{
        "workspace":ws,"pilot":pilot,"experiment":exp,
        "output":out,"seed":"unit","size":10,"labels":None,
    })()
    report=module.prepare(args)
    assert report["sampled"]==10
    assert (out/"review.html").is_file()
    assert (out/"sample-manifest.json").is_file()
    assert len(list((out/"media").glob("*.mp4")))==10
    rows=blind.sample_manifest(*_dont_run()) if False else json.loads(
        (out/"sample-manifest.json").read_text())
    labels=owner(rows)
    labelpath=ws/"reports"/"owner.json"
    labelpath.write_text(json.dumps(labels))
    args.labels=labelpath
    scored=module.grade(args)
    assert scored["reviewed"]==10
    assert (out/"calibration-score.json").is_file()
    assert all(p.read_bytes()==before[p.name] for p in (exp/"receipts").glob("*.json"))
    with pytest.raises(ValueError,match="already exists"):
        module.grade(args)


def test_no_output_on_incomplete_auto_scout(tmp_path):
    ws,pilot,exp=_private_fixture(tmp_path)
    p=next((exp/"receipts").glob("*.json"))
    p.unlink()
    args=type("Args",(),{
        "workspace":ws,"pilot":pilot,"experiment":exp,
        "output":ws/"reports"/"blocked","seed":"unit","size":8,"labels":None,
    })()
    with pytest.raises(ValueError,match="unattempted"):
        _cli().prepare(args)
    assert not args.output.exists()

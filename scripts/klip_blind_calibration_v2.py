#!/usr/bin/env python3
"""Create a prediction-blind 36-clip QA study from existing Klip V2 results.

No Ollama, no original source video reads or modifications. Explicit review
export is used only for descriptive calibration, not automatically for ranking.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

from adventure_intelligence import auto_scout_v2 as scout
from adventure_intelligence import calibration_v2 as cal
from adventure_intelligence.calibration_studio_v2 import page


def prepare(args) -> dict:
    ws=args.workspace.expanduser().resolve(strict=True)
    pilot=scout._bounded_workspace_path(ws,args.pilot,existing=True)
    experiment=scout._bounded_workspace_path(ws,args.experiment,existing=True)
    output=scout._bounded_workspace_path(ws,args.output,existing=False)
    if (output.exists() or output.is_symlink() or output==pilot
            or output==experiment or output.is_relative_to(pilot)
            or output.is_relative_to(experiment)):
        raise ValueError("Choose a new isolated private calibration directory")
    rows,plan_sha=scout.load_pilot(ws,pilot)
    state=experiment/"experiment-state.json"
    if state.is_symlink() or not state.is_file():
        raise ValueError("No frozen model experiment state")
    ident=json.loads(state.read_text())
    if (ident.get("version")!=scout.VERSION
            or ident.get("pilot")!=str(pilot)
            or ident.get("inputPlanSha256")!=plan_sha
            or ident.get("model")!=args.model
            or ident.get("policyFingerprint")!=scout.fingerprint(args.model)):
        raise ValueError("Model experiment differs from this frozen source packet")
    receipts=scout.read_receipts(
        experiment,rows,plan_sha,ident["policyFingerprint"])
    if len(receipts)!=len(rows):
        raise ValueError("Scout has unattempted sources; finish checkpointed run first")
    selections=cal.select(cal.candidates(rows,receipts),
                          count=args.count,seed=args.seed)
    manifest={
        "version":cal.VERSION,"reviewVersion":cal.REVIEW_VERSION,
        "sampleId":cal.sample_id(plan_sha,selections),
        "sourcePlanSha256":plan_sha,
        "modelPolicyFingerprint":ident["policyFingerprint"],
        "sampleSize":len(selections),"seed":args.seed,
        "strata":dict(Counter(x["stratum"] for x in selections)),
        "clips":selections,
        "modelPredictionsWithheldFromReviewPage":True,
        "doNotUseForAutomaticClipRanking":True,
        "selectionBias":"Stratified, not random representation of entire archive",
    }
    output.mkdir(parents=True,mode=0o700,exist_ok=False)
    for clip in selections:
        matched=next(x for x in rows if x["recordId"]==clip["recordId"])
        scout._safe_preview_link(output,matched)
    scout.save_json(output/"sample-manifest.json",manifest)
    (output/"blind-review.html").write_text(page(manifest),encoding="utf-8")
    return {
        "status":"blind_review_ready",
        "sampleSize":len(selections),
        "strata":manifest["strata"],
        "reviewPage":str(output/"blind-review.html"),
        "sampleManifest":str(output/"sample-manifest.json"),
        "sourceOriginalsRead":0,"modelCalls":0,
        "instructions":"Watch, explicitly save a review for each clip, export JSON. Then run evaluate on that exported JSON.",
    }


def evaluate(args) -> dict:
    folder=args.study.expanduser().resolve(strict=True)
    p=folder/"sample-manifest.json"
    if p.is_symlink() or not p.is_file() or not 50<=p.stat().st_size<=2_000_000:
        raise ValueError("Missing or unsafe calibration manifest")
    manifest=json.loads(p.read_text())
    if manifest.get("version")!=cal.VERSION:
        raise ValueError("Study version mismatch")
    review_file=args.reviews.expanduser().resolve(strict=True)
    if (review_file.is_symlink() or not review_file.is_file()
            or not 20<=review_file.stat().st_size<=2_000_000):
        raise ValueError("Missing or unsafe exported human review file")
    reviews=json.loads(review_file.read_text())
    report=cal.evaluate(manifest,reviews)
    metrics=folder/"calibration-metrics.json"
    if metrics.exists() or metrics.is_symlink():
        raise ValueError("Metrics already exist; use a new study for a new completed review")
    scout.save_json(metrics,report)
    return {
        "status":"review_scored_unbiased_claims_prohibited",
        "reviewed":report["ownerReviewed"],
        "selected":report["selectedClips"],
        "accuracyOnKnown":report["sportAccuracyOnKnownReviewed"],
        "tagMetrics":report["tagMetrics"],
        "qualityMetrics":report["qualityMetrics"],
        "metricsFile":str(metrics),
        "originalVideoRead":False,"mediaChanged":False,
    }


def main() -> int:
    p=argparse.ArgumentParser(description=__doc__)
    commands=p.add_subparsers(dest="command",required=True)
    a=commands.add_parser("prepare")
    a.add_argument("--workspace",required=True,type=Path)
    a.add_argument("--pilot",required=True,type=Path)
    a.add_argument("--experiment",required=True,type=Path)
    a.add_argument("--output",required=True,type=Path)
    a.add_argument("--model",default="gemma3:4b")
    a.add_argument("--count",type=int,default=36)
    a.add_argument("--seed",default="klip-v2-calibration-001")
    b=commands.add_parser("evaluate")
    b.add_argument("--study",required=True,type=Path)
    b.add_argument("--reviews",required=True,type=Path)
    args=p.parse_args()
    try:
        print(json.dumps(prepare(args) if args.command=="prepare" else evaluate(args),
                         sort_keys=True,indent=2,allow_nan=False))
        return 0
    except (ValueError,TypeError,KeyError, OSError, json.JSONDecodeError) as e:
        print("Blind calibration blocked safely: "+str(e),file=sys.stderr)
        return 3


if __name__=="__main__":
    raise SystemExit(main())

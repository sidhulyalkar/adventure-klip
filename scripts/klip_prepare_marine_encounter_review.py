#!/usr/bin/env python3
"""Prepare a blind wildlife-evidence review of ALL existing snorkeling previews.

No media copying or decoding. Playback URLs refer to frozen verified proxy
files in a sibling folder under the SAME private workspace reports directory.
Run a localhost server rooted in reports/ if file:// media is restricted.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

from adventure_intelligence import auto_scout_v2 as scout
from adventure_intelligence import complete_highlight_rerank as ranker
from adventure_intelligence import marine_encounter_review as marine


def execute(args) -> dict:
    ws=args.workspace.expanduser().resolve(strict=True)
    pilot=scout._bounded_workspace_path(ws,args.pilot,existing=True)
    scout_dir=scout._bounded_workspace_path(ws,args.autoscout,existing=True)
    upgrading=bool(getattr(args,"upgrade_review_page",False))
    if upgrading and getattr(args,"run",False):
        raise ValueError("Select either --run or --upgrade-review-page")
    output=scout._bounded_workspace_path(ws,args.output,existing=upgrading)
    if (output.parent!=ws/"reports" or pilot.parent!=ws/"reports"
            or (not upgrading and (output.exists() or output.is_symlink()))
            or output.is_symlink()
            or output==pilot or output==scout_dir):
        raise ValueError("Review target must be a safe sibling reports/ directory")
    rows,plan_sha=scout.load_pilot(ws,pilot)
    state_path=scout_dir/"experiment-state.json"
    if state_path.is_symlink() or not state_path.is_file():
        raise ValueError("Missing frozen scout state")
    state=json.loads(state_path.read_text())
    if (state.get("version")!=scout.VERSION
            or state.get("inputPlanSha256")!=plan_sha
            or state.get("pilot")!=str(pilot)
            or state.get("policyFingerprint")!=scout.fingerprint(state.get("model",""))):
        raise ValueError("Marine review does not match frozen Auto-Scout inputs")
    receipts=scout.read_receipts(scout_dir,rows,plan_sha,state["policyFingerprint"])
    if len(receipts)!=len(rows):
        raise ValueError("Complete frozen scout receipts required")
    digest=sha256(json.dumps(
        {k:receipts[k] for k in sorted(receipts)},
        sort_keys=True,separators=(",",":"),allow_nan=False,
    ).encode()).hexdigest()
    labels=ranker.candidates(rows,receipts)
    sample=marine.manifest(labels,plan_sha,digest,"../"+pilot.name+"/media")
    if not sample["clips"]:
        raise ValueError("No snorkeling previews available in frozen study")
    result={
        "status":"blind_marine_review_ready" if args.run else "blind_marine_review_dry_run",
        "sourceCandidates":len(rows),
        "modelAnalyzed":len(labels),
        "snorkelingPreviews":len(sample["clips"]),
        "sourcePlanSha256":plan_sha,"receiptsSha256":digest,
        "reviewSampleId":sample["sampleId"],
        "modelPredictionsHiddenFromReview":True,
        "originalDJIVideoReads":0,"modelCalls":0,
        "clipMediaDuplicated":False,
        "reviewPage":str(output/"review.html"),
        "previewMediaAreVerifiedSiblingFiles":True,
    }
    if upgrading:
        frozen_path=output/"marine-sample.json"
        if frozen_path.is_symlink() or not frozen_path.is_file():
            raise ValueError("Old review folder has no frozen marine sample")
        previous=json.loads(frozen_path.read_text())
        if previous!=sample:
            raise ValueError("Old marine sample no longer matches frozen preview receipts")
        new_page=output/"review-scene-first.html"
        contents=marine.blind_review_html(sample)
        if new_page.is_symlink():
            raise ValueError("Refusing symlinked review page")
        if new_page.exists():
            if new_page.read_text(encoding="utf-8")!=contents:
                raise ValueError("Scene-first page exists with differing content")
        else:
            new_page.write_text(contents,encoding="utf-8")
        return {
            **result,
            "status":"scene_first_review_page_ready_legacy_page_untouched",
            "reviewPage":str(new_page),
            "sampleUnchanged":True,
            "olderReviewHtmlUnchanged":True,
            "existingJsonExportsCanBeImported":True,
            "previousUnsavedBrowserStateNotRecoverable":True,
        }
    if not args.run:
        return result
    output.mkdir(parents=True,mode=0o700,exist_ok=False)
    scout.save_json(output/"marine-sample.json",sample)
    (output/"review.html").write_text(marine.blind_review_html(sample),encoding="utf-8")
    scout.save_json(output/"status.json",result)
    return result


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace",type=Path,required=True)
    p.add_argument("--pilot",type=Path,required=True)
    p.add_argument("--autoscout",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    p.add_argument("--run",action="store_true",
                   help="Create offline local review page and source-bound manifest")
    p.add_argument("--upgrade-review-page",action="store_true",
                   help="Write review-scene-first.html alongside an existing frozen review without modifying it")
    args=p.parse_args()
    try:
        print(json.dumps(execute(args),indent=2,sort_keys=True,allow_nan=False))
        return 0
    except (ValueError,TypeError,KeyError,OSError,json.JSONDecodeError) as exc:
        print("Marine review prep blocked safely: "+str(exc),file=sys.stderr)
        return 3


if __name__=="__main__":
    raise SystemExit(main())

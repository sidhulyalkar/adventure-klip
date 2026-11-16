#!/usr/bin/env python3
"""Store a source-linked owner-reviewed ski run as a NEW private sidecar record.

This preserves an entire marked park line and ordered features (including
jumps beyond the initial 20s preview) rather than flattening them into a cut.
No original source reads, catalog writes, model inference or public publishing.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

from adventure_intelligence.auto_scout_v2 import _bounded_workspace_path,save_json
from adventure_intelligence.ski_run_context import VERSION, OWNER_VERSION, validate_owner_run


def _read(path:Path, limit:int) -> tuple[dict,bytes]:
    if path.is_symlink() or not path.is_file() or not 2<=path.stat().st_size<=limit:
        raise ValueError("Missing, symlinked or oversized run record input")
    raw=path.read_bytes()
    d=json.loads(raw)
    if not isinstance(d,dict):
        raise ValueError("Expected a private run annotation JSON object")
    return d,raw


def save(args) -> dict:
    ws=args.workspace.expanduser().resolve(strict=True)
    context=_bounded_workspace_path(ws,args.context_dir,existing=True)
    output=_bounded_workspace_path(ws,args.output_dir,existing=False)
    if (output.exists() or output.is_symlink() or
            output==context or output.is_relative_to(context)):
        raise ValueError("Choose a new private report folder for the reviewed run")
    plan,plan_bytes=_read(context/"run-context-plan.json",2_000_000)
    if plan.get("version")!=VERSION or plan.get("previewRender",{}).get("sourceMediaChanged") is not False:
        raise ValueError("Run context has no authenticated source-safe preview")
    video=context/"media"/"run-context.mp4"
    receipt=plan["previewRender"]
    if (video.is_symlink() or not video.is_file()
            or sha256(video.read_bytes()).hexdigest()!=receipt.get("videoSha256")):
        raise ValueError("Run preview missing or does not match frozen SHA receipt")
    owner_path=Path(args.review_json).expanduser()
    doc,owner_bytes=_read(owner_path,200_000)
    validated=validate_owner_run(doc,plan)
    a,b=validated["runStartMs"],validated["runEndMs"]
    features=sorted(validated["featureMarkers"],key=lambda x:(x["sourceMs"],x["kind"]))
    inside=[x for x in features if a is not None and a<=x["sourceMs"]<=b]
    outside=[x for x in features if x not in inside]
    stable=sha256(("|".join([
        plan["sourceAssetSha256"],plan["recordId"],
        str(a),str(b),sha256(owner_bytes).hexdigest(),
    ])).encode()).hexdigest()[:24]
    result={
        "version":"klip-ski-reviewed-run-sidecar-v1",
        "reviewId":stable,
        "sourceAssetSha256":plan["sourceAssetSha256"],
        "originalAnchorRecordId":plan["recordId"],
        "originalAnchorSourceBoundsMs":[plan["anchorSourceStartMs"],plan["anchorSourceEndMs"]],
        "runSourceBoundsMs":[a,b] if a is not None else None,
        "sourceContextBoundsMs":[plan["contextSourceStartMs"],plan["contextSourceEndMs"]],
        "runType":validated["runKind"],
        "runPreference":validated["preference"],
        "reviewerVerifiedContinuousRun":validated["reviewerVerifiedContinuousRun"],
        "sourceTimebase":"approximate_from_reencoded_preview",
        "featureMarkersInsideRun":inside,
        "featureMarkersOutsideRun":outside,
        "featureCountInsideRun":len(inside),
        "positiveRunSelectionAllowed":bool(
            validated["preference"]=="keep" and a is not None
            and validated["reviewerVerifiedContinuousRun"]),
        "proposedFinalMasterRender":False,
        "publicationAuthorized":False,
        "sourceMediaRead":False,
        "contextPlanSha256":sha256(plan_bytes).hexdigest(),
        "ownerAnnotationSha256":sha256(owner_bytes).hexdigest(),
        "privateContextReviewPage":str(context/"run-review.html"),
        "privatePreviewPath":str(video),
        "privatePreviewSha256":receipt["videoSha256"],
        "nextStep":"Calibrate full-run boundaries and feature sequence; do not auto-publish.",
    }
    output.mkdir(parents=True,mode=0o700,exist_ok=False)
    save_json(output/"run-record.json",result)
    return {
        "status":"source_linked_run_saved_no_media_mutation",
        "record":str(output/"run-record.json"),
        "reviewId":stable,
        "positiveRunSelectionAllowed":result["positiveRunSelectionAllowed"],
        "featureCountInsideRun":len(inside),
        "runStartEndMs":result["runSourceBoundsMs"],
        "old20SecondCutNoLongerLimitsRunRecord":True,
        "originalSourceReads":0,"sourceOrCatalogMutations":0,
    }


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workspace",required=True,type=Path)
    p.add_argument("--context-dir",required=True,type=Path)
    p.add_argument("--review-json",required=True,type=Path)
    p.add_argument("--output-dir",required=True,type=Path)
    args=p.parse_args()
    try:
        print(json.dumps(save(args),indent=2,sort_keys=True,allow_nan=False))
        return 0
    except (ValueError,KeyError,TypeError,OSError,json.JSONDecodeError) as exc:
        print("Reviewed-run save blocked safely: "+str(exc),file=sys.stderr)
        return 3


if __name__=="__main__":
    raise SystemExit(main())

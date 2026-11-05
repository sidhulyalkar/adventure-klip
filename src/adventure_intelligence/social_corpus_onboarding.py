"""Non-destructive setup and validation for independently permissioned social references.

Never fabricates creator videos, engagement counts, media licenses, training
labels, or scores. A missing corpus is a data-acquisition prerequisite, not a
model failure. All actions remain local without network calls.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from pathlib import Path
import json

from .archive_pilot import _open_catalog
from .safety import protect_source_output
from .social_action_lens import (
    DATA_VERSION, FEATURE_VERSION, FEATURES, MAX_ITEMS,
    _json, _social_record, make_pairs, split_creators,
)

VERSION="social-lens-corpus-onboarding-v1"


def init_corpus(workspace:Path,output:Path)->dict:
    """Create a genuinely empty JSON corpus at the exact user-requested path."""
    root=Path(workspace).expanduser().resolve()
    path=Path(output).expanduser().resolve()
    if (path==root or not path.is_relative_to(root)
            or path.relative_to(root).parts[0] not in ("reports","exports")
            or path.suffix.lower()!=".json" or path.exists()):
        raise ValueError(
            "Use a NEW .json corpus path under workspace reports/ or exports/")
    with closing(_open_catalog(root)) as db:
        protect_source_output(db,path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("x",encoding="utf-8") as stream:
        json.dump({"version":DATA_VERSION,"items":[]},stream,indent=2,sort_keys=True)
        stream.write("\n")
    return {
        "status":"empty_reference_corpus_initialized",
        "corpus":str(path),"itemCount":0,
        "realCreatorExamples":0,"trainingReady":False,
        "inventedLikesViewsOrLabels":0,"externalUploads":0,
        "nextAction":"Add independently permitted real social-video observations and measured common-rubric features, then run social-lens-preflight",
        "note":"This is a blank local data manifest, NOT training data",
    }


def preflight_corpus(corpus:Path,*,seed:int=23)->dict:
    """Report readiness without training, media reads or model inference."""
    if type(seed) is not int or not 0<=seed<=2**31-1:
        raise ValueError("Invalid seed")
    path=Path(corpus).expanduser().resolve()
    base={
        "version":VERSION,"corpus":str(path),"trainingReady":False,
        "externalRequests":0,"mediaReads":0,"modelCalls":0,
        "sourceVideosModified":False,
    }
    if not path.is_file():
        return {
            **base,"status":"missing_reference_corpus","items":0,
            "nextAction":"Create an empty manifest with 'klip social-lens-corpus-init --output PATH', then acquire real permissioned reference videos; the Klip feature-template is a different dataset",
        }
    if path.stat().st_size>20_000_000:
        return {**base,"status":"oversized_reference_corpus",
                "nextAction":"Split or reduce the corpus to 20 MB and <=5000 records"}
    try:
        document=_json(path)
    except (ValueError,UnicodeError,KeyError,TypeError,json.JSONDecodeError) as exc:
        return {**base,"status":"invalid_json","errorType":type(exc).__name__,
                "nextAction":"Repair the JSON document and rerun preflight"}
    if document.get("version")!=DATA_VERSION or not isinstance(document.get("items"),list):
        other=document.get("version")
        return {
            **base,"status":"wrong_corpus_schema",
            "detectedVersion":other if isinstance(other,str) else None,
            "expectedVersion":DATA_VERSION,
            "nextAction":"Social Lens training requires external creator-licensed examples, not Klip's null-only personal feature-template",
        }
    items=document["items"]
    if len(items)>MAX_ITEMS:
        return {
            **base,"status":"too_many_records","items":len(items),
            "nextAction":"Reduce to <=5000 independently permissioned, fully observed records",
        }
    if not items:
        return {
            **base,"status":"awaiting_permissioned_examples","items":0,
            "validItems":0,"creatorGroups":0,"trainingPairs":0,
            "heldOutPairs":0,"featuresObserved":0,
            "nextAction":"Collect real licensed creator exports and consented raw footage; do not copy your 300 unassessed Klip intervals into this external corpus",
        }
    errors=[]
    valid=[]
    for idx,item in enumerate(items):
        try:
            valid.append(_social_record(item))
        except (ValueError,TypeError,KeyError) as exc:
            if len(errors)<12:
                errors.append({
                    "itemIndex":idx,"errorType":type(exc).__name__,
                    "reason":str(exc)[:240],
                })
    ids=[x["clipId"] for x in valid]
    duplicates=len(ids)-len(set(ids))
    creators={x["creatorGroup"] for x in valid}
    sports=Counter(x["sport"] for x in valid)
    platforms=Counter(x["platform"] for x in valid)
    train_pairs=heldout_pairs=0
    training_creators=heldout_creators=0
    if len(creators)>=3:
        train_ids,test_ids=split_creators(valid,seed)
        train_pairs=len(make_pairs([x for x in valid if x["creatorGroup"] in train_ids]))
        heldout_pairs=len(make_pairs([x for x in valid if x["creatorGroup"] in test_ids]))
        training_creators=len(train_ids)
        heldout_creators=len(test_ids)
    blockers=[]
    if len(items)<6:
        blockers.append("At least six independently permissioned feature-complete examples")
    if errors:
        blockers.append("Every input record must pass rights, features, and metrics validation")
    if duplicates:
        blockers.append("Duplicate clip IDs are forbidden")
    if len(creators)<3:
        blockers.append("At least three creator groups are required")
    if train_pairs<3:
        blockers.append("At least three matched training pairs from training creators are required")
    if heldout_pairs<1:
        blockers.append("At least one matched pair from a held-out creator is required for an evaluable model")
    ready=not blockers
    return {
        **base,"status":"ready_for_exploratory_training" if ready else "incomplete_reference_corpus",
        "items":len(items),"validItems":len(valid),"rejectedItems":len(errors)
            if len(errors)==len(items)-len(valid) else len(items)-len(valid),
        "errors":errors,"duplicateClipIds":duplicates,
        "creatorGroups":len(creators),
        "trainingCreatorGroups":training_creators,
        "heldOutCreatorGroups":heldout_creators,
        "trainingPairs":train_pairs,"heldOutPairs":heldout_pairs,
        "featuresObserved":len(valid)*len(FEATURES),
        "sportCounts":dict(sorted(sports.items())),
        "platformCounts":dict(sorted(platforms.items())),
        "featureVersion":FEATURE_VERSION,
        "trainingReady":ready,
        "blockingRequirements":blockers,
        "rightsProvenanceIndependentlyVerified":False,
        "nextAction":(
            "This is an exploratory matched-pair model only. Independently verify license grants and evaluate the actual creator/video permissions before training"
            if ready else
            "Resolve blocking requirements with real, permissioned and fully observed examples; then rerun social-lens-preflight"),
    }

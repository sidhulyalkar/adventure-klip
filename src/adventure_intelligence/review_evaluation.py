"""Transparent, held-out evaluation of *proposed* scene-state intervals.

The benchmark uses only explicitly reviewed examples. Missing cached scene
evidence counts as abstention, never a correct negative. A hash-grouped split
by recording is provisional and must be replaced with trip/session holdouts
before claiming generalization.
"""
from __future__ import annotations

from collections import defaultdict

from .catalog import connect
from .examples import review_inventory
from .editorial_quality import _scene_frames
from .review_signals import context_intervals

VERSION="scene-review-eval-v1"
SCENE_LABELS={
    "ski_lift":"chairlift_candidate",
    "chairlift":"chairlift_candidate",
    "slow_flat_skiing":"flat_traverse_candidate",
    "flat_traverse":"flat_traverse_candidate",
    "stopped":"stationary_candidate",
    "surface_break":"surface_candidate",
    "surface_transition":"surface_candidate",
    "bike_stopped":"stationary_candidate",
}


def review_quality_report(workspace) -> dict:
    inventory=review_inventory(workspace)
    results=defaultdict(lambda:defaultdict(lambda:{"tp":0,"tn":0,"fp":0,"fn":0,
                                                   "abstained":0,"examples":0}))
    with connect(workspace) as db:
        assets={x["id"]:dict(x) for x in db.execute("SELECT * FROM assets")}
        cache={}
        for ex in inventory["examples"]:
            label=SCENE_LABELS.get(ex["label"])
            if label is None or ex["decision"]=="uncertain":
                continue
            aid=ex["assetId"]
            if aid not in assets:
                continue
            split=ex["reviewSplit"]
            item=results[split][ex["label"]]
            item["examples"]+=1
            if aid not in cache:
                cache[aid]=_scene_frames(db,workspace,aid)
            frames=cache[aid]
            if not frames:
                item["abstained"]+=1
                continue
            intervals=context_intervals(
                frames,assets[aid]["activity"],
                int((assets[aid]["duration"] or 0)*1000))
            matches=[i for i in intervals if i["label"]==label]
            total=ex["endMs"]-ex["startMs"]
            cover=sum(max(0,min(ex["endMs"],i["endMs"])-max(ex["startMs"],i["startMs"]))
                      for i in matches)
            predicted=cover/max(1,total)>=.5
            actual=ex["decision"]=="confirmed"
            item["tp" if predicted and actual else
                 "fp" if predicted and not actual else
                 "fn" if not predicted and actual else "tn"]+=1
    report={}
    for split,labels in results.items():
        report[split]={}
        for label,counts in labels.items():
            tp,fp,fn=counts["tp"],counts["fp"],counts["fn"]
            # Undefined metrics remain null; never render a perfect score when
            # there are no predicted/positive reviewed events.
            report[split][label]={**counts,
                "precision":round(tp/(tp+fp),4) if tp+fp else None,
                "recall":round(tp/(tp+fn),4) if tp+fn else None,
                "coverage":round((counts["examples"]-counts["abstained"])/
                                 counts["examples"],4) if counts["examples"] else 0}
    return {"schemaVersion":1,"algorithm":VERSION,
            "groups":report,"overlapConflicts":inventory["overlapConflicts"],
            "evaluationWarnings":[
                "Benchmarks use human-reviewed labels, not scientific ground truth",
                "No model parameter may be tuned on the test group",
                "Split is grouped by exact recording only, not by trip or filming day",
                "Highly similar duplicate recordings can leak between groups",
                "Missing semantic features are abstentions, not true negatives",
                "No species, skiing biomechanics or crash accuracy is measured",
            ]}

"""Human-in-the-loop curation queue that scales to many recordings.

Review a bounded number of distinct source intervals per page instead of
displaying every overlapping motion episode. No heuristic automatically
rejects footage. Predictions remain labeled as hypotheses; missing features
are unknown.
"""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path
import math

from .catalog import connect
from .editorial_quality import editorial_candidates, _overlap
from .examples import latest_examples
from .scene_atlas import frames_for_asset, clip_feature, similarity

QUEUE_VERSION="curation-queue-v2"
ACTIVITIES={"all","ski","bike","snorkeling","unknown"}
PER_ASSET=3
PAGE_ASSETS=6
MIN_GAP_MS=2500


def _group_candidates(candidates,reviewed,aid,activity):
    seen=[]
    groups=[]
    for c in sorted(candidates,key=lambda x:(-x["score"],x["startMs"])):
        a,b=c["startMs"],c["endMs"]
        # A new candidate may overlap an already reviewed interval; don't keep
        # asking for the same moment just because a second detector fired.
        review_overlap=max(
            (_overlap(a,b,e["start_ms"],e["end_ms"])/max(1,b-a)
             for e in reviewed if e["asset_id"]==aid
             and e["highlight"] in {"keep","skip"}),default=0)
        if review_overlap>=.70:
            continue
        # Suppress neighboring redundant optical-flow episodes with substantial
        # overlap, but retain distant distinct parts of a long recording.
        if any(_overlap(a,b,x["startMs"],x["endMs"])/max(1,min(b-a,x["endMs"]-x["startMs"]))>.45
               or abs(a-x["startMs"])<MIN_GAP_MS for x in seen):
            continue
        scene=c.get("sceneEvidence") or {}
        low=c.get("lowValueContext") or {}
        flags=c.get("incidentReviewFlags") or []
        underwater=c.get("underwaterContinuity") or {}
        reason=[]
        if flags:reason.append("Possible camera disturbance; inspect, not a verified crash")
        if scene.get("likelyNegativeContext") or low.get("supportedFraction",0)>.4:
            lane="likely_filler"
            reason.append("Unverified non-action scene hypothesis")
        elif scene.get("likelyPositiveContext") or underwater.get("supportedFraction",0)>.75:
            lane="promising"
            reason.append("Consistent candidate action or underwater context")
        elif c["kind"] in {"wildlife_encounter","jump_chain","carving_flow","tree_weaving",
                           "technical_feature_chain"}:
            lane="promising"
            reason.append("Activity event proposal requiring review")
        else:
            lane="needs_review"
            reason.append("Camera motion alone cannot determine highlight value")
        if flags and lane!="likely_filler":lane="needs_review"
        rejected_labels=[e["label"] for e in reviewed
                         if e["asset_id"]==aid and e["decision"]=="rejected"
                         and _overlap(a,b,e["start_ms"],e["end_ms"])/max(1,b-a)>=.70]
        if rejected_labels:
            lane="needs_review"
            reason.append("Earlier scene label rejected; choose the correct action or scene")
        if activity=="snorkeling" and c.get("acousticEvidence",{}).get("surfaceCoincidentTransients"):
            reason.append("Audio coincides with possible surfacing")
        groups.append({"assetId":aid,"startMs":a,"endMs":b,
                       "label":c["kind"],"activity":activity,
                       "editorialScore":c["score"],"lane":lane,
                       "reasons":reason,"reviewState":"unreviewed",
                       "evidence": {"scene":scene,"motion":c.get("motionEvidence"),
                                    "audio":c.get("acousticEvidence"),
                                    "lowValue":low,"incidentFlags":flags},
                       "candidateId":f"{c['episodeId']}:{a}:{b}"})
        seen.append(c)
        if len(groups)>=PER_ASSET:
            break
    return groups


def _annotate_visual_stacks(db,workspace,moments):
    """Group only compatible cached embeddings; never discard visual neighbors.

    Pairwise complete-link similarity deliberately avoids transitive chaining.
    This is an editorial navigation hint, not a model of action equivalence.
    """
    cache={}
    stacks=[]
    for moment in moments:
        aid=moment["assetId"]
        if aid not in cache:
            cache[aid]=frames_for_asset(db,workspace,aid,provider="siglip2")
        vec=clip_feature(*cache[aid],moment["startMs"],moment["endMs"])
        group=None
        if vec.get("available"):
            for idx, members in enumerate(stacks):
                if not members:
                    continue
                if all(old["key"]==vec["modelKey"] and
                       similarity(old["vector"],vec["vector"]) is not None and
                       similarity(old["vector"],vec["vector"])>=.94
                       for old in members):
                    group=idx
                    break
        if group is None:
            group=len(stacks)
            stacks.append([])
        stacks[group].append({"key":vec.get("modelKey"),"vector":vec.get("vector")})
        moment["visualStack"]="stack-"+str(group+1)
        moment["visualComparisonAvailable"]=bool(vec.get("available"))
    counts={idx+1:len(items) for idx,items in enumerate(stacks)}
    for m in moments:
        group=int(m["visualStack"].split("-")[-1])
        m["similarSceneCount"]=counts[group]-1 if m["visualComparisonAvailable"] else 0
        m["visualSimilarityStatus"]="uncalibrated" if m["visualComparisonAvailable"] else "unknown"
    return moments


def review_queue(workspace:Path,*,activity="all",page=0) -> dict:
    if activity not in ACTIVITIES:
        raise ValueError("Unknown activity filter")
    if not isinstance(page,int) or isinstance(page,bool) or page<0 or page>100000:
        raise ValueError("Queue page must be a nonnegative integer")
    with connect(workspace) as db:
        cond="WHERE activity=?" if activity!="all" else ""
        args=(activity,) if activity!="all" else ()
        assets=[dict(x) for x in db.execute(
            "SELECT id,activity,duration,stored_path FROM assets "+cond+
            " ORDER BY created_at,id",args)]
        reviewed=latest_examples(db)
        total_assets=len(assets)
        batch=assets[page*PAGE_ASSETS:(page+1)*PAGE_ASSETS]
        moments=[]
        for a in batch:
            rows=[dict(r) for r in db.execute(
                "SELECT * FROM episodes WHERE asset_id=? AND kind!='recording_summary' "
                "ORDER BY score DESC,start_ms LIMIT 24",(a["id"],))]
            if not rows:continue
            candidates,_=editorial_candidates(db,workspace,rows,policy="suggest")
            groups=_group_candidates(candidates,reviewed,a["id"],a["activity"])
            for g in groups:
                g["sourceName"]=Path(a["stored_path"]).name
            moments.extend(groups)
        # Use only previously cached semantic vectors. No decoding or model
        # downloads occur inside the Inbox.
        _annotate_visual_stacks(db,workspace,moments)
    # Review uncertain moments first, with diversity across recordings.
    lane_priority={"promising":0,"needs_review":1,"likely_filler":2}
    moments.sort(key=lambda x:(lane_priority[x["lane"]],
                               -x["editorialScore"],x["assetId"],x["startMs"]))
    # Keep first representative near the front of each lane. Later same-looking
    # clips are not hidden: the reviewer can still compare distinct moments.
    visited=set()
    for x in moments:
        stack=x["visualStack"]
        x["visualRepresentative"]=stack not in visited
        visited.add(stack)
    moments.sort(key=lambda x:(lane_priority[x["lane"]],
                               not x["visualRepresentative"],
                               -x["editorialScore"],x["assetId"],x["startMs"]))
    counts={lane:sum(x["lane"]==lane for x in moments)
            for lane in lane_priority}
    return {"schemaVersion":1,"version":QUEUE_VERSION,
            "activity":activity,"page":page,"pageSizeAssets":PAGE_ASSETS,
            "totalAssets":total_assets,
            "totalPages":math.ceil(total_assets/PAGE_ASSETS),
            "hasNext":(page+1)*PAGE_ASSETS<total_assets,
            "moments":moments,"counts":counts,
            "note":"Queue lanes are editorial hypotheses. Nothing is automatically rejected; reviewed moments are suppressed when substantially overlapping."}

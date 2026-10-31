"""Source-verified visual similarity, exemplar matching and diversity.

All vectors come from checksum-verified completed semantic artifacts. We never
compare different models, versions or image-preprocessing configurations, and
cosine distance is not event identity, novelty certainty or a species label.
"""
from __future__ import annotations
import hashlib
import json
import math

from .semantic_jobs import completed_document
from .examples import latest_examples
from .audio_triage import audio_window
from .acoustics import AUDIO_VERSION
from .artifacts import read_artifact

VERSION = "scene-atlas-v1"


def unit_vector(vector) -> list[float] | None:
    if not isinstance(vector,list) or not 1 <= len(vector) <= 8192:
        return None
    if not all(isinstance(x,(int,float)) and not isinstance(x,bool)
               and math.isfinite(x) for x in vector):
        return None
    norm = math.sqrt(sum(float(x)**2 for x in vector))
    if norm<=1e-12:
        return None
    return [float(x)/norm for x in vector]


def similarity(a: list[float], b: list[float]) -> float | None:
    if len(a)!=len(b) or not a:
        return None
    return max(-1,min(1,sum(x*y for x,y in zip(a,b))))


def frames_for_asset(db, workspace, asset_id: str,
                     provider: str = "siglip2") -> tuple[list[dict], str | None]:
    active=db.execute(
        """SELECT j.* FROM semantic_jobs j JOIN semantic_active a
           ON j.asset_id=a.asset_id AND j.config_id=a.config_id
           WHERE a.asset_id=? AND a.provider=? AND j.status='completed'""",
        (asset_id,provider)).fetchone()
    if active is None:
        return [],None
    doc=completed_document(workspace,active)
    if doc is None or doc.get("provider")!=provider:
        return [],None
    config=doc.get("config",{})
    provider_config=config.get("providerConfig",{})
    # Sampling density is intentionally excluded; the actual resolved model
    # revision / preprocessing / normalization are required to match.
    model_key=json.dumps({
        "provider":provider,
        "modelVersion":doc.get("modelVersion"),
        "resolvedRevision":provider_config.get("resolvedRevision"),
        "imageProcessor":provider_config.get("imageProcessor"),
        "dimensions": next((len(f["embedding"]) for f in doc.get("frames",[])
                            if isinstance(f.get("embedding"),list)),None),
    },sort_keys=True,separators=(",",":"),default=str)
    return doc.get("frames",[]),hashlib.sha256(model_key.encode()).hexdigest()[:20]


def clip_feature(frames: list[dict], model_key: str | None,
                 start_ms: int, end_ms: int) -> dict:
    if end_ms <= start_ms:
        raise ValueError("Invalid clip boundaries")
    if model_key is None:
        return {"available":False,"status":"unknown","reason":"semantic_model_unavailable"}
    vectors=[]
    seen_times=set()
    for f in frames:
        t=f.get("decodedPtsMs")
        if not isinstance(t,int) or t<0:
            t=f.get("tMs")
        if not isinstance(t,int) or t in seen_times or not start_ms<=t<end_ms:
            continue
        v=unit_vector(f.get("embedding"))
        if v is not None:
            vectors.append(v)
            seen_times.add(t)
    if not vectors:
        return {"available":False,"status":"unknown","reason":"no_embedding_in_interval",
                "modelKey":model_key}
    dims={len(v) for v in vectors}
    if len(dims)!=1:
        return {"available":False,"status":"unknown","reason":"mixed_embedding_dimensions",
                "modelKey":model_key}
    mean=[sum(x[i] for x in vectors)/len(vectors) for i in range(len(vectors[0]))]
    pooled=unit_vector(mean)
    if pooled is None:
        return {"available":False,"status":"unknown","reason":"degenerate_pooled_embedding",
                "modelKey":model_key}
    return {"available":True,"modelKey":model_key,"vector":pooled,
            "sampleCount":len(vectors),"method":"mean_normalized_image_embeddings",
            "status":"uncalibrated_similarity"}


def _overlap(a,b,c,d):
    return max(0,min(b,d)-max(a,c))


def _latest_review_features(db,workspace,provider,examples,cache):
    result=[]
    for ex in examples:
        if ex["decision"]!="confirmed" or ex["highlight"] not in {"keep","skip"}:
            continue
        aid=ex["asset_id"]
        if aid not in cache:
            cache[aid]=frames_for_asset(db,workspace,aid,provider)
        features=clip_feature(*cache[aid],ex["start_ms"],ex["end_ms"])
        if features["available"]:
            result.append((ex,features))
    return result


def enrich_candidates(db, workspace, candidates: list[dict], *,
                      provider: str = "siglip2") -> tuple[list[dict],dict]:
    """Attach audio, object proposals, visual embeddings and review evidence.

    The vectors are transient in this call; the returned report is serialized
    without the large raw vectors unless explicitly requested internally.
    """
    examples=latest_examples(db)
    cache={}
    reviews=_latest_review_features(db,workspace,provider,examples,cache)
    enriched=[]
    by_asset={}
    for c in candidates:
        aid=c["sourceAssetId"]
        if aid not in cache:
            cache[aid]=frames_for_asset(db,workspace,aid,provider)
        frames,model_key=cache[aid]
        features=clip_feature(frames,model_key,c["startMs"],c["endMs"])
        # Distinct detected labels, not a certified species or object count.
        object_labels=sorted({str(obj.get("label")) for f in frames
                              if c["startMs"]<=f.get("tMs",-1)<c["endMs"]
                              for obj in f.get("objects",[])
                              if isinstance(obj,dict) and obj.get("label")})
        if aid not in by_asset:
            doc=read_artifact(db,workspace,aid,AUDIO_VERSION)
            by_asset[aid]=doc["series"] if doc else None
        sound=audio_window(by_asset[aid],c["startMs"],c["endMs"])
        nearest=[]
        if features["available"]:
            for ex,vec in reviews:
                if vec["modelKey"]!=features["modelKey"] or ex["asset_id"]==aid:
                    continue
                sim=similarity(features["vector"],vec["vector"])
                if sim is not None:
                    nearest.append({"exampleId":ex["example_id"],
                                    "label":ex["label"],"highlight":ex["highlight"],
                                    "similarity":round(sim,5),"assetId":ex["asset_id"]})
        nearest.sort(key=lambda x:-x["similarity"])
        annotated={**c,
            "audioTriage":sound,
            "visualEvidence":{
                "available":features["available"],
                "status":features.get("status","unknown"),
                "modelKey":features.get("modelKey"),
                "sampleCount":features.get("sampleCount",0),
                "method":features.get("method"),
                "objectProposals":object_labels,
                "warning":"Cosine similarity is not scene identity or label certainty",
            },
            "nearestReviewedExamples":nearest[:3],
            "_vector":features.get("vector"),
            "_modelKey":features.get("modelKey"),
        }
        # Cross-source reviewed exemplars require diversity before influencing
        # score. One approved example MUST NOT dominate an archive-wide rank.
        compatible=[x for x in nearest if x["similarity"] >= .84]
        independent_assets={x["assetId"] for x in compatible}
        if len(compatible)>=3 and len(independent_assets)>=2:
            positive=max((x["similarity"] for x in compatible
                          if x["highlight"]=="keep"),default=0)
            negative=max((x["similarity"] for x in compatible
                          if x["highlight"]=="skip"),default=0)
            if positive>negative+.03:
                annotated["score"]=min(1,round(c["score"]+.06,4))
                annotated.setdefault("reasonCodes",[]).append("reviewed_example_similarity_bonus")
            elif negative>positive+.03:
                annotated["score"]=round(c["score"]*.89,4)
                annotated.setdefault("reasonCodes",[]).append("reviewed_example_similarity_caution")
        enriched.append(annotated)
    return enriched, {
        "version":VERSION,"provider":provider,
        "reviewedExamples":len(examples),"featureMatchedExamples":len(reviews),
        "note":"Audio is triage only; visual matches are uncalibrated and cross-model matches prohibited",
    }


def diverse_representatives(candidates: list[dict], *, max_seconds: int,
                            max_per_asset: int=3, similarity_threshold: float=.88
                            ) -> tuple[list[dict],dict]:
    """Choose high-scoring nonoverlapping clips with a bounded redundancy cost.

    No union-find chaining: a cluster member must match every cluster member.
    Missing features never cause a clip to be called a duplicate.
    """
    if max_seconds<=0 or not .5<=similarity_threshold<=1:
        raise ValueError("Invalid selection configuration")
    clusters=[]
    for candidate in sorted(candidates,key=lambda x:(-x["score"],x["sourceAssetId"],x["startMs"])):
        key=candidate.get("_modelKey")
        vec=candidate.get("_vector")
        group=None
        if key and vec:
            for i,members in enumerate(clusters):
                if all(other.get("_modelKey")==key and other.get("_vector")
                       and similarity(vec,other["_vector"])>=similarity_threshold
                       for other in members):
                    group=i
                    break
        if group is None:
            group=len(clusters)
            clusters.append([])
        clusters[group].append(candidate)
        candidate["visualCluster"]=f"cluster-{group+1:04d}"
    picked=[]
    per_asset={}
    occupied=0
    remaining=list(candidates)
    while remaining:
        possible=[]
        for c in remaining:
            aid=c["sourceAssetId"]
            if occupied+c["duration"]>max_seconds or per_asset.get(aid,0)>=max_per_asset:
                continue
            if any(p["sourceAssetId"]==aid and
                   _overlap(c["startMs"],c["endMs"],p["startMs"],p["endMs"])>0
                   for p in picked):
                continue
            related=[similarity(c["_vector"],p["_vector"]) for p in picked
                     if c.get("_modelKey") and c.get("_modelKey")==p.get("_modelKey")
                     and c.get("_vector") and p.get("_vector")]
            repeat=max([x for x in related if x is not None],default=0)
            # Promote visual diversity only when a vector exists and we have
            # evidence of cross-shot repetition.
            adjusted=c["score"]-max(0,repeat-.55)*.28
            possible.append((adjusted,c))
        if not possible:
            break
        _,best=max(possible,key=lambda t:(t[0],t[1]["score"],
                                          t[1]["sourceAssetId"],-t[1]["startMs"]))
        picked.append(best)
        per_asset[best["sourceAssetId"]]=per_asset.get(best["sourceAssetId"],0)+1
        occupied+=best["duration"]
        remaining.remove(best)
    def public(c):
        return {k:v for k,v in c.items() if not k.startswith("_")}
    return [public(p) for p in picked], {
        "version":VERSION,"clusters":len(clusters),
        "visualRepresentatives":len({c["visualCluster"] for c in picked}),
        "similarityThreshold":similarity_threshold,
        "metric":"uncalibrated_cosine_similarity",
        "selectedDurationSeconds":round(occupied,3),
    }

"""Blind, source-backed Klip V2 review sampling and conditional calibration."""
from __future__ import annotations
from collections import Counter
from hashlib import sha256

from .moment_edit_v2 import TAGS, DEFECTS

VERSION = "klip-v2-blind-calibration-v1"
REVIEW_VERSION = "klip-v2-human-calibration-review-v1"
SPORTS = {"ski", "snorkeling", "bike", "unknown", "other"}
QUOTAS = [
    ("ski_special", 4), ("ski_other", 8), ("marine_ray", 6),
    ("marine_other", 6), ("bike", 4), ("unknown", 2), ("schema_failed", 6),
]


def candidates(rows: list[dict], receipts: dict) -> list[dict]:
    out = []
    for row in rows:
        r = receipts.get(row["recordId"])
        if r is None:
            raise ValueError("Incomplete checkpoint coverage")
        if r["status"] == "done":
            p = r["analysis"]["proposal"]
            activity = p["activity"]
            tags = [x["tag"] for x in p["categories"]]
            flags = [x["kind"] for x in p["qualityFlags"]]
            priority = p["reviewPriority"]
            if activity == "ski":
                bucket = "ski_special" if set(tags) & {"ski_jump", "ski_box", "ski_rail"} else "ski_other"
            elif activity == "snorkeling":
                bucket = "marine_ray" if "marine_ray" in tags else "marine_other"
            elif activity == "bike":
                bucket = "bike"
            else:
                bucket = "unknown"
        elif r["status"] == "failed":
            activity, tags, flags, priority, bucket = (
                "unavailable", [], [], "unavailable", "schema_failed")
        else:
            raise ValueError("Unexpected receipt status")
        out.append({
            "recordId": row["recordId"], "sourceSha256": row["sourceSha256"],
            "proxySha256": row["proxySha256"],
            "sourceStartMs": row["sourceStartMs"],
            "sourceEndMs": row["sourceEndMs"], "catalogSportHint": row["sportHint"],
            "modelStatus": r["status"], "modelActivity": activity,
            "modelTags": sorted(tags), "modelQuality": sorted(flags),
            "modelPriority": priority, "stratum": bucket,
        })
    return out


def select(rows: list[dict], count: int = 36, seed: str = "klip-v2-calibration-001") -> list[dict]:
    if type(count) is not int or not 12 <= count <= min(120, len(rows)):
        raise ValueError("Calibration count is 12..120 and must fit available rows")
    if len({x["recordId"] for x in rows}) != len(rows):
        raise ValueError("Repeated candidate ID")
    if not isinstance(seed,str) or not 1<=len(seed)<=100:
        raise ValueError("Invalid deterministic seed")
    order = lambda r: sha256(f"{seed}:{r['recordId']}".encode()).hexdigest()
    buckets = {k: sorted((r for r in rows if r["stratum"] == k), key=order)
               for k,_ in QUOTAS}
    targets = {k: int(count*n/36) for k,n in QUOTAS}
    missing = count-sum(targets.values())
    for k,n in sorted(QUOTAS,key=lambda kv: (-(count*kv[1]/36-targets[kv[0]]), kv[0]))[:missing]:
        targets[k] += 1
    chosen, chosen_ids, by_source = [], set(), Counter()
    def take(pool, n):
        available = [r for r in pool if r["recordId"] not in chosen_ids]
        while n>0 and available and len(chosen)<count:
            best=min(available,key=lambda r:(by_source[r["sourceSha256"]],order(r)))
            chosen.append(best);chosen_ids.add(best["recordId"])
            by_source[best["sourceSha256"]]+=1;available.remove(best);n-=1
    for k,_ in QUOTAS:
        take(buckets[k], targets[k])
    take(rows,count-len(chosen))
    if len(chosen)!=count:
        raise ValueError("Could not fill calibration sample")
    return sorted(chosen,key=lambda r:sha256(f"{seed}:blind:{r['recordId']}".encode()).hexdigest())


def sample_id(plan_sha: str, clips: list[dict]) -> str:
    return sha256((plan_sha+"|"+",".join(sorted(r["recordId"] for r in clips))).encode()).hexdigest()


def validate_review(manifest: dict, review: dict) -> list[dict]:
    if (not isinstance(review,dict) or review.get("version")!=REVIEW_VERSION
            or review.get("sampleId")!=manifest["sampleId"]):
        raise ValueError("Review not associated with this frozen blind sample")
    supplied=review.get("reviews")
    if not isinstance(supplied,list) or len(supplied)>len(manifest["clips"]):
        raise ValueError("Bad number of human reviews")
    by_id={r["recordId"]:r for r in manifest["clips"]}
    seen=set()
    for r in supplied:
        if not isinstance(r,dict) or set(r)!={"recordId","sourceSha256","activity",
                         "decision","tags","quality","eventStartMs","eventEndMs"}:
            raise ValueError("Malformed human review")
        rid=r["recordId"]
        if rid in seen or rid not in by_id or r["sourceSha256"]!=by_id[rid]["sourceSha256"]:
            raise ValueError("Duplicate or mismatched review source")
        seen.add(rid)
        if r["activity"] not in SPORTS or r["decision"] not in {"keep","skip","unsure"}:
            raise ValueError("Invalid human review decision")
        tags,quality=r["tags"],r["quality"]
        if (not isinstance(tags,list) or any(type(t) is not str for t in tags)
                or len(tags)!=len(set(tags))
                or not all(t in TAGS.get(r["activity"],()) for t in tags)):
            raise ValueError("Invalid human sport tags")
        if (not isinstance(quality,list) or any(type(t) is not str for t in quality)
                or len(quality)!=len(set(quality))
                or not all(t in DEFECTS for t in quality)):
            raise ValueError("Invalid human quality tags")
        a,b=r["eventStartMs"],r["eventEndMs"]
        if (a is None)!=(b is None):
            raise ValueError("Event bounds must be a pair")
        clip=by_id[rid]
        if a is not None and (type(a) is not int or type(b) is not int
                             or not clip["sourceStartMs"]<=a<b<=clip["sourceEndMs"]):
            raise ValueError("Event bounds outside the source interval")
    return supplied


def _measure(tp,fp,fn):
    return {"tp":tp,"fp":fp,"fn":fn,
            "precision":round(tp/(tp+fp),3) if tp+fp else None,
            "recall":round(tp/(tp+fn),3) if tp+fn else None}


def evaluate(manifest: dict, review: dict) -> dict:
    labeled=validate_review(manifest,review)
    by_id={r["recordId"]:r for r in manifest["clips"]}
    matches=0; known=0; conf=Counter(); priorities=Counter()
    tag_tp=Counter();tag_fp=Counter();tag_fn=Counter()
    q_tp=Counter();q_fp=Counter();q_fn=Counter()
    excluded=0
    model_failed_reviewed=0
    human_keep_on_failed=0
    human_unsure=0
    for r in labeled:
        c=by_id[r["recordId"]]
        priorities[(c["modelPriority"],r["decision"])]+=1
        if r["decision"]=="unsure":
            human_unsure+=1
        if c["modelStatus"]!="done":
            model_failed_reviewed+=1
            if r["decision"]=="keep":
                human_keep_on_failed+=1
            excluded+=1
            continue
        # 'other' is a known negative class; only unknown is an abstention.
        if r["activity"]=="unknown":
            excluded+=1
            continue
        actual,pred=r["activity"],c["modelActivity"]
        conf[(actual,pred)]+=1; known+=1;matches+=int(actual==pred)
        for x in set(c["modelTags"])|set(r["tags"]):
            if x in c["modelTags"] and x in r["tags"]:tag_tp[x]+=1
            elif x in c["modelTags"]:tag_fp[x]+=1
            else:tag_fn[x]+=1
        for x in set(c["modelQuality"])|set(r["quality"]):
            if x in c["modelQuality"] and x in r["quality"]:q_tp[x]+=1
            elif x in c["modelQuality"]:q_fp[x]+=1
            else:q_fn[x]+=1
    return {
        "version":VERSION,"sampleId":manifest["sampleId"],
        "selectedClips":len(manifest["clips"]), "ownerReviewed":len(labeled),
        "unreviewedNotAssumedNegative":len(manifest["clips"])-len(labeled),
        "excludedFromTagMetrics":excluded,
        "reviewedModelFailureCount":model_failed_reviewed,
        "humanKeepAmongModelFailures":human_keep_on_failed,
        "humanUnsureDecisions":human_unsure,
        "successfulModelReviewsWithKnownSport":known,
        "sportAccuracyOnKnownReviewed":round(matches/known,3) if known else None,
        "sportConfusion":[{"human":a,"model":b,"count":n} for (a,b),n in sorted(conf.items())],
        "tagMetrics":{k:_measure(tag_tp[k],tag_fp[k],tag_fn[k])
                      for k in sorted(set(tag_tp)|set(tag_fp)|set(tag_fn))},
        "qualityMetrics":{k:_measure(q_tp[k],q_fp[k],q_fn[k])
                          for k in sorted(set(q_tp)|set(q_fp)|set(q_fn))},
        "keepSkipByModelPriority":[{"priority":p,"humanDecision":v,"count":n}
                                   for (p,v),n in sorted(priorities.items())],
        "interpretation":"Descriptive results on a stratified, intentionally biased review sample; NOT archive-wide accuracy, independent-outing performance, or automatic clip approval.",
        "noCameraOriginalRead":True,"noModelInference":True,
    }

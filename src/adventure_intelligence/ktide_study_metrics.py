"""K-TIDE held-out retrieval metrics: human-graded, trip-macro, paired baseline.

No video, model inference, training, data uploads or automatic ground truth.
A grade=3 is a user's independently adjudicated relevance, not evidence of
animal species, successful sports actions, or objective causation.
"""
from __future__ import annotations

from collections import defaultdict
from math import log2, sqrt
import random

from .ktide_study_contract import validate_deck, validate_embeddings


METHODS = ("ktideEmbedding", "meanTeacherEmbedding")
METRICS = tuple(
    f"{kind}{k}" for kind in ("ndcg", "recall", "precision") for k in (1,3,5,10)
) + ("mrr", "pairwiseAgreement")
CONTROLS = ("untrainedEmbedding", "temporalChangeEmbedding")


def _cosine(a: list, b: list) -> float:
    if len(a) != len(b):
        raise ValueError("Incompatible embedding dimensions within method")
    dot = sum(float(x)*float(y) for x,y in zip(a,b))
    na = sqrt(sum(float(x)*float(x) for x in a))
    nb = sqrt(sum(float(y)*float(y) for y in b))
    if na < 1e-12 or nb < 1e-12:
        raise ValueError("Degenerate embedding")
    return max(-1.0, min(1.0, dot/(na*nb)))


def _query_metrics(options: list[tuple[str,int,float]]) -> dict:
    """Calculate retrieval utility across a *fully judged* candidate bank."""
    ordered = sorted(options, key=lambda r: (-r[2], r[0]))
    gains = [2**r[1]-1 for r in ordered]
    ideal = sorted((2**r[1]-1 for r in options), reverse=True)
    grades = [r[1] for r in ordered]
    npositive = sum(r[1]>=2 for r in options)
    if npositive == 0:
        raise ValueError("Query without human-adjudicated relevant candidates")
    result = {}
    for k in (1,3,5,10):
        count = min(k,len(options))
        dcg = sum(gains[i]/log2(i+2) for i in range(count))
        optimum = sum(ideal[i]/log2(i+2) for i in range(count))
        result[f"ndcg{k}"] = dcg/optimum if optimum else 0.0
        result[f"recall{k}"] = sum(g>=2 for g in grades[:count])/npositive
        result[f"precision{k}"] = sum(g>=2 for g in grades[:count])/count
    result["mrr"] = next(1.0/(i+1) for i,g in enumerate(grades) if g>=2)
    correct = equal = total = 0
    for i, left in enumerate(options):
        for right in options[i+1:]:
            if left[1] == right[1]:
                continue
            total += 1
            signed = (left[1] - right[1]) * (left[2] - right[2])
            if abs(left[2] - right[2]) < 1e-10:
                equal += 1
            elif signed > 0:
                correct += 1
    result["pairwiseAgreement"] = ((correct + equal*.5)/total if total else None)
    result["judgedCandidates"] = len(options)
    result["relevantCandidates"] = npositive
    return result


def _group_metric(rows: list[dict], key: str, method: str):
    grouped = defaultdict(list)
    for row in rows:
        value = row["metrics"][method][key]
        if value is not None:
            grouped[row["tripGroup"]].append(value)
    if not grouped:
        return None
    return sum(sum(v)/len(v) for v in grouped.values())/len(grouped)


def _bootstrap_deltas(rows: list[dict], *, seed: int, resamples: int = 1000) -> dict:
    """Paired query-trip cluster bootstrap. Descriptive, never a magic p-value."""
    groups = sorted({r["tripGroup"] for r in rows})
    if len(groups) < 5:
        return {
            "status":"insufficient_independent_test_trips",
            "groups":len(groups),
            "minimumForExploratoryInterval":5,
            "confidenceIntervals":None,
        }
    by_group = {g:[r for r in rows if r["tripGroup"]==g] for g in groups}
    rng = random.Random(seed)
    samples = {metric:[] for metric in METRICS}
    for _ in range(resamples):
        chosen = [rng.choice(groups) for _ in groups]
        for metric in METRICS:
            vals = []
            for group in chosen:
                subset = by_group[group]
                a = [r["metrics"]["ktideEmbedding"][metric] for r in subset]
                b = [r["metrics"]["meanTeacherEmbedding"][metric] for r in subset]
                pairs = [(x,y) for x,y in zip(a,b) if x is not None and y is not None]
                if pairs:
                    vals.append(sum(x-y for x,y in pairs)/len(pairs))
            if vals:
                samples[metric].append(sum(vals)/len(vals))
    intervals = {}
    for metric, vals in samples.items():
        vals.sort()
        if not vals:
            intervals[metric] = None
        else:
            intervals[metric] = {
                "low":round(vals[int((len(vals)-1)*.025)],6),
                "high":round(vals[int((len(vals)-1)*.975)],6),
            }
    return {
        "status":"exploratory_group_bootstrap_not_significance",
        "groups":len(groups), "seed":seed,"resamples":resamples,
        "confidenceIntervals":intervals,
    }


def evaluate_retrieval(deck: dict, manifest: dict, manifest_sha: str,
                       training: dict, projection: dict, *,
                       bootstrap_seed: int=23) -> dict:
    """Compare learned K-TIDE to frozen mean under precisely the same pairs."""
    if type(bootstrap_seed) is not int or not 0<=bootstrap_seed<2**32:
        raise ValueError("Invalid bootstrap seed")
    info = validate_deck(deck, manifest, manifest_sha, training,
                         require_complete=True)
    vectors = validate_embeddings(projection, manifest, training)
    samples = {r["sampleId"]:r for r in manifest["samples"]}
    outcomes = []
    eligible_query_ids = set()
    for query in deck["queries"]:
        grades = [c["relevance"] for c in query["candidates"]]
        if not (any(g >= 2 for g in grades) and any(g <= 1 for g in grades)):
            continue
        qid = query["querySampleId"]
        eligible_query_ids.add(qid)
        group = samples[qid]["groupId"]
        results = {}
        for method in METHODS:
            comparisons = [
                (row["sampleId"], row["relevance"],
                 _cosine(vectors[qid][method], vectors[row["sampleId"]][method]))
                for row in query["candidates"]
            ]
            results[method] = _query_metrics(comparisons)
        outcomes.append({
            "queryId": qid,
            "tripGroup":group,
            "subsport":samples[qid]["subsport"],
            "task":query["task"], "metrics":results,
        })
    if not outcomes:
        raise ValueError("No mixed-relevance queries; preserve no-match cases but collect a discriminative candidate set for ranking")
    totals={}
    for method in METHODS:
        totals[method]={}
        for metric in METRICS:
            value=_group_metric(outcomes,metric,method)
            totals[method][metric]=round(value,6) if value is not None else None
        totals[method]["queries"]=len(outcomes)
        totals[method]["testTripGroups"]=len({r["tripGroup"] for r in outcomes})
    delta={
        key: (round(totals["ktideEmbedding"][key]-totals["meanTeacherEmbedding"][key],6)
              if all(totals[m][key] is not None for m in METHODS) else None)
        for key in METRICS
    }
    control_summary = {}
    for control in CONTROLS:
        matched = []
        for query in deck["queries"]:
            if query["querySampleId"] not in eligible_query_ids:
                continue
            ids = [query["querySampleId"]] + [c["sampleId"] for c in query["candidates"]]
            if any(vectors[sid].get(control) is None for sid in ids):
                continue
            group = samples[query["querySampleId"]]["groupId"]
            options = [
                (row["sampleId"], row["relevance"],
                 _cosine(vectors[query["querySampleId"]][control],
                         vectors[row["sampleId"]][control]))
                for row in query["candidates"]
            ]
            matched.append({
                "tripGroup": group,
                "metrics": {
                    control: _query_metrics(options),
                    "ktideEmbedding": next(r["metrics"]["ktideEmbedding"]
                                           for r in outcomes
                                           if r["tripGroup"] == group
                                           and r["queryId"] == query["querySampleId"]),
                },
            })
        scored = len(matched)
        control_summary[control] = {
            "fullySupportedQueries": scored,
            "totalQueries": len(outcomes),
            "coverageFraction": round(scored/len(outcomes), 6),
            "comparisonIsSameQuerySubset": True,
            "ndcg5Control": (round(_group_metric(matched,"ndcg5",control),6)
                             if scored else None),
            "ndcg5KTIDEOnMatched": (round(_group_metric(matched,"ndcg5","ktideEmbedding"),6)
                                    if scored else None),
        }
    stratified={}
    for activity in sorted({r["subsport"] for r in outcomes}):
        subset=[r for r in outcomes if r["subsport"]==activity]
        stratified[activity]={
            "queries":len(subset),
            "testTripGroups":len({r["tripGroup"] for r in subset}),
            "ndcg5ByMethod":{
                m: (round(_group_metric(subset,"ndcg5",m),6)) for m in METHODS
            },
            "lowSupport":len({r["tripGroup"] for r in subset})<3,
        }
    return {
        "version":"klip-ktide-heldout-evaluation-v1",
        "protocol":"cross_trip_test_only",
        "datasetManifestSha256":manifest_sha,
        "checkpointSha256":training["checkpointSha256"],
        "blindHumanJudgmentsRequired":True,
        "onlyHeldoutTripGroupsEvaluated":True,
        "datasetGroupsUsedForTrainingOrTuning":False,
        "judgmentCoverage":info,
        "rankingQueries":len(outcomes),
        "fullyJudgedQueriesNotUsedForRanking":len(deck["queries"])-len(outcomes),
        "noMatchQueriesRetainedForFutureAbstention":info["queryAnswerability"].get("no_match",0),
        "macroByTrip":totals,
        "deltaKTIDEMinusFrozenMean":delta,
        "pairedGroupBootstrap":_bootstrap_deltas(outcomes,seed=bootstrap_seed),
        "perSubsport":stratified,
        "additionalControls":control_summary,
        "rawVideoRead":False,"modelInferencePerformed":False,
        "humanGroundTruthGeneratedByModel":False,
        "animalSpeciesOrActionVerified":False,
        "interpretation":"descriptive heldout retrieval not causal or statistical proof of improvement",
    }

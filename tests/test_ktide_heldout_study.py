"""Synthetic-only cross-trip blind-study qualification tests."""
from copy import deepcopy
from hashlib import sha256
import json
import math

import pytest

from adventure_intelligence.encoder_dataset import VERSION as DATASET_VERSION, group_splits
from adventure_intelligence.ktide_study_contract import (
    build_blind_deck, validate_training_lineage, validate_deck,
    validate_embeddings,
)
from adventure_intelligence.ktide_study_metrics import (
    _query_metrics, evaluate_retrieval,
)


def study_data(*, group_count=20, samples_per_group=6):
    rows = []
    for g in range(group_count):
        for k in range(samples_per_group):
            n = g * samples_per_group + k
            rows.append({
                "sampleId": f"synthetic-{g:03d}-{k:03d}",
                "sourceSha256": f"{g+1:064x}",
                "groupId": f"synthetic-trip-{g:03d}",
                "goal": "sport_action",
                "subsport": "skiing_trees" if k % 2 else "skiing_terrain_park",
                "startMs": k * 15000, "endMs": (k+1) * 15000,
                "shard": f"shards/{n+1:024x}.npz",
                "shardSha256": sha256(f"shard-{n}".encode()).hexdigest(),
                "sampleCount": 8,
            })
    manifest = {
        "version": DATASET_VERSION, "featureModelKey": "synthetic-teacher-v1",
        "embeddingDim": 16, "samples": rows,
    }
    frozen_bytes = json.dumps(manifest, sort_keys=True).encode()
    digest = sha256(frozen_bytes).hexdigest()
    seed = 17
    training = {
        "version": "klip-ktide-training-run-v1",
        "datasetManifestSha256": digest,
        "teacherModelKey": manifest["featureModelKey"],
        "modelVersion": "ktide-gap-aware-temporal-v1",
        "checkpointSha256": "e" * 64,
        "seed": seed, "testNotUsedForSelection": True,
        "featureCacheOnly": True, "rawMediaRead": False,
        "humanPreferenceRankingQualified": False,
        "splitAssignments": group_splits(manifest, seed=seed),
    }
    return manifest, digest, training


def fake_embeddings(manifest, training, *, include_controls=True):
    items = []
    for i,row in enumerate(manifest["samples"]):
        theta = (i % 13) * .19
        vector = [math.cos(theta), math.sin(theta)] + [0.0] * 14
        # Distinct synthetic views so every method is testable.
        ctrl = [math.cos(theta + .5), math.sin(theta + .5)] + [0.0] * 14
        item = {
            "sampleId": row["sampleId"],
            "sourceSha256": row["sourceSha256"],
            "groupId": row["groupId"],
            "ktideEmbedding": vector,
            "meanTeacherEmbedding": ctrl,
        }
        if include_controls:
            item["untrainedEmbedding"] = ctrl
            item["temporalChangeEmbedding"] = (None if i % 7 == 0 else vector)
        items.append(item)
    return {
        "version": "klip-ktide-embedding-report-v1",
        "modelVersion": training["modelVersion"],
        "checkpointSha256": training["checkpointSha256"],
        "featureModelKey": manifest["featureModelKey"],
        "mediaRead": False, "eventClaimsVerified": False,
        "numSamples": len(items), "results": items,
    }


def judge(deck):
    out = deepcopy(deck)
    for q in out["queries"]:
        for i,c in enumerate(q["candidates"]):
            c["adjudication"] = "confirmed"
            c["relevance"] = 3 if i == 0 else 0
            c["evidenceIds"] = [f"synthetic-human-adjudication-{i}"]
    return out


def test_query_deck_is_deterministic_blind_and_cross_trip():
    manifest, digest, training = study_data()
    a = build_blind_deck(manifest, digest, training,
                         max_queries=12,candidates_per_query=5,seed=49)
    b = build_blind_deck(manifest, digest, training,
                         max_queries=12,candidates_per_query=5,seed=49)
    assert a == b
    assert not a["scoresExposedDuringAnnotation"]
    assert not a["labelsGeneratedByModel"]
    index = {r["sampleId"]:r for r in manifest["samples"]}
    splits = training["splitAssignments"]
    for q in a["queries"]:
        query = index[q["querySampleId"]]
        assert splits[query["groupId"]] == "test"
        for c in q["candidates"]:
            opponent = index[c["sampleId"]]
            assert opponent["groupId"] != query["groupId"]
            assert splits[opponent["groupId"]] == "test"
            assert c["adjudication"] == "unreviewed"
            assert c["relevance"] is None


def test_unreviewed_is_unknown_not_negative_and_cannot_be_evaluated():
    manifest, digest, training = study_data()
    deck = build_blind_deck(manifest, digest, training,
                            max_queries=8,candidates_per_query=5)
    status = validate_deck(deck, manifest, digest, training, require_complete=False)
    assert status["candidateJudgments"]["unreviewed"] > 0
    assert not status["metricsEligible"]
    with pytest.raises(ValueError, match="Incomplete query"):
        evaluate_retrieval(deck,manifest,digest,training,
                           fake_embeddings(manifest,training))


def test_graded_labels_and_trip_macro_retrieval_metrics():
    manifest,digest,training=study_data()
    deck=judge(build_blind_deck(manifest,digest,training,
                                max_queries=12,candidates_per_query=5))
    result=evaluate_retrieval(deck,manifest,digest,training,
                              fake_embeddings(manifest,training))
    assert result["judgmentCoverage"]["fullyAdjudicated"]
    assert result["judgmentCoverage"]["metricsEligible"]
    assert result["macroByTrip"]["ktideEmbedding"]["testTripGroups"] >= 2
    assert result["macroByTrip"]["ktideEmbedding"]["ndcg5"] <= 1.
    assert result["macroByTrip"]["meanTeacherEmbedding"]["recall10"] == 1.
    assert result["additionalControls"]["untrainedEmbedding"]["fullySupportedQueries"] == 12
    assert result["additionalControls"]["temporalChangeEmbedding"]["fullySupportedQueries"] <= 12
    assert not result["rawVideoRead"]
    assert not result["animalSpeciesOrActionVerified"]


def test_retrieval_metric_perfect_order_and_one_relevant_item():
    value=_query_metrics([
        ("candidate-a",3,.98),("candidate-b",1,.4),("candidate-c",0,-.3)
    ])
    assert value["ndcg1"] == 1.0
    assert value["ndcg5"] == 1.0
    assert value["recall1"] == 1.0
    assert value["mrr"] == 1.0
    assert value["pairwiseAgreement"] == 1.0


def test_tampered_train_or_val_sample_injection_rejected():
    manifest,digest,training=study_data()
    deck=build_blind_deck(manifest,digest,training,
                          max_queries=8,candidates_per_query=5)
    other=next(r["sampleId"] for r in manifest["samples"]
               if training["splitAssignments"][r["groupId"]] == "train")
    deck["queries"][0]["candidates"][0]["sampleId"]=other
    with pytest.raises(ValueError,match="crosses split"):
        validate_deck(deck,manifest,digest,training,require_complete=False)


def test_mismatched_frozen_training_lineage_fails_closed():
    manifest,digest,training=study_data()
    wrong=deepcopy(training)
    wrong["splitAssignments"][next(iter(wrong["splitAssignments"]))]="train"
    with pytest.raises(ValueError,match="group split mismatch"):
        validate_training_lineage(wrong,manifest,digest)
    wrong=deepcopy(training)
    wrong["testNotUsedForSelection"]=False
    with pytest.raises(ValueError,match="Unqualified"):
        validate_training_lineage(wrong,manifest,digest)


@pytest.mark.parametrize("change", [
    lambda p: p["results"][0].update({"groupId":"wrong-trip"}),
    lambda p: p["results"][0].update({"meanTeacherEmbedding":[0.0]*16}),
    lambda p: p["results"][0].update({"ktideEmbedding":[float("nan")]+[0.0]*15}),
    lambda p: p["results"].append(deepcopy(p["results"][0])),
])
def test_tampered_vector_projection_rejected(change):
    manifest,digest,training=study_data()
    projection=fake_embeddings(manifest,training)
    change(projection)
    with pytest.raises(ValueError):
        validate_embeddings(projection,manifest,training)


def test_not_enough_test_trips_reports_no_bootstrap_confidence():
    manifest,digest,training=study_data(group_count=14)
    deck=judge(build_blind_deck(manifest,digest,training,
                                max_queries=8,candidates_per_query=5))
    report=evaluate_retrieval(deck,manifest,digest,training,
                              fake_embeddings(manifest,training))
    assert report["pairedGroupBootstrap"]["status"]=="insufficient_independent_test_trips"
    assert report["pairedGroupBootstrap"]["confidenceIntervals"] is None


def test_cluster_bootstrap_requires_at_least_five_heldout_query_groups():
    manifest,digest,training=study_data(group_count=42)
    deck=judge(build_blind_deck(manifest,digest,training,
                                max_queries=36,candidates_per_query=5))
    report=evaluate_retrieval(deck,manifest,digest,training,
                              fake_embeddings(manifest,training),
                              bootstrap_seed=33)
    assert report["pairedGroupBootstrap"]["status"].startswith("exploratory")
    assert report["pairedGroupBootstrap"]["groups"] >= 5
    assert report["pairedGroupBootstrap"]["confidenceIntervals"]["ndcg5"] is not None


def test_uncertain_and_missing_judgment_evidence_not_assumed_negative():
    manifest,digest,training=study_data()
    deck=judge(build_blind_deck(manifest,digest,training,
                                max_queries=8,candidates_per_query=4))
    row=deck["queries"][0]["candidates"][0]
    row["adjudication"]="uncertain"
    row["relevance"]=None
    row["evidenceIds"]=[]
    assert not validate_deck(deck,manifest,digest,training,require_complete=False)["fullyAdjudicated"]
    with pytest.raises(ValueError,match="Incomplete query"):
        validate_deck(deck,manifest,digest,training,require_complete=True)



def test_fully_reviewed_no_match_query_preserved_but_not_fake_positive():
    manifest,digest,training=study_data()
    deck=judge(build_blind_deck(manifest,digest,training,
                                max_queries=12,candidates_per_query=5))
    for option in deck["queries"][0]["candidates"]:
        option["relevance"]=0
    status=validate_deck(deck,manifest,digest,training,require_complete=True)
    assert status["fullyAdjudicated"]
    assert status["queryAnswerability"]["no_match"]==1
    report=evaluate_retrieval(deck,manifest,digest,training,
                              fake_embeddings(manifest,training))
    assert report["noMatchQueriesRetainedForFutureAbstention"]==1
    assert report["rankingQueries"]==11
    assert report["fullyJudgedQueriesNotUsedForRanking"]==1


def test_every_query_no_match_cannot_claim_retrieval_benchmark():
    manifest,digest,training=study_data()
    deck=judge(build_blind_deck(manifest,digest,training,
                                max_queries=8,candidates_per_query=5))
    for query in deck["queries"]:
        for option in query["candidates"]:
            option["relevance"]=0
    assert not validate_deck(deck,manifest,digest,training,
                             require_complete=True)["metricsEligible"]
    with pytest.raises(ValueError,match="No mixed-relevance queries"):
        evaluate_retrieval(deck,manifest,digest,training,
                           fake_embeddings(manifest,training))


def test_fully_reviewed_all_positive_query_is_reported_not_a_free_win():
    manifest,digest,training=study_data()
    deck=judge(build_blind_deck(manifest,digest,training,
                                max_queries=8,candidates_per_query=5))
    for option in deck["queries"][0]["candidates"]:
        option["relevance"]=3
    report=evaluate_retrieval(deck,manifest,digest,training,
                              fake_embeddings(manifest,training))
    assert report["judgmentCoverage"]["queryAnswerability"]["all_positive"]==1
    assert report["rankingQueries"]==7

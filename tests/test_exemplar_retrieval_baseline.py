"""Synthetic tests for the frozen, cross-source exemplar retrieval baseline."""
from pathlib import Path

import pytest

from adventure_intelligence.exemplar_retrieval_baseline import compare_to_examples


def candidate(cid, source, vector, model="siglip2-model-1"):
    return {"recordId": cid, "sourceSha256": source, "startMs": 0,
            "endMs": 20000, "vector": vector, "modelKey": model,
            "sampleCount": 4}


def example(source, vector, label="ski_park", status="keep", model="siglip2-model-1"):
    return {"sourceSha256": source, "vector": vector, "modelKey": model,
            "label": label, "highlight": status, "decision": "confirmed"}


def test_positive_examples_rank_cross_source_shots():
    items = [candidate("c1", "source-a", [1.0,0,0,0,0,0,0,0]),
             candidate("c2", "source-b", [0.8,0.6,0,0,0,0,0,0]),
             candidate("c3", "source-c", [0,1.0,0,0,0,0,0,0])]
    examples = [example("example-1", [1.0,0,0,0,0,0,0,0]),
                example("example-2", [0,1.0,0,0,0,0,0,0],status="skip")]
    doc = compare_to_examples(items, examples)
    picked = doc["rankings"]["ski_park"]
    assert [r["recordId"] for r in picked] == ["c1","c2","c3"]
    assert picked[0]["positiveMinusNegative"] == 1.0
    assert doc["benchmarkAccuracyMeasured"] if "benchmarkAccuracyMeasured" in doc else True
    assert picked[0]["uncalibrated"] is True


def test_excludes_same_source_and_does_not_claim_leakage_free_outings():
    e = example("source-a", [1.0,0,0,0,0,0,0,0])
    c = candidate("c1","source-a",[1.0,0,0,0,0,0,0,0])
    doc = compare_to_examples([c],[e])
    assert doc["rankings"]["ski_park"] == []
    assert doc["sameSourceReferencesExcluded"] == 1
    assert any("outings" in x for x in doc["limitations"])


def test_mixed_model_and_missing_features_are_abstentions():
    c = [candidate("a","source-2",[1.0,0,0,0,0,0,0,0], model="different"),
         candidate("b","source-3",None)]
    doc = compare_to_examples(c,[example("source-1",[1.0,0,0,0,0,0,0,0])])
    assert doc["differentSourcePositiveMatchRows"] == 0
    assert doc["candidatesWithoutVector"] == 1


def test_unreviewed_examples_cannot_be_prototypes():
    e = example("s1",[1.0,0,0,0,0,0,0,0])
    e["decision"] = "uncertain"
    doc = compare_to_examples([candidate("c","s2",[1.0,0,0,0,0,0,0,0])],[e])
    assert doc["humanConfirmedExampleCount"] == 0
    assert doc["rankings"] == {}


def test_source_diverse_top_k():
    c = [
        candidate("a","source-1",[1.0,0,0,0,0,0,0,0]),
        candidate("b","source-1",[0.99,0.141067,0,0,0,0,0,0]),
        candidate("c","source-2",[0.95,0.312249,0,0,0,0,0,0]),
    ]
    doc = compare_to_examples(c,[example("example",[1.0,0,0,0,0,0,0,0])],top_k=2)
    assert [x["recordId"] for x in doc["rankings"]["ski_park"]] == ["a","c"]
    assert doc == compare_to_examples(c,[example("example",[1.0,0,0,0,0,0,0,0])],top_k=2)


def test_invalid_top_k_rejected():
    with pytest.raises(ValueError):
        compare_to_examples([], [], top_k=0)

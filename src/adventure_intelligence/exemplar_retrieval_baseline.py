"""Read-only exemplar matching against verified existing SigLIP2 clip features.

A discovery baseline, not a supervised benchmark or calibrated classifier.
Reuses scene_atlas cosine/clip pooling and frozen semantic artifact verification.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math

from .scene_atlas import similarity

VERSION = "klip-exemplar-retrieval-readonly-v1"


def _valid_feature(row: dict) -> bool:
    v = row.get("vector")
    return (
        isinstance(row.get("sourceSha256"), str) and
        isinstance(row.get("modelKey"), str) and
        bool(row["modelKey"]) and
        isinstance(v, list) and 8 <= len(v) <= 8192 and
        all(type(x) in (int, float) and math.isfinite(x) for x in v)
    )


def compare_to_examples(candidates: list[dict], examples: list[dict],
                        *, top_k: int = 10) -> dict:
    """Compare a candidate to confirmed, different-source human examples.

    Exemplar labels need not belong to the current sport taxonomy.
    A score exists only when a different-source positive and matching
    feature model are present. Negative examples are advisory, never
    automatic suppression of a potentially important candidate.
    """
    if type(top_k) is not int or not 1 <= top_k <= 100:
        raise ValueError("top_k must be 1..100")
    validated = []
    for ex in examples:
        if ex.get("decision") != "confirmed" or ex.get("highlight") not in ("keep", "skip"):
            continue
        if not _valid_feature(ex):
            continue
        label = ex.get("label")
        if not isinstance(label, str) or not 1 <= len(label) <= 80:
            raise ValueError("Invalid reviewed-example label")
        validated.append(ex)
    labels = sorted({x["label"] for x in validated})
    groups = defaultdict(list)
    for ex in validated:
        groups[ex["label"]].append(ex)
    matched = []
    without_features = 0
    no_pos = 0
    source_leak_excluded = 0
    for c in candidates:
        if not _valid_feature(c):
            without_features += 1
            continue
        for label in labels:
            refs = groups[label]
            compatible = [
                e for e in refs
                if e["modelKey"] == c["modelKey"] and
                len(e["vector"]) == len(c["vector"]) and
                e["sourceSha256"] != c["sourceSha256"]
            ]
            excluded = sum(
                e["modelKey"] == c["modelKey"] and
                e["sourceSha256"] == c["sourceSha256"]
                for e in refs
            )
            source_leak_excluded += excluded
            pos = [similarity(c["vector"], e["vector"])
                   for e in compatible if e["highlight"] == "keep"]
            neg = [similarity(c["vector"], e["vector"])
                   for e in compatible if e["highlight"] == "skip"]
            pos = [v for v in pos if v is not None]
            neg = [v for v in neg if v is not None]
            if not pos:
                no_pos += 1
                continue
            match = max(pos)
            nearest_negative = max(neg) if neg else None
            matched.append({
                "recordId": c["recordId"],
                "sourceSha256": c["sourceSha256"],
                "startMs": c["startMs"], "endMs": c["endMs"],
                "label": label,
                "modelKey": c["modelKey"],
                "positiveSimilarity": round(match, 7),
                "negativeSimilarity": round(nearest_negative, 7)
                if nearest_negative is not None else None,
                "positiveMinusNegative": round(match - nearest_negative, 7)
                if nearest_negative is not None else None,
                "numberPositiveReferences": len(pos),
                "numberNegativeReferences": len(neg),
                "candidateFrameCount": c.get("sampleCount", 0),
                "humanReviewedLabel": False,
                "uncalibrated": True,
            })
    rankings = {}
    for label in labels:
        group = [r for r in matched if r["label"] == label]
        group.sort(key=lambda x: (-x["positiveSimilarity"],
                                  x["sourceSha256"], x["startMs"],
                                  x["recordId"]))
        # Greedy source balancing prevents near-identical neighboring shots
        # from filling every place in the top-k; no outing independence claim.
        selected, used = [], Counter()
        while group and len(selected) < top_k:
            best = min(group, key=lambda x: (
                used[x["sourceSha256"]],
                -x["positiveSimilarity"], x["sourceSha256"],
                x["startMs"], x["recordId"],
            ))
            selected.append(best)
            used[best["sourceSha256"]] += 1
            group.remove(best)
        rankings[label] = selected
    return {
        "version": VERSION,
        "method": "frozen_siglip2_cross_source_nearest_positive",
        "candidateCount": len(candidates),
        "candidatesWithCompatibleVector": sum(_valid_feature(x) for x in candidates),
        "candidatesWithoutVector": without_features,
        "humanConfirmedExampleCount": len(validated),
        "positiveExampleCount": sum(e["highlight"] == "keep" for e in validated),
        "negativeExampleCount": sum(e["highlight"] == "skip" for e in validated),
        "referenceLabelCount": len(labels),
        "differentSourcePositiveMatchRows": len(matched),
        "noCompatiblePositivePairs": no_pos,
        "sameSourceReferencesExcluded": source_leak_excluded,
        "rankings": rankings,
        "limitations": [
            "Same-source matches excluded but independent outings are not verified.",
            "Mean-pooled still-image vectors do not prove temporal actions, species or successful tricks.",
            "Cosine similarity is not probability of a correct highlight or human preference.",
            "Negative exemplars are diagnostics; unobserved clips are not automatically rejected.",
            "Catalog and verified frozen artifacts are used without opening source camera video.",
        ],
    }

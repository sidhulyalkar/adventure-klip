# Read-only human-exemplar retrieval control

Status: **experimental, training-free baseline in `main` after this PR merges**. Extracted without the dependent Auto-Scout UI from [PR #118](https://github.com/sidhulyalkar/adventure-klip/pull/118).

This is not a learned temporal action model or a preference predictor. It is a baseline to ask: **do frozen image features plus a handful of explicitly human-reviewed examples retrieve promising clips at all?** If K-TIDE fails to beat it on held-out human judgments, a larger encoder is not justified yet.

## Data contract

Call `adventure_intelligence.exemplar_retrieval_baseline.compare_to_examples(candidates, examples, top_k=10)` with in-memory Python lists. No file paths, video, database, model weights or network are accessed.

Candidates need a stable `recordId`, `sourceSha256`, `startMs`, `endMs`, `modelKey`, and frozen feature `vector` (8–8192 finite numbers). Reviewed examples need `sourceSha256`, `modelKey`, `vector`, `decision="confirmed"`, `highlight="keep"|"skip"`, and `label`. Source identifiers and feature receipts should come from an independently verified source catalog. This pure function does not itself verify the underlying asset SHA or embedding file.

Use **one exact frozen model/configuration** for compatible vectors. An arbitrary equal-length embedding from a different checkpoint must never be assumed comparable. `modelKey` equality is an input contract, not independent proof.

## Example

```python
from adventure_intelligence.exemplar_retrieval_baseline import compare_to_examples

refs = [{
    "sourceSha256": "source-verified-by-caller",
    "modelKey": "frozen-model-revision",
    "vector": [1.0] + [0.0] * 7,
    "decision": "confirmed",
    "highlight": "keep",
    "label": "ski_park",
}]
candidates = [{
    "recordId": "new-candidate",
    "sourceSha256": "different-source-verified-by-caller",
    "startMs": 12000,
    "endMs": 22000,
    "modelKey": "frozen-model-revision",
    "vector": [1.0] + [0.0] * 7,
    "sampleCount": 4,
}]
result = compare_to_examples(candidates, refs)
print(result["rankings"]["ski_park"][0]["recordId"])
```

These identifiers are synthetic illustrations. For real work, use full verified asset hashes and exact model/provenance keys. The returned score is **cosine similarity**, not a probability of an event, a verified animal or a predicted human Keep.

## Scientific limitations

- Same-source exemplars are excluded to limit trivial overlap; **same-outing or near-duplicate content across sources is not detected**. For a true held-out study, enforce trip-based split boundaries outside this library.
- Only confirmed human examples contribute. Skips remain advisory; they do not suppress unreviewed footage.
- A still-image embedding cannot distinguish a completed jump from a stationary shot of the same terrain. K-TIDE/K-MOMENT temporal models should be measured against this cheap baseline rather than assumed superior.
- Do not tune baseline thresholds on held-out test clips. Use independently judged queries, random-source negatives, no-match controls and reported coverage.
- The code intentionally does **not** import the experimental Auto-Scout UI, run video analysis, or modify review labels.

## Next qualification

Use the same frozen feature source, candidate pool and held-out outings for: mean-pooled frozen features, this nearest-reviewed-exemplar baseline, absolute and signed temporal changes, untrained K-TIDE, trained K-TIDE, and any new dense temporal encoder. Evaluate task-specific **event-phase** relevance separately from visual situation similarity and personal editorial preference.

A passing unit test proves safe deterministic matching only. No real user-video accuracy result is claimed here.

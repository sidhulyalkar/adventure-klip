# K-TIDE temporal observability and matched-order objective V2

**Status:** Executable SHA-verified feature-shard audit and training objective correction.
No original videos, models, or media are opened by the audit. The goal is
to measure whether cached frames contain useful temporal observations
before pursuing richer K-MOMENT scene understanding.

## First histogram smoke: what it did and did not prove

The first private smoke report showed 46 windows from 16 sources, a
48-dimensional histogram teacher, and a five-epoch MPS checkpoint.
That verifies the feature-to-training path, **not** scene semantics.
The old report did not measure the number of timestamp gaps exceeding
the model's max_gap_ms (7000 by default). A long feature window can
have abundant still images without any usable temporal adjacency.

## Read-only audit of the existing feature shards

Refresh the Klip worktree and run:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    python scripts/klip_ktide_observability.py \
      --workspace "$WS" \
      --manifest "$WS/reports/ktide-histogram-judge-smoke-001/manifest.json" \
      --max-gap-ms 7000 \
      > "$WS/reports/ktide-histogram-temporal-observability-v1.json"

Summary:

    python - <<'PY'
    import json, os
    from pathlib import Path
    p=Path(os.environ["WS"])/"reports/ktide-histogram-temporal-observability-v1.json"
    d=json.loads(p.read_text())
    for key in ("sampleWindows","sources","attentionConnectedPairFraction",
                "windowsWithThreeFrameLocalOrder","windowsWithoutThreeFrameLocalOrder",
                "medianAdjacentGapMs","decodedPtsCoverage",
                "medianAbsoluteRequestedVsDecodedGapDifferenceMs","riskFlags"):
        print(key,":",d[key])
    PY

This diagnostic validates the manifest and every referenced feature-shard
digest. It reads ONLY private .npz shards (not source videos), neither
inferences nor trains, and does not update any workspace files itself.
Keep the private JSON inside reports. Do not commit it to GitHub.

### Definitions

- Adjacent pair: two consecutive observed, strictly increasing requested
  frame times within a prepared feature window.
- Attention-connected pair: adjacent frame times separated by no more
  than the model's declared max_gap_ms. This is a *necessary*, not
  sufficient, condition for seeing an evolving action.
- Connected run: a succession of attention-connected frames. Three or
  more frames qualify for local order training; fewer do not.
- Isolated frame: an observation without a connected neighbor; it may
  teach appearance, but cannot establish motion across absent time.
- Decoded PTS coverage: available source-PTS receipts, not a verified
  common clock. We compare *consecutive time gaps* between requested
  and decoded PTS, not absolute time origins, which may differ.
- Trip groups in the current pilot remain provisional and are not
  independent scientific validation groups.

## Correction: matched-mask local reversal

Old training compared MASKED chronological features with UNMASKED
reversed features. The order classifier could exploit this masking
asymmetry rather than infer frame order. The old reversal also ignored
long gaps.

The corrected objective reverses each *supported contiguous segment*
of frozen features **together with the exact same visibility mask**,
leaving input timestamps and padding unchanged. This eliminates the
mask-visibility shortcut and prevents fake ordering across unsupported
gaps. Windows with no run of at least three connected observations
contribute exactly zero to order classification, although their valid
frames may still contribute to masked reconstruction.

Training loss remains:

    total = masked_teacher_cosine
            + 0.15 * trip_guarded_contrastive
            + 0.10 * matched_local_reversal

Model layer shapes and architecture version are unchanged, so V1
weights remain usable for INFERENCE. This does **not** retroactively
fix the objective that trained earlier checkpoints. New reports and
checkpoints are marked with a dedicated trainingObjectiveVersion
(ktide-matched-local-temporal-reversal-v2). Existing files are never
overwritten; every new run requires a unique name.

An optional new smoke may be run after inspecting the audit:

    python scripts/klip_train_ktide.py \
      --workspace "$WS" \
      --manifest "$WS/reports/ktide-histogram-judge-smoke-001/manifest.json" \
      --name ktide-run-matched-order-smoke-001 \
      --epochs 5 --batch-size 8 \
      --hidden 64 --layers 2 --device auto

**Do not repeat histogram training solely to reduce its loss.** If most
frames are disconnected, fund denser, SHA-verified semantic/video
observations first.

## Remaining limits

- Cross-trip clips depicting the *same type of event* are currently
  potential contrastive false negatives despite same-trip exclusion.
  Ablate or redesign inter-trip negatives before claiming transfer.
- Matched reversal alone is not event understanding; an order
  classifier may still learn scene-specific chronological shortcuts.
- Frozen image embeddings alone cannot infer ski technique,
  hidden muscles, exact ski resort/run, individual wildlife species,
  filming intent or personal meaning.
- Audio/video timestamps and source PTS may differ in origin and
  drift; the audit flags discrepancies but does not fix them.
- Versioned, evidence-backed K-MOMENT dossiers and full-video event
  localization remain future work, not capabilities delivered here.

## Promotion gates

1. Tests prove matched feature/mask reversal, no gap-spanning order,
   and zero order supervision for disconnected windows.
2. A read-only local audit establishes actual temporal connectivity
   on archive feature windows and missing-PTS coverage.
3. Higher-density semantic windows come from source-verified,
   bounded extraction, with quiet controls retained.
4. Heldout cross-trip evaluation requires *verified outings* and
   blinded human relevance judgments, not provisional capture buckets.
5. Compare learned K-TIDE with frozen frame mean, random K-TIDE,
   temporal-delta controls, and independently judged order/event tasks.

Next: see MOMENTWORTHINESS_SCENE_DOSSIERS_V1.md for the complete
archive-aware scene/outing/context/editing research roadmap.

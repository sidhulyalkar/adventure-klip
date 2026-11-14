# K-MOMENT dense embedding probe

Purpose: compare cheap **unlabeled** embedding representations using SHA-verified, already decoded 4-fps K-MOMENT feature caches. Provides deterministic different-source nearest-neighbor decks and reversed/shuffled-order controls. This does NOT establish action recognition or ranking quality, or replace human judgments.

In the up-to-date study worktree:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export EXP="$WS/reports/embedding-ranking-study-001"
    mkdir -p "$EXP"
    python scripts/klip_embedding_probe.py --workspace "$WS" --seed 31 --top-k 3 > "$EXP/embedding-probe-v1.json.tmp"
    mv "$EXP/embedding-probe-v1.json.tmp" "$EXP/embedding-probe-v1.json"

Methods:
- frozen_mean: order-invariant normalized mean SigLIP2 vector
- linear_time_weighted: same frozen vectors, recency-weighted; sensitive to reversal
- mean_absolute_delta: mean absolute change per feature; reversal-invariant but sensitive to permutations
All methods use the exact same observed frames; nearest-neighbor candidates exclude the same original source SHA, but **do not ensure different trips**. The output keeps full source IDs privately for review. No numeric relevance/ground-truth labels are fabricated.

The probe refuses unsupported temporal gaps greater than seven seconds. The underlying bridge rechecks plan/decoded JPEG/derived SHA receipts. Resampled filter timestamps are not native camera presentation timestamps.

To stage the next K-TIDE experiment, create the owner-reviewed outing assignment template:

    python scripts/klip_dense_ktide_bridge.py --workspace "$WS" --mode template > "$EXP/dense-trip-assignments.json"

Do not invent group IDs from capture times; multiple sources from the same outing share the same groupId. When at least three **independent verified outings** are present, the bridge can export a K-TIDE manifest; this is only a training smoke until a larger held-out cross-trip annotated benchmark is available. See docs/EMBEDDING_RANKING_PILOT_001.md.

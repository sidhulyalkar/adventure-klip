# K-MOMENT to K-TIDE embedding-ranking pilot

This adds two offline-by-default experiment utilities to the existing Klip checkout. No full-archive scan, network call, or original camera-media read is performed. The coarse audit only reads completed catalog semantic caches; the dense bridge reads SHA-verified derived receipts and already-computed SigLIP2 vectors.

Run from the repository's active virtualenv:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export EXP="$WS/reports/embedding-ranking-study-001"
    mkdir -p "$EXP"
    python scripts/klip_ktide_inventory.py --workspace "$WS" > "$EXP/feature-inventory.json"
    python scripts/klip_coarse_feature_gap_audit.py --workspace "$WS" --inventory "$EXP/feature-inventory.json" > "$EXP/coarse-gap-audit.json"
    python scripts/klip_dense_ktide_bridge.py --workspace "$WS" --mode audit > "$EXP/dense-bridge-audit.json"

To create an owner-verified outing assignment template:

    python scripts/klip_dense_ktide_bridge.py --workspace "$WS" --mode template > "$EXP/dense-trip-assignments.json"

Fill groupId, goal and subsport in the private assignment JSON with **real verified outing identities**. All recordings from one outing share groupId, even across cameras. Choose valid profile values from scene_profiles/scene_relevance. No model can infer this safely.

After verifying at least three independent outings:

    python scripts/klip_dense_ktide_bridge.py --workspace "$WS" --mode export --assignments "$EXP/dense-trip-assignments.json" --name ktide-dense-verified-pilot-001 > "$EXP/dense-export.json"
    python scripts/klip_ktide_observability.py --workspace "$WS" --manifest "$WS/reports/ktide-dense-verified-pilot-001/manifest.json" --max-gap-ms 7000 > "$EXP/dense-temporal-observability.json"

Optional training smoke after inspection, using a unique output name:

    python scripts/klip_train_ktide.py --workspace "$WS" --manifest "$WS/reports/ktide-dense-verified-pilot-001/manifest.json" --name ktide-run-dense-verified-smoke-001 --epochs 5 --batch-size 4 --hidden 64 --layers 2 --device auto > "$EXP/dense-training-smoke.json"

The bridge will not overwrite prior dataset directories, downloads no models, does not store human reviews, and preserves source/derived receipt hashes. Its derived times are FFmpeg resampled filter-relative timestamps, NOT native camera presentation times. Missing native PTS is explicitly represented as -1. Training eight small pilot windows does not demonstrate ranking improvements or cross-trip generalization. All private outputs remain under workspace/reports; do not commit them.

# K-TIDE zero-cache recovery: catalog present but no training-ready features

## What zero SigLIP2 sources means

K-TIDE's existing inventory uses active, completed checksum-verified
semantic artifact documents. It does not search earlier experimental
preview caches, other model providers or unfinished frame checkpoints.
Zero training-ready sources does NOT mean the 232 original recordings are
missing or previous analysis was lost.

This utility reports semantic job/provider statuses, inactive completed
jobs, partial frame checkpoints, and metadata-based pilot candidates.
Its inspect, template, and preflight modes read a SQLite database using
read-only mode, and do not open, hash or modify source videos, run a model,
download weights or mutate the catalog.

## 1. Inspect the current workspace

Use a dedicated checkout of the latest merged main:

    cd ../adventure-klip-ktide
    git fetch origin
    git switch --detach origin/main
    source .venv/bin/activate
    export WS="$HOME/AdventureAI-clean.QJnopt"
    mkdir -p "$WS/reports"

    python scripts/klip_ktide_bootstrap.py \
      --workspace "$WS" --inspect \
      > "$WS/reports/ktide-zero-cache-diagnostic.json"

Short summary:

    python - <<'PY'
    import os,json
    from pathlib import Path
    d=json.loads((Path(os.environ["WS"])/"reports/ktide-zero-cache-diagnostic.json").read_text())
    for key in ("catalogSources","semanticJobs","jobsByProviderStatus",
                "activeByProviderStatus","inactiveCompletedByProvider",
                "partialSemanticFrameCheckpointRows","semanticJsonFilesOnDisk",
                "suspectedZeroReadyCause"):
        print(key,":",d[key])
    for x in d["suggestedPilotSourcesForHumanSelection"]:
        print(x["sourceSha256"][:12],x["activityFromCatalogNotVerified"],
              x["durationSeconds"],"available",x["originalPathAppearsAvailable"])
    PY

If the diagnostic finds inactive completed SigLIP2 jobs, inspect their
immutable receipts and model revisions before generating anything.
Do not simply activate or copy old artifacts. Partial frame checkpoint
rows are not independently trusted as completed feature documents.

## 2. Explicitly select a small multi-trip pilot

Generate a source-identity draft from the catalog:

    python scripts/klip_ktide_bootstrap.py \
      --workspace "$WS" --template \
      > "$WS/reports/ktide-assignments-v1.json"

Review the JSON. Keep 3 to 6 known source SHA IDs from at least three
different recorded trips. The draft intentionally contains EMPTY fields
for groupId, goal and subsport and is unrunnable until an owner supplies
them. All clips from the same outing must share a groupId even across
different cameras, recording filenames, or sub-sports.

For a skiing source, fill the fields with values such as:

    "groupId": "verified-ski-trip-01",
    "goal": "sport_action",
    "subsport": "skiing_trees"

For underwater clips choose a real snorkeling profile, e.g.
snorkeling_reef, or for biking mountain_biking_trail. No model can infer
trip grouping or approval from filenames for you.

Validate selections without touching original bytes:

    python scripts/klip_ktide_bootstrap.py \
      --workspace "$WS" --preflight \
      --assignments "$WS/reports/ktide-assignments-v1.json" \
      > "$WS/reports/ktide-extraction-preflight.json"

The plan checks catalog membership, original-file presence and size from
filesystem metadata, selected source count, duration, total byte budget,
and effective sample interval. The defaults cap extraction at six sources,
eight minutes each, 30 GB total and 160 frames per source at nominal 4s
interval. This preflight does not independently verify source SHA bytes.

## 3. Explicit opt-in to creating semantic features

The following will open and hash the selected original source recordings,
decode frames, and create/verify SigLIP2 semantic caches in the workspace.
No source media is deleted or overwritten. First-time Hugging Face model
weight downloading is allowed only with the explicit flag shown below:

    python -m pip install -e '.[semantic]'

    caffeinate -i python scripts/klip_ktide_bootstrap.py \
      --workspace "$WS" \
      --run --ack-original-reads --allow-model-download \
      --assignments "$WS/reports/ktide-assignments-v1.json" \
      > "$WS/reports/ktide-extraction-result.json"

Without --allow-model-download the CLI sets offline model-loading
environment variables; that mode requires local cached weights. The
original video is never uploaded by this command, though model weights
may be fetched when permitted.

Klip uses the workspace writer lock to avoid concurrent media writers.
The existing semantic worker checks the source content SHA-256 before
using it and again after processing, verifies file stat stability, and
writes resumed frame checkpoints plus a completed SHA-backed semantic
artifact. Do not run two writers against the same workspace.

Errors in selected source files are reported; rerun only approved sources
after resolving the local issue. The command never expands automatically
to all 232 videos.

## 4. Recheck K-TIDE readiness

    python scripts/klip_ktide_inventory.py \
      --workspace "$WS" \
      > "$WS/reports/ktide-inventory-after-bootstrap.json"

Inspect readySources and modelKey. If 3 pilot sources become ready, that
validates a small extraction-to-feature pipeline, NOT generalization.
The actual 12-query cross-trip retrieval study requires multiple
independent held-out trip groups, independently reviewed playback
evidence, and enough fully judged comparisons.

Training instructions: docs/KTIDE_TRAINING_V1.md
Human evaluation protocol: docs/KTIDE_HELDOUT_STUDY_V1.md

Keep all original footage, private review text, feature shards and model
checkpoints outside the GitHub repository.

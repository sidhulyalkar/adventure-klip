# K-TIDE V1: train a private temporal encoder on Adventure-Klip

**Status:** runnable self-supervised temporal representation learner over
already-computed, verified frozen SigLIP2 frame features. NOT a pixel
foundation model, scientifically validated action classifier, or calibrated
personal preference model. No private samples or source footage in Git.

## Architecture and objective

K-TIDE (Klip Temporal Interaction & Dynamics Encoder) learns how visual
states evolve over source-relative time, with explicit unknown gaps. A
time-token model receives masked frozen teacher vectors, gap-aware frame
differences, and time coordinates, then applies a transformer whose attention
cannot cross missing evidence intervals longer than max_gap_ms.

Self-supervised objective:

    L = L_masked_teacher_cosine
        + 0.15 * L_trip_safe_contrastive
        + 0.10 * L_artificial_order_reversal

Masked feature prediction reconstructs unseen frozen teacher embeddings.
Contrastive positives are two independently masked views of the same
clip. All other clips from the SAME declared trip are excluded as negatives.
The final control distinguishes an original sequence from artificial
temporal reversal. None of these objectives automatically establishes
jump/trick identity, animal species, real speed or physical causality.

The output is a normalized learned episode embedding. A frozen mean-pooled
SigLIP2 feature is included as the minimum retrieval baseline. The model
is randomly initialized; training takes real optimizer steps and produces
a checkpoint, without downloading a pretrained temporal adapter.

## Exact data contract and safety

One private manifest JSON plus one .npz feature shard per explicit window.
Each sample has a source SHA-256, sampleId, manually assigned groupId
for the **entire trip/session**, goal, subsport, source-relative timestamps,
a frozen teacher-model key, compressed feature arrays and a shard SHA-256.

The groupId MUST be shared across all recordings from the same outing to
prevent false train/test generalization. This is not inferred from capture
time. At least 3 independent groups allow a smoke split; 10+ outings with
balanced sport/encounter/quiet controls are more appropriate for evaluation.

Only already completed, receipt-checked local semantic feature artifacts
are read. We do NOT read original videos or rehash the underlying sources
during this export. The original source hash remains a catalog identity,
not fresh original-byte verification. Keep/check source identity when
performing new inference/rendering later.

The owner-provided review export contains 117 human opinions with mostly
free-text instructions, not verified frame-by-frame training labels. Do not
pretend those are ground-truth event, species or speed labels. Do not train
on private recordings without the user's local explicit execution.

## Private Mac / CUDA workflow

Use a separate checkout of the new feature branch (or merged main), with an
existing Klip workspace and completed semantic cache. Do not make changes
to an active SSD-processing checkout.

Install optional local training dependencies:

    python -m pip install -e '.[encoder,dev]'

Check local cached feature coverage without reading source recordings:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    python scripts/klip_ktide_inventory.py --workspace "$WS" \
      > "$WS/reports/ktide-inventory.json"

The inventory outputs source identity, duration, cached sample counts, and
possible timestamp metadata. groupId, goal, and subsport remain unset,
requiring explicit owner assignment.

Create a PRIVATE selection file at
$WS/reports/ktide-selection-v1.json with:

    {
      "version": "klip-ktide-selection-v1",
      "selections": [
        {
          "sampleId": "trip-a-window-01",
          "sourceSha256": "REPLACE_WITH_REAL_SOURCE_SHA256",
          "groupId": "verified-trip-a",
          "goal": "sport_action",
          "subsport": "skiing_trees",
          "startMs": 10000,
          "endMs": 35000
        }
      ]
    }

Actual sourceSha256 must be a 64-character hash from the local inventory.
Include at least 3 different group IDs. Avoid only choosing exciting clips:
include quiet, negative-control and difficult footage for self-supervision.

Generate immutable, bounded local feature shards:

    python scripts/klip_prepare_ktide.py \
      --workspace "$WS" \
      --selection "$WS/reports/ktide-selection-v1.json" \
      --name ktide-pilot-001 \
      --provider siglip2 --max-frames 96

The exporter refuses missing caches, inconsistent teacher revisions, mixed
vector dimensions, duplicate timestamps, and windows with fewer than four
observed frozen features. Missing frames are NOT interpolated. Output is
$WS/reports/ktide-pilot-001/manifest.json.

Train on CPU, Apple MPS or CUDA (auto selects an available backend):

    python scripts/klip_train_ktide.py \
      --workspace "$WS" \
      --manifest "$WS/reports/ktide-pilot-001/manifest.json" \
      --name ktide-run-pilot-001 \
      --epochs 12 --batch-size 8 --hidden 128 --layers 2 \
      --device auto

Training creates a new private reports folder with:
- ktide-best.pt: trained tensor state dictionary, model config, teacher
  revision key and dataset provenance. Later load with weights_only=True.
- training-report.json: exact dataset hash, group split counts, seed,
  validation-selected epoch and separate untouched test objective.

Export learned and frozen-average embeddings for offline comparisons:

    python scripts/klip_embed_ktide.py \
      --manifest "$WS/reports/ktide-pilot-001/manifest.json" \
      --checkpoint "$WS/reports/ktide-run-pilot-001/ktide-best.pt" \
      > "$WS/reports/ktide-embeddings.json"

Outputs represent video windows in the frozen embedding space or trained
temporal representation. Similarity alone does not verify events/identities.

## Qualification and next model stage

1. Freeze the actual source/trip roster. Report missing cached coverage.
   If many videos lack features, perform an *explicitly approved* separate
   bounded embedding pass, not an implicit whole-SSD rescan.
2. Compare K-TIDE with untrained temporal, frozen SigLIP2 mean, and
   motion-change representations on held-out trips using identical samples.
3. Build true human-adjudicated temporal tasks: ski approach/trick/landing,
   crash coaching evidence, underwater encounter progression, family
   memories, different/duplicate wildlife events, quiet/control footage.
4. Collect source/trip-disjoint human pairwise preferences for each
   editorial goal and sub-sport. Train a regularized Bradley-Terry
   preference head only after explicit pairs are sufficiently numerous.
5. Test Recall@K, pairwise accuracy, temporal boundary errors, false
   scene/event claims, uncertainty and editing time against baselines.
6. Expand from frozen teacher features to trainable pixel backbones or
   richer multi-modal features only if actual video evaluation justifies
   the extra compute and controlled source reads.

A lower masked SSL loss is an *optimization check*, not proof of better
highlight curation. The exit criterion for scientifically useful
representations is better **trip-held-out** human event retrieval and
edit preference metrics at comparable compute cost.

## Research inspiration

V-JEPA 2/2.1 suggests predictive video objectives and temporal
consistency; masked video autoencoding and group-aware contrastive learning
suggest additional learning signals. K-TIDE is a research composition
requiring ablations and independent evaluation, not a claimed reproduction
or improvement of those existing models.

No automatic network upload, source-video deletion, publication, external
model download or destructive SSD operation is performed by these tools.

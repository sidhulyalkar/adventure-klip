# K-MOMENT Dense Visual QA V1: understand what the new frames actually show

Status: executable **derived-frame contact sheets** and **optional batch
SigLIP2 visual change hypotheses**. This does NOT detect complete
ski turns, jumps, landings, animal identities, exact action phases or
final trim boundaries. It never opens original camera recordings.

## Motivation and actual inputs

The user completed the first K-MOMENT dense extraction: 8/8 intervals,
649/649 resampled, hashed local JPEGs, 7 distinct original recordings,
zero failures, including four exploration windows. The previous 8-second
SigLIP2 coarse index has zero adjacent observation pairs connected under
K-TIDE's default 7000 ms gap limit.

V1 treats **decoded, previously verified JPEG frames** as the source of
visual evidence. No new SHA scans of the large external HEVC originals,
no FFmpeg, and no SQLite writes are performed by this stage. Every
receipt and referenced image is checksum-verified before usage.

## Quick option: contact sheets without loading an AI model

Use a clean checkout with this PR or merged main, while the private
dense plan and completed JPEG output remain in your workspace:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export PLAN="$WS/reports/kmoment-dense-inspection-plan-v1.json"

First preflight, which only verifies private derived receipts:

    python scripts/klip_dense_visual_qa.py \
      --workspace "$WS" --plan "$PLAN" \
      --decode-name kmoment-dense-pilot-001 \
      --name kmoment-qa-storyboards-001

Then explicitly create a set of **12-frame, time-uniform contact sheets**
per selected clip (8 sheets total):

    python scripts/klip_dense_visual_qa.py \
      --workspace "$WS" --plan "$PLAN" \
      --decode-name kmoment-dense-pilot-001 \
      --name kmoment-qa-storyboards-001 \
      --backend storyboards --run \
      > "$WS/reports/kmoment-qa-storyboards-001-result.json"

Find images under:

    $WS/reports/kmoment-qa-storyboards-001/clip-*/storyboard.jpg

The storyboard intentionally samples each interval uniformly across its
start, middle and ending, rather than only showing intense visual frames.
The results can reveal an obvious mismatch between a proposal and its
source, though 12 frames are NOT sufficient to verify rapid actions.

## Optional second pass: new dense visual representations

This loads the existing SigLIP2 model once, processes derived JPEGs in
small batches and stores an independently versioned vector timeline.
It does **not** download or open raw videos; pretrained model files may
be downloaded if not already present locally.

    caffeinate -i python -u scripts/klip_dense_visual_qa.py \
      --workspace "$WS" --plan "$PLAN" \
      --decode-name kmoment-dense-pilot-001 \
      --name kmoment-qa-siglip2-001 \
      --backend siglip2 \
      --batch-size 6 --run \
      > "$WS/reports/kmoment-qa-siglip2-001-result.json"

Each clip stores:
- storyboard.jpg, uniformly sampled and timestamp-labeled
- frozen_features.json, vector per observed frame with image SHA
- analysis.json, SHA-backed input receipt, model key, pairwise gap QA,
  median cosine change, strongest *visual appearance change* proposals
- summary.json, completed/reused clip counts and connected frame pairs

Image vectors use the existing SigLIP2 pretrained model and preprocessing.
The same frozen model-key construction as the coarse index is used for
future compatibility checks, but **the JPEG-derived dense frames do not
get substituted for native camera frames**.

The temporal change baseline uses cosine distance between successive,
supported image embeddings and applies a conservative relative threshold
plus a minimum absolute difference and peak spacing. This detects image
appearance transitions, which may be camera turns, bubbles, exposure,
pans, cuts or genuine action. It is **NOT an action detector** and
cannot justify a final cut without viewing motion and event resolution.

The new derived cache stays separate from the existing SQLite
semantic_active records and the 8-second archive feature index.
Reusing valid completed analysis checks image/contact/feature digests.
Corrupted analysis output is not silently overwritten.

## Evaluation gate before expanding

Review the eight contact sheets, then use the local Studio to watch
their source intervals if playback is available. For each interval,
note: sport correct?, relevant event present?, complete onset-to-end?,
important context lost?, reason to keep/skip? The four exploration
samples should be reviewed independently of the four ranked samples.

For a subsequent learned K-TIDE comparison, do NOT evaluate on the
same provisional trip groups used to select the clips. Freeze verified
independent outings and human-reviewed event relevance first.
Compare temporal/semantic features with an equal-frame frozen pooling
baseline and a cheap temporal GRU. Optimize true clip recall and
boundary completeness, not only adjacent cosine distance.

Dense frame sampling at ~4 fps gives connected requested grid frames,
but the timestamps still use FFmpeg resampled-filter PTS. An actual
native-frame/PTS audit remains necessary for frame-accurate boundaries.

See docs/KMOMENT_DENSE_FRAME_EVIDENCE_V1.md and
docs/KTIDE_TEMPORAL_OBSERVABILITY_V1.md.

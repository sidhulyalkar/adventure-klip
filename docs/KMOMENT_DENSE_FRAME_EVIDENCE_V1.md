# K-MOMENT Dense Frame Evidence V1: bounded local extraction

**Status:** executable, source-SHA-verified 4-fps JPEG inspection pilot.
Does not classify action phases, determine animal species or generate final edits.

## Why

The 48-source overnight SigLIP2 pilot produced 1,608 frames with no errors;
42 sources were training-window-ready with 1,596 usable frames. Across those
sources all 1,554 adjacent sample gaps exceeded K-TIDE's 7-second maximum.
The 16-clip adaptive inspection plan targets 1,211 higher-rate observations
across 12 original videos, including 4 unscored exploration probes.

This executable stage bridges *planned timestamps* to *observed local JPEGs*,
without opening or processing every recording or altering the 8-second
SigLIP2 semantic cache.

## Local run

Use a clean checkout with this PR or the merged main. The existing plan
lives at workspace/reports/kmoment-dense-inspection-plan-v1.json.

First, **preflight only**. No original video reads, decoding, writes or
model inferences occur:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    python scripts/klip_decode_dense_inspection.py \
      --workspace "$WS" \
      --plan "$WS/reports/kmoment-dense-inspection-plan-v1.json" \
      --name kmoment-dense-pilot-001 \
      --max-clips 8 --max-frames 800

Only after reviewing the plan, and ensuring the external video SSD remains
mounted, that no other workspace writer is active, and that the workspace
has free space, opt into source reads:

    caffeinate -i python -u scripts/klip_decode_dense_inspection.py \
      --workspace "$WS" \
      --plan "$WS/reports/kmoment-dense-inspection-plan-v1.json" \
      --name kmoment-dense-pilot-001 \
      --max-clips 8 --max-frames 800 \
      --run --ack-original-reads \
      > "$WS/reports/kmoment-dense-pilot-001-result.json"

The first eight of the observed 16 requests total about 649 planned
frames and include 4 exploration and 4 ranked intervals. This avoids
jumping directly into all 1,211 frames without testing throughput,
PTS receipts and disk use on the real external volume. A later full
run should use a **new** run name and max-clips=16, max-frames=1700;
do not silently extend an earlier run's limit or overwrite its evidence.

The CLI preflight does not create output directories. In run mode it saves
source-linked, hashed JPEGs and immutable-looking completed clip receipts
inside workspace/reports/kmoment-dense-pilot-001/clips/. A run.json
summary gives successful/reused clips and bounded per-source errors.
Resuming the exact same run uses completed, checksum-validated clip
receipts and verifies the original source SHA before treating them as
reusable. A failed or manually changed derived clip is not overwritten
without an explicit separate repair workflow.

## Source verification

- Group windows by source SHA; full SHA-256 verification happens **before
  and after each source's extraction**. A changed or missing original
  source cannot yield a published completed receipt. All original media
  is read-only. Derived JPEGs and run receipts live only in the workspace.
- Acquire the existing single-writer POSIX workspace lock. Do not run
  another Klip writer against the same workspace concurrently.
- Decode one bounded FFmpeg job per selected interval, never launch
  thousands of FFmpeg processes for individual timestamps.
- Use -ss/-t input seek with -copyts and the video's ffprobe video-stream
  start time to anchor output sampled times. FFmpeg resamples decoded
  frames to 4 fps; the image2 frame_pts filename and stream start_time
  produce a **resampled filter-timestamp estimate** of source-relative
  time, NOT native decoded camera-frame PTS. Match to requested plan
  times with a max 135 ms grid tolerance; report missing requests.
- Decode at 384px by default, cap requests, output frames and per-image
  bytes, require 512 MiB free in derived workspace, enforce per-window
  FFmpeg timeouts, and never use shell=True.
- All source paths are taken from SHA-verified indexed catalog locations,
  never from arbitrary input JSON. The private reports contain source
  SHA identities and derived relative image paths, not originals' paths.
- A verified receipt attests source identity during the decoding pass
  and the checksums of saved JPEGs, NOT that event phase, object identity,
  true speed, source-native frame timing or editorial value were measured.

## Known boundaries and next experiment

This V1 creates sampled images, **not SigLIP2 embeddings**, optical flow,
native decoded PTS, temporal event tracks, complete clips or fast playable
proxy videos. It also does not yet deduplicate partially overlapping
window decodes within a single source, though it verifies that source only
once before and after its grouped windows.

Inspect the new images/receipts and actual output-frame coverage. Then add
a frozen encoder pass over these derived previews, observe short-window
change points and compare with the earlier 8s coarse feature stream.
For boundary accuracy, collect native PTS and a higher-rate micro-pass
around phase transitions, keeping source-relative time and decoded PTS
as distinct quantities. Later preserve setup/onset/core/resolution when
generating cut alternatives and evaluate against human-approved edits.

See docs/KMOMENT_ADAPTIVE_INSPECTION_V1.md and
docs/KTIDE_TEMPORAL_OBSERVABILITY_V1.md.

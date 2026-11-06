# Adventure-Klip full-archive highlight campaign

## End goal

Discover the best supported athletic, nature, travel, filming and personal
moments across the WHOLE archived video corpus; show source-backed, watchable
cross-sport reels; let the user mark frame-relative moments, comment, propose
precise cuts and retain separate action/filming/personal-value judgments.
Keep original camera footage and provisional model hypotheses distinct
from human-approved edited clips.

## Why the previous overnight model job did no work

In the reported overnight experiments, both local second-look queues had
ZERO eligible video windows. The two preflights listed 20 and 89 missing
verified preview entries and 3 and 6 waiting for prerequisite adaptive
first-pass judgments. The runs invoked no new local vision models. Thus
their completed checkpoint means the queue was exhausted, not that the
videos were understood.

The earlier archive-value study listed 232 catalog recordings, 214
accessible and valid at that historical moment, only 42 with existing
episode hypotheses and 355 ranked candidate windows. Later curation
recovered 11 verified local previews. These counts represent different
snapshots and cannot be silently combined into a live archive total.

## Full program and acceptance gates

| Gate | Milestone | Acceptance |
|---|---|---|
| G0 | Catalog and source audit | Every recording accounted for; source location, size, duration, historic coverage, invalid fields and blocked paths reported; ZERO source video I/O |
| G1 | Stable local source provenance | Source media moved to known healthy storage and SHA-verified against catalog identities; missing/corrupt originals remain blocked |
| G2 | Coarse entire-timeline exposure | Every qualified recording has a frozen source-backed low-resolution scan receipt or explicit exception; frame coverage and wall time reported |
| G3 | Reviewable candidate evidence | Motion and quiet control intervals represented across sources; SHA-verified private H264 playback proxies or explicit missing state |
| G4 | Sport/scene intelligence | Context-aware temporal boundaries, motion/gaze/camera separation, terrain/sport context, action peak/resolution and honest uncertainty; **not yet built as archive-wide training system** |
| G5 | Multi-perspective human review | Blind action and filming ratings, personal memory, independent viewers and timecoded reel comments, with disagreement retained |
| G6 | Scientific benchmark | Source/outing-disjoint splits, random time controls, recall of real highlights, top-K human acceptance, correctness of outcome claims, time per approved moment |
| G7 | Production reels | Verified source-quality export, camera-aware finishing, appropriate audio/rights, multiple objective-specific collections |

A completed job with ZERO eligible candidates is NOT a successful
understanding run. Missing evidence stays unknown, never auto-negative.

## Safe first command: zero original video reads

With package installed on the new branch:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export CAMPAIGN_DIR="$WS/reports/full-highlight-campaign-v1"
    PHASE=audit bash scripts/klip_full_archive_highlights.sh

Inspect the exact local results:

    cat "$CAMPAIGN_DIR/audit/summary.json"

The audit reads the local SQLite catalog and optionally the already-frozen
archive value study. It neither stats nor resolves any catalog source video
path, including a potentially stalled removable SDXC volume. Storage
location classes are path-only indicators, not live verification.

## Restore the full media corpus to healthy local storage

Do not ask an unattended process to re-read the previously stalled SD
card. Diagnose/recover that storage separately, transferring intact
originals to a healthy, stable directory before commissioning expensive
analysis. Transfer can also hang on a faulty card; no script here promises
to repair the device.

For a directory that ALREADY contains intact local video files:

    export HEALTHY_SOURCE_ROOT="$HOME/AdventureAI-staged-originals"
    klip --workspace "$WS" index "$HEALTHY_SOURCE_ROOT" --dry-run
    klip --workspace "$WS" index "$HEALTHY_SOURCE_ROOT"

Zero-copy indexing verifies source identities. In this branch, duplicate
index registration no longer rehashes an OLD catalog path on a removable
volume. The new source directory MUST remain available; indexing does
not copy camera files. If you need Klip-managed content-addressed originals,
use klip ingest from a healthy staged folder instead, assuming the workspace
has enough capacity. Do not ingest directly from an unresponsive card.

An index/ingest changes catalog source paths, so use a NEW campaign
audit, never resume an old audit after source registrations change.

## Opt-in complete healthy-local coarse pass + blind review packet

Only after confirming the staged directory is healthy and registered:

    export CAMPAIGN_DIR="$WS/reports/full-highlight-from-staged-v1"
    export PHASE=all
    export ENABLE_ORIGINAL_READS=YES
    export HEALTHY_SOURCE_ROOT="$HOME/AdventureAI-staged-originals"
    export PREPARE_MISSING_PREVIEWS=YES
    export SCAN_ASSETS_PER_PASS=8
    export SCAN_PASSES=40
    export SCAN_FPS=1
    export PACKET_LIMIT=240

    caffeinate -i bash scripts/klip_full_archive_highlights.sh

Each source scan SHA-verifies one trusted local original, checks duration,
then samples the entire timeline at very low resolution. The proposal
sampler includes high motion changes plus evenly distributed quiet
source-time controls. This is NOT a trick detector or species classifier.

Scans run serially in version 1 and checkpoint per asset. A scan failure
does not imply that the footage is boring; untrusted, missing and corrupt
files remain blocked.

The packet stage reuses existing SHA-verified local proxies. With
PREPARE_MISSING_PREVIEWS=YES it opens healthy source originals only
after distinct per-source SHA verification, then encodes at most TWO
independent short previews concurrently. The review packet remains
subjectively unlabeled. No human rating, athletic success or training
rights are inferred. Source times in fast-seek previews need manual
boundary refinement for frame-accurate final editing.

Review generated outputs:

    cat "$CAMPAIGN_DIR/coarse-scan/scan-state.json"
    cat "$CAMPAIGN_DIR/packet-result.json"
    open "$CAMPAIGN_DIR/review-packet/index.html"

Rerun PHASE=scan with the same CAMPAIGN_DIR to resume incomplete
per-source scan receipts. Do not overwrite an immutable existing
review packet; use a fresh packet/campaign directory for modifications.

An external mounted SSD requires explicit ALLOW_MOUNTED_SSD=YES. This is
for a user-verified healthy SSD, not the stalled SDXC reader. Do not
increase original-read concurrency to force throughput on bad hardware.

## Human review to interactive reel

After completing annotations in the new blind review page, export
human JSON, then run:

    klip --workspace "$WS" highlight-feedback-plan \
      --pilot-dir "$CAMPAIGN_DIR/review-packet" \
      --reviews "$HOME/Downloads/EXACT-REVIEW-EXPORT.json" \
      --output-dir "$CAMPAIGN_DIR/human-feedback"

    klip --workspace "$WS" highlight-reel-studio \
      --pilot-dir "$CAMPAIGN_DIR/review-packet" \
      --feedback "$CAMPAIGN_DIR/human-feedback/review-discovery-plan.json" \
      --workers 2 --scout-limit 80 \
      --output-dir "$CAMPAIGN_DIR/interactive-reels"

    open "$CAMPAIGN_DIR/interactive-reels/index.html"

Later import the player's timecoded reactions with
highlight-reel-feedback, and run highlight-next-pass to prioritize
verified retrims, interest markers and longer sport runs. Interactive
preview reels do not replace source-quality final video output.

## Compute plan, realistic for Apple Silicon

Initial safe concurrent workloads:

- Source ingest/checksum: ONE original-video reader. The scan records
  SHA MiB/s, decoder video-seconds per wall-second and per-asset elapsed
  time so throughput tuning can be evidence based.
- Coarse motion: ONE FFmpeg decoder at 1 FPS output, 64 by 36 gray
  pixels. It still decodes much of the original video; actual time
  depends on codec, frame size, disk and hardware acceleration.
- Preview rendering: up to TWO CPU FFmpeg encoders per verified source.
- Cached private derivative copies: up to FOUR independent I/O workers.
- SigLIP2/other heavy vision embeddings: ONE resident model on MPS with
  microbatching as a future separate stage; do not instantiate a model
  for every 20-second clip.
- Local Ollama reasoning: ONE stream only when there are actually
  SHA-verified, first-pass-qualified videos.

Do not assume increasing workers is faster. Benchmark 5-10 healthy,
representative files, recording wall time, memory and SSD throughput.
Compare one versus two decoder workers only after the one-worker
baseline and source safety gates pass.

Archive processing time should be estimated from actual catalog duration
H, measured decoder speed R (video seconds per wall second), and SHA
read throughput B, not an invented fixed estimate. Disk staging capacity
must be checked against total catalog GiB plus temporary derivatives.
A managed copy can require another whole source-sized footprint.

## Immediate deliverables versus remaining engineering

Implemented in the initial PR: metadata-only catalog coverage audit;
frozen provenance; explicit healthy-local-source SHA verification;
single-stream low-res full-timeline coarse discovery; per-source
checkpoints and measured throughput; motion and uniform-control
candidate selection; source-safe cache-first blind review packet with
bounded parallel preview encoding; stale removable-path rehash fix.

Future separate tasks: robust event phases and continuous scene
segmentation, sport technique evidence, multidimensional ranking,
adaptive high-res rescoring of salient/uncertain intervals, verified
outing/source grouping, independent benchmarks and original-resolution
finished music/audio video exports. Do not label these as complete
merely because the coarse campaign reports finished.


## Operational campaign status: do not mistake no-op for completion

Each launcher phase writes progress.json using the new command:

    klip --workspace "$WS" highlight-campaign-status \
      --audit "$CAMPAIGN_DIR/audit/campaign-audit.json" \
      --scan-dir "$CAMPAIGN_DIR/coarse-scan" \
      --packet-dir "$CAMPAIGN_DIR/review-packet"

Before scanning, omit --scan-dir. Before a review packet exists,
omit --packet-dir. This is a source-file-free status check.

It computes:
- exact catalog denominator, valid identity/duration and previous
  candidate coverage;
- SHA-backed completed scans, verified video duration percentage,
  attempted hash passes, remaining/failed/blocked source counts;
- catalog compressed original GiB covered (not live disk space),
  coarse motion samples and candidate intervals proposed;
- measured decoder throughput, available playable H264 review proxies,
  and whether the packet matches the current source scan checkpoint;
- explicit go/no-go blockers. Even 100% coarse scan exposure is NOT
  proof that all athletic highlights were discovered.

The status tool never stats an original stored_path, decodes media,
re-hashes camera videos, calls Ollama or changes human reviews.

The cross-platform integration test constructs a real ORIGINAL
SYNTHETIC H264 sample and exercises actual SHA verification, entire
timeline FFmpeg decoding, preparation of playable cached H264 previews,
and the existing offline blind review packet. It is an engineering
qualification test, not evidence of quality on a DJI archive. To run:

    python -m pytest -q tests/test_full_archive_campaign.py

An uninterruptible macOS kernel read on unhealthy media still cannot
reliably be cancelled from Python. Never point any overnight job at
the previously stalled SDXC reader. Repair/stage to healthy local
storage before enabling camera-original reads.

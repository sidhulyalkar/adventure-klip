# Adaptive Highlight Next Pass V1

The goal is to convert a limited first user curation pilot and time-aligned
reel comments into a **repeatable, source-safe next inspection queue**, not
to pretend ten human preferences identify the archive's globally best clips.

## What this implementation does

- Joins **frozen source family and source-relative time** from an immutable
  curation pilot to its associated review-discovery plan and Reel Studio.
- Optionally accepts the Studio's qualified timecoded human feedback,
  including exact positive-duration cut boundary suggestions and
  human-interest time markers.
- Prioritizes exact requested retrims first, then source-time windows
  around timecoded human reactions, then earlier longer-context requests.
  These are **review attention tiers**, not predicted clip scores.
- Merges highly overlapping proposals of the same kind and source
  (at least 72% overlap of the longer candidate). Exact human trim
  requests remain unmerged to preserve the actual human intent.
- Balances known source activities and limits proposed intervals per
  original recording. Unknown-activity sources remain UNKNOWN.
- Inspects only the private verified **local Studio derivatives**,
  matching source SHA, source family and overlapping source-time range.
  The queue records the actual fraction of the proposed interval
  currently playable in existing cached video. Partial visibility
  does **not** establish that an unseen action happened.
- Provides private HTML output with links that open the existing
  Reel Studio directly at the overlapping source-time point.
- Never opens or stats original camera files on the SDXC card;
  never downloads videos, renders new proxies, trains or deletes.

## Run

From a clean copy of the feature branch:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export PILOT="$WS/reports/highlight-curation-pilot60-v1"
    export PLAN="$WS/reports/highlight-feedback-for-reels-v1/review-discovery-plan.json"
    export STUDIO="$WS/reports/interactive-reels-v1"

    klip --workspace "$WS" highlight-next-pass \
      --pilot-dir "$PILOT" \
      --feedback-plan "$PLAN" \
      --studio-dir "$STUDIO" \
      --limit 60 \
      --per-source 3 \
      --output-dir "$WS/reports/highlight-next-pass-v1"

    open "$WS/reports/highlight-next-pass-v1/index.html"

If you have already exported timecoded reactions and qualified them:

    klip --workspace "$WS" highlight-reel-feedback \
      --studio-dir "$STUDIO" \
      --annotations "$HOME/Downloads/klip-reel-reactions-EXACT.json" \
      --output-dir "$WS/reports/reel-timed-feedback-v1"

    klip --workspace "$WS" highlight-next-pass \
      --pilot-dir "$PILOT" \
      --feedback-plan "$PLAN" \
      --studio-dir "$STUDIO" \
      --timed-feedback "$WS/reports/reel-timed-feedback-v1/timed-feedback.json" \
      --limit 60 --per-source 3 \
      --output-dir "$WS/reports/highlight-next-pass-with-reactions-v1"

    open "$WS/reports/highlight-next-pass-with-reactions-v1/index.html"

All folder names are examples. Use actual paths. Each output folder must
be NEW and inside the Klip workspace reports/ or exports/ directory.
The pipeline fails when SHA fingerprints between sources disagree.

## Output contracts

adaptive-queue.json:

- frozen pilot/plan/studio/timed-feedback SHA fingerprints;
- reviewed original asset hashes, source families, activity (UNKNOWN if
  unresolved), exact candidate intervals and supported reviewer cues;
- count of near duplicate proposals removed and number of distinct
  sources, types of requested follow-ups and coverage by cached video;
- whether a verified local preview covers full, partial or none of each
  interval; the associated original Studio clip ID and source-time seek;
- explicit no-original-read / no-new-preview / no-training flags;
- no speed, athlete-skill, rarity, outcome or excitement measurements.

index.html:

- compact ordered list of source-time search tasks and a coverage bar;
- source hash prefix and interval time in seconds (no original paths);
- deep links to the existing private Reel Studio when overlap is
  documented and that clip's exact cached preview SHA verifies;
- explicit statements that a partial overlap is not a verified
  outcome and an unavailable candidate needs controlled source recovery.

The underlying video player has a bounded deep-link contract:
query ?clip=<frozen-id>&t=<source-ms> is accepted only if that clip is
in the frozen playlist and source time falls within its actual window.
No arbitrary URLs or file paths can be supplied by a deep link.

## Execution sequencing and parallelism

**Stage 0:** frozen local metadata, one inexpensive pass;
fail closed on mismatching source identities or changed local proxy.
**Stage 1:** small parallel local checksum/copy workers, where source
derivatives already exist (Reel Studio stage, at most 4).
**Stage 2:** reviewer timecodes and source coverage proposals, no
original media reads.
**Stage 3:** when healthy and authorized local original files become
available, one serial source hash per unique original with checkpoint
resume (#67). Do not repeat complete SHA verification for every
proposed 20-second window. Fan out limited FFmpeg jobs **only after**
deduplicated source-verification and local healthy storage is proven.
**Stage 4:** parallel CPU-bound task slots for independent decoded local
proxies; use 1–2 FFmpeg encoding jobs on the M1 Max initially and
measure throughput, memory and thermal throttling before raising caps.
Run at most one heavy unified-memory vision model at a time until
benchmarks demonstrate actual concurrency gains.
**Stage 5:** human acceptance versus baseline, comparison between
source/outing-disjoint tasks and independent observer ratings.

No new model-training-ready dataset, complete archive recall or video
quality metric is established by the queue. The largest unknown from
the previous pilot remains the 172 eligible recordings with no ranked
episode candidates. Address that discovery blind spot separately using
the archive coverage audit before concluding the reels contain the
archive's best sports moments.

## Scientific stop/go gates

- Report reviewer coverage, **not just** top selected-candidate quality.
- Keep the user's immediate four provisional highlights independent
  from unknown athletic scouting material.
- A Skip does not create a negative training example for the rest of
  the source, and a "Wow" doesn't verify a successful stunt.
- Preserve disagreement and unknown/off-camera outcomes; never
  hallucinate trick completion from POV motion.
- Evaluate source-time search recall on random held-out windows.
- Keep all source families, edits, and reposted views in the same
  train/test group once a licensed, adjudicated dataset exists.
- Keep private reaction texts, location traces and source paths out of
  public GitHub commits and any hosted reel.

CI tests include deterministic queue sorting, exact trim priority,
deduplication, metadata-bounded times, private preview hash validation,
cross-artifact fingerprint rejection, and a fake unmounted SD card that
must never be accessed.

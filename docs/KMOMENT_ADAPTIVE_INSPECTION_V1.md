# K-MOMENT Adaptive Inspection V1: coarse context to dense observation

Status: implemented deterministic, **read-only sampling planner**. This does
not extract frames or claim a verified action or finished edit.

## Actual local evidence (Nov 13, 2026)

The 48-source, 8-second SigLIP2 run computed 1,608 frames without errors.
The frozen-cache inventory found 42 window-ready recordings containing
1,596 usable frames across 3.883 hours. Six very short recordings had
1..3 samples. Across the 42 ready sources there were 1,554 adjacent
observation pairs with median gap 8,000 ms; **zero** pairs met K-TIDE's
current 7,000 ms connected-time threshold.

Thus coarse embeddings can support visual-context retrieval and provisional
appearance change, but cannot yet support valid contiguous action sequences.
Increasing the gap threshold to force connectivity would not observe
unseen turns, jump landings or the appearance and departure of marine life.

## The three-stage design

1. Coarse discovery: use frozen visual embeddings, existing motion
   proposals, audio cues and human reviews to find potentially valuable
   regions; include sampled quiet/unknown scenes to measure false negatives.
2. Dense inspection: decode only bounded selected source intervals at
   multiple rates, preserving approach and aftermath around full candidate
   cores, recording actual decoded PTS and evidence gaps.
3. Event assembly and editing: use verified or inspected dense sequences to
   distinguish setup, onset, peak, development and resolution. Generate
   competing trims that *preserve complete core events*, then evaluate
   human preference and technical quality separately from event truth.

This PR implements the read-only **stage 2 inspection planner** only.
The decoder/executor and event-phase model are later milestones.

## Run the planner

Refresh your checkout with the merged feature branch (or use this PR's
branch until merged). The existing private ranking JSON is the input:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    python scripts/klip_plan_dense_inspection.py \
      --workspace "$WS" \
      --rank-report "$WS/reports/kmoment-clip-ranking-v1.json" \
      --max-clips 16 \
      --frame-budget 1700 \
      --explore-fraction .25 \
      --max-per-source 2 \
      > "$WS/reports/kmoment-dense-inspection-plan-v1.json"

Inspect the private plan without reading video:

    python - <<'PY'
    import json, os
    from pathlib import Path
    d=json.loads((Path(os.environ["WS"])/"reports/kmoment-dense-inspection-plan-v1.json").read_text())
    for key in ("plannedClips","distinctSources","requestedFrames",
                "frameBudget","countsBySelectionType",
                "countsByUnverifiedSportHint","skipped"):
        print(key,":",d[key])
    for item in d["inspections"]:
        c=item["coarseContext"]
        print(item["sportHint"], item["selectionType"], item["sourceSha256"][:12],
              "window",item["expandedStartMs"],item["expandedEndMs"],
              "frames",item["plannedFrameCount"],
              "coarse samples",c["observationsInExpandedInterval"],
              "median gap",c["medianAdjacentGapMs"])
    PY

Source identifiers and private reports stay outside Git. No original video
paths or source video bytes are emitted. This JSON is a *sampling request*,
NOT observed video, successful verification or a permitted publish/edit.

## Scheduling rules and safety

- Verify every source identity is in catalog and candidate timestamps fit
  its cataloged duration. Detect missing/stale sources. Limit input size
  and record count, and accept ranking packets only inside workspace reports.
- Read SQLite read-only and consume only existing checksum-verified frozen
  SigLIP2 caches. Do not run models, FFmpeg, open/rehash original footage,
  update catalogs or change active semantic configurations.
- Preserve the full candidate interval and include 3.5..6.5 seconds of
  sport-conditioned context when available. Do not truncate a long action
  around an assumed climax: mark over-45-second expanded requests as
  manual-split-required and leave the original interval alone.
- Plan two frames/second in the approach/aftermath context and four
  frames/second throughout the entire proposed core. These are inspection
  starting values, not sufficient evidence of short turns or rapid animal
  movement; a subsequent boundary/peak pass may need 8..16 fps.
- Cap by total requested frames (default 1,700) and number of intervals
  (default 16). Preserve complete requests or skip if over budget, rather
  than silently cutting a candidate core.
- Reserve a configurable fraction (default 25%) for unscored exploration,
  interleave unverified sport hints, suppress heavily overlapping
  duplicate source intervals, and cap selected intervals per source.
- Respect human Skip intervals as hard guards. Never use camera jolts as
  verified crashes, scene contrast as action truth, or static embeddings
  as evidence that an event completed.
- Report coarse sample count, adjacent gap statistics, pairs connected
  under the current 7-second K-TIDE threshold and optional sparse cosine
  appearance contrast. It does not classify the underlying event.
- Event phase fields are unobserved, and trimReady is false. No
  automatic deletion, publication, lossless cut, preference training,
  event verification or action labeling occurs.

## Next: efficient, auditable dense decoding

Build a separate, explicitly opt-in extractor for exactly these scheduled
source-relative frame requests. It must SHA-verify originals before/after,
use a bounded batched FFmpeg extraction rather than launching a decoder
for every frame, checkpoint completed evidence, preserve true source PTS,
and store the results in private derived report artifacts.

Keep the 8-second coarse semantic configuration distinct from the new
dense-derived configuration. Do not silently overwrite semantic_active
so that prior analyses become irreproducible. A specific downstream
consumer should request a named evidence revision.

After this, build human-reviewed phase boundaries and paired edit variants.
Measure event/core retention, trim boundary error, duration wasted,
discovery recall and false-skip rate. Compare against naive high-motion
clipping and unchanged full windows on the same verified recordings.

See also: docs/PROBLEM_FORMULATION.md,
docs/KMOMENT_CLIP_RANKING_V1.md,
docs/KTIDE_TEMPORAL_OBSERVABILITY_V1.md.

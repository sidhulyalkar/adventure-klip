# Klip Ski: multi-scale event lines and standalone trick edits

## Why the 20-second moment abstraction is not enough

A reviewed park run can contain a connected sequence of jumps, followed by
an extra trick after a long transition. Raw source-relative point markers
capture *when* a feature occurred, not takeoff, landing, or clip boundaries.
A single edit must not flatten those separate editorial units into one
unnecessarily long or a dozen disconnected 5s clips.

The reviewer already marked the full run source start/end and feature points.
This experimental planner learns its **local cadence from the shorter half of
consecutive feature gaps**, rather than assuming an arbitrary 20s ceiling:

    shortGapCadence = median(shortest_ceil(nGaps / 2))
    allowedGap = clamp(scaleRatio * shortGapCadence, scaleMin, scaleMax)

Three complementary scales are exposed:

| Scale | Purpose | Cadence ratio | Min/Max join gap |
|---|---|---:|---|
| tight | short, linked events | 1.35 | 6.5–10s |
| balanced | recommended human review | 1.80 | 8–12s |
| immersive | broader full-line option | 2.50 | 9.5–18s |

With human marker intervals around 6.1, 5.8, and 13.9 seconds:
tight + balanced group the first three features and separate the last.
Immersive retains the option to group the complete line. A reviewer can
force a split or join after a particular feature timestamp. An explicit
owner-reviewed stop/lift marker vetoes **all** joins across it.

Source-relative edit ranges preserve an approach (full reviewed run start
for the first feature group), allow configurable local pre/post padding,
and clamp to the owner's **reviewed run** bounds. An additional feature
near run end is marked as **post-roll undercovered** rather than pretending
a complete landing is verified. The original full context video can be
reviewed to extend the run end if justified.

## New modules

- \`ski_event_scaling.py\`: deterministic, side-effect-free temporal
  clustering, three alternate edit scales, gap decisions and review flags.
- \`klip_plan_ski_event_edits.py\`: input SHA verification; a no-side-effects
  plan-only mode; optional non-destructive edits of the verified **existing
  context preview**. It does **not** read the original DJI SSD.
- Synthetic tests: example with 4 feature points, forced splits/joins,
  explicit stop barrier, short clipped tail, single-feature run,
  source-preserving preview clip render and sequential montage.

No pretrained model runs, no athlete velocity inference, no inferred
trick difficulty, no final master approved, no footage deleted or altered.

## Run locally

This is a follow-on to \`SKI_RUN_CONTEXT_V1.md\`. Use the existing
\`run-context-plan.json\` and exported owner JSON from the full-run review.

First, dry run:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export RUN="$WS/reports/klip-v3-ski-run-20261115-190305-94429"
    export LABELS="$HOME/Downloads/klip-run-2efff9077808-16000.json"
    export EDIT="$WS/reports/klip-temporal-ski-edits-$(date +%Y%m%d-%H%M%S)-$$"

    python scripts/klip_plan_ski_event_edits.py \
      --workspace "$WS" --context-dir "$RUN" \
      --owner-json "$LABELS" --output "$EDIT" \
      --force-break-after-ms 23714 \
      --secondary-feature-ms 37639

The user-entered overrides explicitly establish the first three jumps as
the main line and the fourth as a smaller extra, regardless of how much
the heuristic threshold changes on future examples. Both inputs are
separate from the canonical annotated full run.

Then preview clips and optional simple montage:

    python scripts/klip_plan_ski_event_edits.py \
      --workspace "$WS" --context-dir "$RUN" \
      --owner-json "$LABELS" --output "$EDIT" \
      --force-break-after-ms 23714 \
      --secondary-feature-ms 37639 --render-preview

    open "$EDIT/review.html"

Rendering uses only the 48.128-second private H.264 context copy. It
outputs tentative main-line and extra-jump clips, plus a simple sequence
for watching them back to back. **No crossfades are implemented** and
native-source frame-PTS accuracy has not been verified. If a chosen
cut needs more landing context, adjust the owner run end after playback;
the planner will not silently extend outside reviewed bounds.

## Next: actual video-conditioned temporal segmentation

This baseline is time-series *annotation cadence*, not video-native
action understanding. Next evaluate event groups against dense source
video continuity: stop/lift, hard cuts, camera stabilization, motion-flow
changes, takeoff/landing, and human-approved continuity labels. A frozen
pretrained video encoder could supply a temporal representation for
candidate groups, but must beat the simpler cadence baseline on
held-out runs before replacing it.

Review metrics: group-match against owner editorial splits, missed feature
count, unnecessary run fragmentation, transition retention, post-landing
coverage, and A/B preference of edited vs uncut clips.

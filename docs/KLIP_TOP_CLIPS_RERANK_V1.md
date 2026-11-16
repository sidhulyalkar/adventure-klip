# Klip: Rerank and extract playable top clips from frozen verified previews

## Scope and accuracy contract

This deliverable provides an immediate **playable local Top Clips review gallery**.
It uses a frozen 120-window V2 candidate plan, the 108 model-accepted
Auto-Scout reports, and optionally one human-reviewed complete ski run.
It **does not claim** to have discovered the best footage in all 232
recordings or rescanned the 452 GiB originals. Only the original 120
proxies + one source-backed full-run context are searchable here.

The known editorial benchmark is a 48.128s source video with jumps at
11.846, 17.960, 23.714 and 37.639 seconds. The owner wants the first three
as one connected park line and the extra as a separate highlight.

## Ranking hierarchy

1. Human-kept run and connected subclips first, preserving complete event
   context; show suggested cut points and any insufficient landing tail.
2. For the remaining model-only previews, rank for **review opportunity**
   using specific feature suggestions, within-sport rarity, the sampled
   stills supporting a tag and source diversity. Those scores are
   heuristics, NOT trained probabilities or real highlight-quality scores.
3. Model-proposed \`ski_park\` (present in every ski candidate) and
   \`marine_scenic\` get near-zero semantic weight.
4. The very overpredicted model quality flags (\`blur\` in 102/108) are
   deliberately excluded from scoring. They are displayed as context only.
5. The 12 \`model_schema\` failed candidates stay in a separate
   investigation pool, not tagged as bad clips or fake negatives.
6. Automatically avoid selecting 20s fragments from the same source as the
   reviewed full park line.

No original camera read, no new model call, no master video render, no
media publication or catalog mutation.

## Outputs

An explicit \`--render\` creates an independent new report directory with:

- \`review.html\`: playable, source-labeled Top Clips gallery;
- \`ranked-clips.json\`: all ranked source windows and provenance;
- \`top-ranked.csv\`: sortable ranked index and source-relative timecodes;
- \`all-candidates.json\`: source-backed model candidates, owner groups and
  all failed-model controls;
- \`media/*.mp4\`: actual local MP4 copies of selected 20s proxies and
  newly rendered owner-cut excerpts from the verified 48.128s context proxy.

All other source recordings remain untouched. The owner's complete
three-jump edit is encoded from the existing verified run-context H264,
not recomputed from the DJI original. The original native frame PTS may
differ slightly from the approximate proxy time mapping.

## Runbook

From an isolated checkout of branch
\`experiment/klip-full-preview-top-ranking\`:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export PILOT="$WS/reports/klip-v2-pilot-20261114-153934-88998"
    export SCOUT="$WS/reports/klip-v2-autoscout-20261115-130506-89529"
    export RUN="$WS/reports/klip-v3-ski-run-20261115-190305-94429"
    export OWNER="$HOME/Downloads/klip-run-2efff9077808-16000.json"
    export TOP="$WS/reports/klip-new-top-clips-$(date +%Y%m%d-%H%M%S)-$$"

    python scripts/klip_rank_complete_clips_v1.py \
      --workspace "$WS" --pilot "$PILOT" --autoscout "$SCOUT" \
      --context-dir "$RUN" --owner-run "$OWNER" \
      --force-break-after-ms 23714 \
      --secondary-feature-ms 37639 \
      --output "$TOP" --top 24

    python scripts/klip_rank_complete_clips_v1.py \
      --workspace "$WS" --pilot "$PILOT" --autoscout "$SCOUT" \
      --context-dir "$RUN" --owner-run "$OWNER" \
      --force-break-after-ms 23714 \
      --secondary-feature-ms 37639 \
      --output "$TOP" --top 24 --render

    open -a Safari "$TOP/review.html"

The dry run only verifies input receipts, policies and media hashes; it
does not create a report. The render writes the actual gallery and clip files.

## Next milestone: full recording ranking

A credible ranking of the complete library requires full-timeline
coverage and event segmentation per video, source/outing-aware dedupe,
then a comparison of predicted complete-run boundaries against multiple
human reviewed examples. A single seven-still proxy clip or one approved
park line is not sufficient evidence for training a general preference
ranker. See \`docs/SKI_RUN_CONTEXT_V1.md\` and
\`docs/SKI_TEMPORAL_EVENT_SCALING_V1.md\`.

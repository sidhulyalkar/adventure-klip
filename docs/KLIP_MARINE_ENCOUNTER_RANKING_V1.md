# E8 | Animal-encounter ranking and clean Top Clips

## Why the previous Top 24 was misleading

- The ranker included 2 unknown-category clips in the same sport-balanced
  review queue as skiing and snorkeling.
- The console helper printed "reviewed run" for empty model tag arrays,
  although those two clips had never been human reviewed.
- Unreviewed snippets as short as 1 second were compared to complete
  experiences. They are valid context-lookup leads, but not credible
  top-ranked highlights.
- Round-robin promotion gave generic riding clips too much of the Top 24.
- Seven VLM stills could list multiple rays/fish/reef tags without verifying
  a single visible animal or pleasing camera framing.

## New contracts

- Top Clips main gallery: skiing, snorkeling, bike ONLY. Other and unknown
  clips stay in all-candidates.json under quarantinedModelCandidates.
- Unreviewed model clips shorter than six seconds also go to quarantine;
  an explicitly human-kept short animal encounter can qualify.
- Aim for 60% snorkeling in the top 24, cap biking at two by default.
- Human-approved complete skiing runs still lead the review gallery.
- Human-confirmed visible animal + Keep is a new higher evidence tier.
  Within this tier, explicit owner filming ratings help rank encounters.
- A human Skip excludes a proposed wildlife clip from the main gallery.
- No unsupported ray species inference or confidence probabilities.
- Blur/shake model guesses remain excluded from ranking penalties.
- Model schema failures and quarantined clips remain investigation controls,
  NOT human-labeled negatives or deleted media.

The score for unreviewed model candidates is still just an imperfect
REVIEW OPPORTUNITY ordering, not true visual/aesthetic quality.

## Immediate clean rerank

Use branch: experiment/klip-animal-encounter-ranking

Set the following workspace inputs in the study worktree:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export PILOT="$WS/reports/klip-v2-pilot-20261114-153934-88998"
    export SCOUT="$WS/reports/klip-v2-autoscout-20261115-130506-89529"
    export RUN="$WS/reports/klip-v3-ski-run-20261115-190305-94429"
    export OWNER="$WS/reports/klip-owner-run-four-jumps.json"
    export TOP="$WS/reports/klip-clean-top-$(date +%Y%m%d-%H%M%S)-$$"

    python3 scripts/klip_rank_complete_clips_v1.py \
      --workspace "$WS" --pilot "$PILOT" --autoscout "$SCOUT" \
      --context-dir "$RUN" --owner-run "$OWNER" \
      --force-break-after-ms 23714 --secondary-feature-ms 37639 \
      --snorkeling-share 0.60 --max-bike 2 --top 24 \
      --output "$TOP" --render

    open -a Safari "$TOP/review.html"

If the reviewed owner file above is not present, substitute the actual
export from Downloads. The workflow refuses to fake it.

## Blind marine encounter review with direct media reuse

Prepare a source-verified page of EVERY predicted snorkeling clip:

    export MARINE="$WS/reports/klip-marine-$(date +%Y%m%d-%H%M%S)-$$"
    python3 scripts/klip_prepare_marine_encounter_review.py \
      --workspace "$WS" --pilot "$PILOT" --autoscout "$SCOUT" \
      --output "$MARINE" --run
    open -a Safari "$MARINE/review.html"

The HTML uses the existing sibling pilot MP4s, without copying them.
In case Safari does not permit local file media, start a private HTTP
server in a separate terminal rooted at WS/reports:

    cd "$WS/reports"
    python3 -m http.server 8765 --bind 127.0.0.1

Then from another terminal:

    open -a Safari "http://127.0.0.1:8765/$(basename "$MARINE")/review.html"

Model guesses are hidden during human review. Mark the presence and broad
class of each *actually visible* animal, passing/schooling/feeding/
interaction, framing, clarity, technical quality, narrative completeness,
and your own Keep/Skip/Unsure decision. Save reviews as you go, export JSON
before leaving the browser and import previous exports to resume.

To rerank with the human export:

    export IMPROVED="$WS/reports/klip-confirmed-animal-top-$(date +%Y%m%d-%H%M%S)-$$"
    python3 scripts/klip_rank_complete_clips_v1.py \
      --workspace "$WS" --pilot "$PILOT" --autoscout "$SCOUT" \
      --context-dir "$RUN" --owner-run "$OWNER" \
      --force-break-after-ms 23714 --secondary-feature-ms 37639 \
      --marine-study "$MARINE" \
      --marine-reviews "/absolute/path/to/exported-marine-labels.json" \
      --top 24 --output "$IMPROVED" --render
    open -a Safari "$IMPROVED/review.html"

Outputs remain source-SHA backed and local. The report contains an
animal-human-evidence summary and count of unverified model ray tags
refuted by explicit human absent-animal review.

## Beyond the existing 120 previews

Best-in-library wildlife discovery still requires full-interval sampling
of complete snorkeling source recordings, dense temporal encounter
segmentation and held-out visual comparisons. Use owner-confirmed wildlife
and framing exemplars for frozen SigLIP2 retrieval, then compare against
video-native embeddings. Score missed encounters, subject visibility,
continuity, quality and source-diverse recall. Do NOT replace the archive
with a newly advertised "perfect" model absent benchmark evidence.

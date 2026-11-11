# Goal-conditioned review calibration V2 (advisory, offline)

This revision uses existing, manifest-validated human preferences to route
**what a reviewed clip is for** before adding it to a sport highlight draft.
No raw camera media is opened or uploaded by routing. No ML model is trained.
No personal review export or raw video is stored in Git.

## Why separate editorial use from clip quality?

A visible failure can be useful for coaching, not action highlights. A
beautiful quiet view might be ideal for a scene transition. A nature encounter
may need comparison against alternate takes. Therefore, the human Keep label
does not establish permission or desirability for every collection.

Reviewed routing writes an advisory object to each verified packet clip:

- humanCurationV2.destination: sport_action, nature_encounter,
  scenic_transition, personal_memory, technique_analysis,
  needs_human_routing, not_selected or unresolved.
- eligibleForSportHighlightDraft: Only reviewed Keep clips routed to action,
  nature or scenery enter the existing sport draft reel.
- editorialPriority: A coarse human-language tier, never a trained video
  utility score or probability. Within its tier, available human quality
  ratings are tie-breakers; missing ratings are NOT zero-quality evidence.
- editActions: Review-controlled follow-ups: trim checks, adjacent context,
  alternate takes, underwater spans, possible postprocessing, video
  speed/orientation, and timestamp-matched external activity telemetry.
- humanTimeAnchorsMs: Human-reported source-relative peak and resolution,
  NOT automatic clip in/out points.
- additionalHumanReviewUses: Optional secondary collection hypotheses.
- autoTrimAuthorized, deleteAuthorized, publicationAuthorized: Always false.

The outputs indicate what the reviewer wrote, not what a model verified from
the video. Animal/species names mentioned in text are not visual recognition.

## Integration and reproducible invocation

This work builds on the unmerged verified sport-reel workflow. It does not
replace frozen scan receipts or repair invalid packets. Use the same verified
packet, matching review manifest, SHA-backed previews and input paths:

    python scripts/klip_campaign_reels.py \
      --workspace "$WS" \
      --campaign-dir "$CAMPAIGN_DIR" \
      --packet-dir "$PACKET_DIR" \
      --output-dir "$WS/reports/reviewed-sport-v2" \
      --mode reviewed --reviews "$REVIEW_JSON" \
      --seconds-per-sport 150 --max-clips-per-sport 8

Inspect reel-plan.json for humanRouting, additionalReviewedCollections,
humanEditorialFollowups (including requests attached to skipped cuts), and
manualOnlyDiscardHolds. The separate reel-review.html shows routes and
follow-up summaries for the clips selected into the sport preview. This is NOT a
new original-video scan or a final mastered reel. Use a new output directory
for every attempt.

Technique and personal-memory Keep decisions are preserved as record IDs,
source hashes, source-time bounds and editorial metadata instead of being
discarded or inserted into an athletic montage. Video remains accessible in
the frozen packet; there is not yet a separate off-reel preview gallery.

## Review interface correction

The blind review form now requires an explicit clip goal rather than silently
defaulting to athletic_skill. Existing exported reviews are unchanged.
A single goal cannot express all legitimate uses, so explicitly tagged
secondary uses remain proposals for human review, not inferred approvals.

## Qualification before learning / shipping

1. Freeze and reconcile missing or unresolved reviews. Verify the matching
   source packet and manifest SHA.
2. Measure route accuracy against separate goal-tagged human adjudication.
   Compare errors per activity and destination.
3. Use source-level held-out evaluation. Never split overlapping windows from
   the same source/event into training and evaluation. Where possible, separate
   trips and reviewers.
4. Measure top-K acceptance by collection, false routing, missed unique
   encounters, boundary completeness, repeated-scene diversity, time to
   acceptable edit and model abstention. Include uniform controls.
5. Validate source-relative, preview and decoded PTS before implementing
   automated event-core trims or context expansion.
6. Compare temporal methods and calibrated scoring against a frozen baseline
   only once those metrics exist. Do not claim trained or validated success.

## Known limitations

- Free-text routing is a fallible heuristic. Sarcasm, negation, multiple
  intentions and context can confuse it.
- Existing athletic_skill and observer-role labels may reflect UI defaults.
  Prior exports are not retroactively corrected.
- Missing, skipped and uncertain clips do not prove the source has no value.
- Delete requests create only manual-review flags, never filesystem actions.
- Speed/stat overlays require independently verified measurements,
  timestamp alignment, units and QC. Optical flow alone is not actual speed.
- No raw media changes or automatic publication.

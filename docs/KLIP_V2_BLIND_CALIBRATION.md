# E6: Klip V2 blind human calibration

The completed local Auto-Scout candidate scan produced 120 valid preview
windows (108 model-accepted and 12 schema-failed). Its proposals appear
overbroad: \`ski_park\` in 45/45 inferred skiing clips; \`blur\` in 102/108
accepted previews. These are ***not*** measured classifier accuracies.

This phase uses an existing frozen private pilot, frozen model receipts,
and local H.264 preview hardlinks. It does **not** decode camera originals,
call a model, edit/delete footage, change catalog labels or promote rejected
machine guesses into confirmed events.

## What this experiment measures

1. **Selection:** stratify the 120 previews into predicted ski park,
   ski-other, snorkel-ray, snorkel-other, other/unknown, and schema-rejected.
   Target 36 with explicit quotas, deterministic seed, and one-per-recording
   preference. The stratum is kept in private server-side sample manifest,
   **not shown on the review webpage**. Absent strata are reallocated.
2. **Blind review:** watch entire preview before seeing any model output.
   Label actual activity, visible scene categories, actual important quality
   defects, Keep/Skip/Unsure, reviewer certainty, optional event boundaries.
   Export JSON in the browser. Nothing leaves the user's computer.
3. **Scoring:** compare frozen predictions against explicit certain human
   clip labels. Score TP/FP/FN/TN, precision, recall and specificity for each
   category and filming flag. Failed model clips contribute to **coverage**
   and count of human-positive missed opportunities, not to automatic
   negative predictions. Unreviewed and uncertain observations cannot
   create false negatives.

Because we deliberately oversample model failures and particular classes,
**aggregate accuracy is not an unbiased estimate of archive-wide
performance**. This is diagnostic calibration of the observed 120 windows,
not whole-video discovery recall and not held-out preference-model training.

## Run from a separate clean worktree

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export PILOT="$WS/reports/klip-v2-pilot-20261114-153934-88998"
    export EXP="$WS/reports/klip-v2-autoscout-20261115-130506-89529"
    export CAL="$WS/reports/klip-blind-calibration-$(date +%Y%m%d-%H%M%S)-$$"

    python scripts/klip_blind_calibration_v2.py prepare \
      --workspace "$WS" --pilot "$PILOT" --experiment "$EXP" \
      --output "$CAL" --size 36

    open "$CAL/review.html"

Review clips in order and **Export private JSON labels**. Browser downloads
default to \`~/Downloads/klip-blind-owner-<sample-sha>.json\`. Save this file.

    python scripts/klip_blind_calibration_v2.py score \
      --workspace "$WS" --pilot "$PILOT" --experiment "$EXP" \
      --output "$CAL" --labels "/absolute/path/to/exported-labels.json"

The full detailed report appears in \`$CAL/calibration-score.json\`.

Only the owner labels with certainty "certain" enter binary precision/recall;
"unknown" is excluded from those metrics. Source ID, record ID, sample
manifest hash and original receipt fingerprint are checked before scoring.
Unreviewed clips stay unreviewed. The score is written once and cannot
silently replace prior results.

## Next decision gates

- Confirm whether the model can actually distinguish park features from
  generic ski footage and whether its ray sightings are real animals.
- Distinguish normal action-camera motion from genuinely unpleasant shake or
  blur. A model that labels virtually everything blurry should not influence
  editing.
- Count genuine human Keep moments among 12 model failures.
- Only then consider training a tiny positive/negative exemplar ranker using
  frozen SigLIP2 features, with outing-based test splits.
- Use video-native temporal models only if preexisting visual retrieval
  misses complete jumps, rail exits, or underwater interactions.

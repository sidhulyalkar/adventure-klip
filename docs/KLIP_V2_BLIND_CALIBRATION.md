# Klip V2: First blind human calibration pilot

The Auto-Scout pass produced 108 validated model outputs and 12 incompatible
model-category responses across 120 cached source-verified previews. It proposed
\`ski_park\` in 45/45 skiing clips and \`blur\` in 102/108 accepted clips.
These are red flags for overprediction, not ground-truth error rates.

This new study tests whether proposed categories and highlight priorities
are useful, **before** changing the editor, retraining, or scanning originals.

## How the next experiment works

- Deterministically select a 36-clip stratified sample. Target pools are
  unusual ski features, other skiing, ray-like marine scenes, other marine,
  broad biking, unknown activity, and some of the 12 rejected model outputs.
- Strictly reverify the original frozen packet, all receipt identities and
  proxy SHA-256 hashes.
- Hard-link only selected existing H264 proxies into an isolated private
  report. No decoding needed to prepare the study.
- Show a local *prediction-blind* browser review page. Explicitly record
  observed activity, multi-label scene categories, Keep/Skip/Unsure, visible
  quality problems, and optional event boundaries. Owner observations start
  empty and are not populated by the local model.
- Export those owner judgments to a JSON download. Nothing is uploaded or
  stored permanently in the browser unless the user exports.
- Score model category and quality precision/recall **only on explicitly
  human-reviewed, known-activity clips**. Show sport confusion and actual
  Keep/Skip by model priority. Unreviewed footage is not negative.

This is a stratified convenience sample, not archive-wide accuracy or
cross-outing validation. Selection is based on model categories (withheld
from the reviewer) and intentionally oversamples uncertain cases. A more
rigorous later study must hold entire independent outings out.

## Run from the new calibration branch checkout

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export PILOT="$WS/reports/klip-v2-pilot-20261114-153934-88998"
    export EXP="$WS/reports/klip-v2-autoscout-20261115-130506-89529"
    export STUDY="$WS/reports/klip-v2-blind-calibration-$(date +%Y%m%d-%H%M%S)-$$"

    python scripts/klip_blind_calibration_v2.py prepare \
      --workspace "$WS" --pilot "$PILOT" --experiment "$EXP" \
      --output "$STUDY" --count 36
    open "$STUDY/blind-review.html"

Review and save as many as possible. Export JSON frequently; keep files
private. The exported file will be in your browser's Downloads directory.

To score after exporting reviews:

    python scripts/klip_blind_calibration_v2.py evaluate \
      --study "$STUDY" \
      --reviews "$HOME/Downloads/klip-calibration-review-<sample-hash>.json"

Inspect \`$STUDY/calibration-metrics.json\`. It reports verified-human sample
statistics but **does not** update catalog labels, change the original
Auto-Scout receipts, mutate clip rankings, or render a new reel.

## Next model decision

Only after reviewing confusion/false positives should we compare the
read-only frozen SigLIP2 exemplar retrieval baseline against model-only
scouting. A frozen video encoder is justified if it measurably improves
held-out action/encounter recall, not simply because its general
benchmark score is higher.

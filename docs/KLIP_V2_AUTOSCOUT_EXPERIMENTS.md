# Klip V2 Auto-Scout E3–E5: source-safe category and recording-quality discovery

## Problem identified by the first pilot

The first Klip V2 private experiment produced 120 checksum-qualified playable
proxies, but **0 owner annotations**, 0 ski collections, 0 specialist snorkel
collections, and 1 generic snorkel candidate. This is a failure of **semantic
coverage and labels**, not proof that the archive lacks park tricks, good tree
lines or wildlife.

This experiment bridges candidate discovery and human review using only those
already verified local H.264 proxies. It DOES NOT scan the original SSD or
label its full 232-source archive; it is a study over the 120 selected windows.

## Three linked experiments

- **E3: Seven-frame local semantic auto-scout.** Ollama \`gemma3:4b\` receives
  seven chronological separate JPEG images per proxy, each sampled by the
  existing bounded FFmpeg utility. It returns a *proposed* broad activity,
  zero or more ski/snorkel/bike category candidates, frame-grounded evidence,
  visible quality clues and an abstention/uncertainty field.
- **E4: Numeric visual telemetry.** Sample tiny 2-fps grayscale frames,
  compute mean image brightness, unusually dark/bright fraction, and mean/max
  frame-to-frame pixel difference. Pixel motion is **not measured skier
  speed, camera shake, stunt difficulty, or clip value**. These are cues for
  inspection; do not set automatic trim boundaries from them.
- **E5: Source-diverse review ranking.** Interleave clips from different source
  recordings across \`review_first\`, \`investigate\`, \`routine\` tiers. Create an
  offline private HTML video deck and a JSON output for the next blind review.
  Nothing is human ground truth until manually checked. Preserve controls and
  unknown/quiet clips, and avoid suggesting that the resulting order is an
  accuracy-improving learned ranker.

The local Ollama model tag may be updated without a fixed immutable digest;
results are not comparable across model revisions without pinning the digest.

## Fresh output and resumption

Run in the separate feature checkout with source-local Python environment.
The first command performs only SHA validation, without model inference.

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export PILOT="$WS/reports/klip-v2-pilot-20261114-153934-88998"
    export EXP="$WS/reports/klip-v2-autoscout-$(date +%Y%m%d-%H%M%S)-$$"

    python scripts/klip_auto_scout_experiment_v2.py \
      --workspace "$WS" --pilot "$PILOT" --output "$EXP" \
      --max-items 24

Requires Ollama running on \`127.0.0.1:11434\` with an installed image-capable
\`gemma3:4b\` model and FFmpeg/FFprobe available:

    caffeinate -i python scripts/klip_auto_scout_experiment_v2.py \
      --workspace "$WS" --pilot "$PILOT" --output "$EXP" \
      --model gemma3:4b --max-items 24 --report-every 8 --run

Inspect summary.json, review.html, and receipts before expansion. To finish all
120 source-verified candidate proxies without repeating successes:

    caffeinate -i python scripts/klip_auto_scout_experiment_v2.py \
      --workspace "$WS" --pilot "$PILOT" --output "$EXP" \
      --model gemma3:4b --max-items 120 --report-every 8 --run --resume

A failed proxy remains a failed evidence item rather than being declared
uninteresting. Only add \`--retry-failures\` after determining its cause.
Never delete receipts or replace the original packet to force a retry.

## Outputs (private)

- \`experiment-state.json\`: frozen pilot manifest and model/policy fingerprints
- \`receipts/<recordId>.json\`: one checksum-bound checkpoint per proxy
- \`summary.json\`: activity/category/quality counts and ranked source-diverse
  review candidates, including unprocessed and failed coverage
- \`review.html\`: offline gallery with verified source-linked preview media
- \`media/*.mp4\`: hard links to SHA-verified packet proxies, not copied or
  newly encoded source video

Never commit workspace reports, original video paths or local model outputs.

## Follow-up validation

For a small, representative sample, review E3 labels blind against actual
continuous playback, stratified by original catalog hints and coverage controls.
Record false ski-park, false ray, missed wildlife and missed tree-run events.
Use owner-verified event start/end and quality defect spans in the existing
V2 Label Studio to enable safe edit proposals. Then compare original,
heuristic and smart-trim versions on held-out outings. Do not import AI
suggestions directly into owner annotations or auto-skip an unseen event.

The next feature should show these candidate suggestions alongside the owner
label page with explicit accept/reject controls and calibration summaries.

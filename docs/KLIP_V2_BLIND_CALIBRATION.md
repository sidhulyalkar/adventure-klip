# Klip V2 / E6: blind human calibration

The completed Auto-Scout run attempted 120 SHA-verified preview clips.
108 model responses were accepted; 12 failed schema validation. Model
suggestions included ski_park for all 45 model-classified ski clips and
blur in 102 of 108 accepted responses. These are **not accuracy figures**.

This experiment uses the existing and tested:
- src/adventure_intelligence/calibration_v2.py (sampling and scoring)
- src/adventure_intelligence/calibration_studio_v2.py (offline, blind review)
- scripts/klip_blind_calibration_v2.py (prepare / evaluate)

Do not introduce a second calibration package for this stage.

## Steps

1. Validate the frozen source pilot and all model receipt SHA identities.
2. Create a deterministic 36-preview, source-diverse **stratified** sample,
   including park and ordinary ski, marine-ray and other underwater,
   biking/unknown, and model-failed controls. Empty buckets are redistributed.
3. Open an offline local review page hiding all model predictions. Watch
   clips and explicitly save a human activity, category labels, quality
   defects, Keep/Skip/Unsure and optional complete event bounds.
4. Export human review JSON and evaluate it against the frozen predictions.
   Missing reviews, unknown sport labels and model failures are never
   automatically treated as negatives. Keep clips on model failures are
   reported as lost model coverage, not dismissed highlights.
5. Generate descriptive metrics from this stratified sample, not an unbiased
   estimate of archive-wide accuracy or action-detection recall.

## Run on the existing Mac private workspace

The repository checkout used earlier is expected at
$HOME/Documents/Projects/adventure-klip.

    bash <<'BASH'
    set -euo pipefail
    REPO="$HOME/Documents/Projects/adventure-klip"
    BRANCH="experiment/klip-v2-blind-calibration"
    git -C "$REPO" fetch --no-tags \
      "https://github.com/sidhulyalkar/adventure-klip.git" "refs/heads/$BRANCH"
    SHA="$(git -C "$REPO" rev-parse 'FETCH_HEAD^{commit}')"
    STUDY="$(dirname "$REPO")/klip-blind-study-$(date +%Y%m%d-%H%M%S)-$$"
    git -C "$REPO" worktree add --detach "$STUDY" "$SHA"
    cd "$STUDY"
    if [[ -f "$REPO/.venv/bin/activate" ]]; then
      source "$REPO/.venv/bin/activate"
    fi
    export PYTHONPATH="$PWD/src"
    export WS="$HOME/AdventureAI-clean.QJnopt"
    export PILOT="$WS/reports/klip-v2-pilot-20261114-153934-88998"
    export EXP="$WS/reports/klip-v2-autoscout-20261115-130506-89529"
    export CAL="$WS/reports/klip-blind-calibration-$(date +%Y%m%d-%H%M%S)-$$"
    python3 -m pytest -q tests/test_calibration_v2.py
    python3 scripts/klip_blind_calibration_v2.py prepare \
      --workspace "$WS" --pilot "$PILOT" --experiment "$EXP" \
      --output "$CAL" --count 36
    printf 'Calibration study: %s\n' "$CAL"
    open "$CAL/blind-review.html"
    BASH

The private study holds sample-manifest.json, sample-manifest.sha256,
blind-review.html, and hardlinks to local verified proxy MP4s.
It does not call Ollama or read original DJI videos.

## After manually completing reviews

Click **Save reviewed clip** as you proceed, and **Export review JSON**.
The downloaded filename begins with:
klip-calibration-review-<sample-id>.json

From the same study checkout, point the command to the exact downloaded file:

    python3 scripts/klip_blind_calibration_v2.py evaluate \
      --study "$CAL" \
      --reviews "/absolute/path/to/klip-calibration-review-....json"

Then inspect:

    cat "$CAL/calibration-metrics.json"

The score command detects an accidentally edited frozen sample manifest,
mismatched sample/source identity, duplicate review, invalid tags or
out-of-bounds event times, and refuses to overwrite existing metrics.

## Human review guidelines

- Ski park: mark a specific visible box, rail, jump or park setting, not
  generic snow, lift, groomer or merely presumed trick completion.
- Marine ray: verify a visible ray-like subject rather than accepting
  reef scenery or an indeterminate fish silhouette as confirmed.
- Blur and shake: mark only meaningful degradation in watchability.
  Fast head-mounted POV is not automatically defective.
- Keep/Skip/Unsure: judge event quality independently from image quality;
  compelling wildlife footage can still need editing.
- Give special attention to model-failed clips, which are potential
  false-negative highlights when machine coverage is incomplete.

This intentionally selected review set is a calibration experiment, not
validated generalization to independent trips or to the full recording
archive. Source originals, catalog decisions and publication gates
remain unchanged.

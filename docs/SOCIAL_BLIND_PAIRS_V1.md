# Social Lens: blinded A/B clip preference pilot

## Goal

Find out whether Klip's existing **relative candidate rankings** reflect what you
actually like watching and saving. This is not an Instagram/TikTok model yet.
It is a *small, controlled and blinded* preference benchmark so future external
training cannot quietly define your personal ground truth.

Two source-distinct candidate windows from the **same sport** are shown without
Klip scores, reasons, recommendations or model attribution. Choose:

- **A**, **B**, **Tie**, or **Neither**
- Optional reason tags and free-text notes
- Export the browser's local JSON at any point, and import it to resume

The report records score rank disagreements and feature evidence deltas only
**after** the offline review file has been exported. It does not train or
change weights, upload anything, write a source, or modify your Klip reviews.

## Inputs and exact versions

This CLI needs three already-generated artifacts, all created for the same
study and exact personal feature template:

1. `archive-value-study-pilot-v1/study.json` (frozen Klip ranking)
2. `social-rubric-balanced40-v1/feature-template.json` (personal candidates)
3. `temporal-proxy40-v1/temporal-evidence.json` (local H264 preview analysis
   run with `--run --prepare-previews` using **that exact feature file**)

Important: a temporal plan without `--run` or a temporal report from a
*different* feature template will not qualify. The manifest SHA-256 check
blocks accidental cross-study joins.

Eligible pairs require two **different SHA-256 source assets** of the same
sport and independently SHA/receipt-verified local H.264 previews. A pair
does not imply independent trips, mounts or environments.

## Mac commands

From a clean working tree:

```bash
git fetch origin
git switch --track origin/feat/social-blind-pair-preference-v1
python -m pip install -e '.[dev]'

export WS="$HOME/AdventureAI-clean.QJnopt"
export STUDY="$WS/reports/archive-value-study-pilot-v1/study.json"
export FEATURES="$WS/reports/social-rubric-balanced40-v1/feature-template.json"
export TEMPORAL="$WS/reports/temporal-proxy40-v1/temporal-evidence.json"
```

If you haven't yet generated the balanced feature set and full 40-clip
temporal analysis, run those first (use **new** output folders):

```bash
klip --workspace "$WS" social-lens-feature-template \
  --study "$STUDY" --limit 40 \
  --output-dir "$WS/reports/social-rubric-balanced40-v1"

klip --workspace "$WS" social-lens-temporal \
  --features "$FEATURES" --limit 40 --run --prepare-previews \
  --output-dir "$WS/reports/temporal-proxy40-v1"
```

The second command opts in to read originals locally to prepare missing
verified private previews; it does not modify those source files. You may
instead attempt `--run` alone to use previously verified previews.

Create up to 12 pairs for the first validation:

```bash
klip --workspace "$WS" social-lens-pair-review \
  --study "$STUDY" \
  --features "$FEATURES" \
  --temporal "$TEMPORAL" \
  --max-pairs 12 --seed 104 \
  --output-dir "$WS/reports/social-pairs12-v1"

open "$WS/reports/social-pairs12-v1/index.html"
```

Use mouse or **1** for A, **2** for B, **0** for Tie, **-** for Neither,
left/right arrows for navigation. Each side independently plays a **muted**
cached H.264 derivative, because Studio preview generation strips audio.
Sound and verified real-time speed cannot be judged from this particular
comparison. Filming quality is also affected by camera geometry, lighting
and environmental context.

The offline report contains locally playable `media/` links or copies of
the **verified proxy only**. On a local filesystem, the script prefers
space-efficient hardlinks, falls back to copying bounded derivatives,
and verifies contents against the original receipt. No original footage is
linked, copied or deleted.

Press **Export JSON reviews** periodically. The browser generates a
download named `klip-paired-review-<SHA prefix>.json`. Later, use
**Resume from JSON** to restore votes in the same frozen pair study.
No browser cloud storage or network API is used.

After reviewing:

```bash
klip --workspace "$WS" social-lens-pair-results \
  --manifest "$WS/reports/social-pairs12-v1/pairs.json" \
  --reviews "$HOME/Downloads/klip-paired-review-ACTUAL-SHA.json" \
  --output-dir "$WS/reports/social-pair-results12-v1"
```

Replace the `--reviews` filename with your actual download. Output
`results.json` reports per-sport agreement denominators, decisive votes,
ties/neither, stated reasons, and which uncalibrated temporal cues differ
in pairs where your choice disagrees with Klip.

All new output folders must be absent beforehand. If the pair count is
zero, this is a coverage/eligibility issue, not a lack of interesting
video: sample more distinct source assets in that sport. A first 8-clip
test may have too few eligible sources.

## Interpretation and scientific boundaries

- Only **qualified candidate windows** are compared. The experiment does
  not recover clips Klip never proposed and therefore cannot measure
  full-archive recall.
- Pair creation deliberately includes **large rank contrasts** and
  **close-ranked difficult comparisons**. It is not a randomized population
  sample. Track pair-type and sport denominators before generalizing.
- A clip may appear in up to two pairs, and pairs may share trips.
  Reported agreement fractions are descriptive, not a calibrated
  preference probability or an independent-sample confidence interval.
- Human Tie and Neither are not automatically bad source footage. Both
  choices should be reported separately and never mapped to Skip.
- Score disagreements are more valuable as an **active review queue**
  than as immediate instructions to change weights.
- The video playback page is **blinded to scores**. The local
  `pairs.json` intentionally stores the frozen scores for *post-review*
  analysis; avoid opening it until after completing the choices.
- This is your personal taste calibration, not social platform success.
  External videos, creator rights, engagement metrics and separate
  social-trained model evaluations are future work.

## Best follow-up

Use the first 12–20 A/B decisions to identify *why* Klip diverges.
Collect examples of false skipped underwater wildlife, low-motion but
beautiful views, useful camera motion vs unstable mounts, long coherent
ski turns, uninteresting high-speed transitions and duplicated assets.
Then prioritize a temporally continuous, source-verified optical-flow and
subject framing evaluator that predicts the **human difference**, not
merely image change.

Freeze a second held-out outing review before any preference reweighting,
and keep blind pair judgments separate from past AI suggestions.

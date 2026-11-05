# Social Lens shared temporal evidence pilot v1

## What this solves

The first Social Lens comparison needed ten human-scored feature dimensions.
The pilot personal review template contained **300 candidate windows, zero
observed features**. The external engagement corpus is separate, and has
not been collected. A user must not be asked to fill thousands of personal
rubric numbers just to test whether Klip can measure *some* temporal facts.

This command adds **bounded measurements from the actual clip duration**
rather than an ungrounded three-frame AI label. The report is still not
an automatically validated `action-raw-human-rubric-v1` feature manifest.
It does **not** train the external social head, fabricate external examples,
predict social engagement or rewrite user labels.

## What is directly measured

For each source SHA + start/end interval:

- Verified private Studio H.264 proxy sampled at 4 fps, 128×72 grayscale:
  median brightness, low/high luminance pixel fraction, spatial texture/
  gradient energy, frame-to-frame pixel change, high-change temporal runs,
  and early/mid/late change sequence.
- Completed, checksum-verified SigLIP2 frame embeddings, where available:
  within-window temporal sample count, largest gap, consecutive cosine
  similarity and actual resolved model identity. Sparse frames abstain.
- Existing checksum-verified motion and camera-audio features:
  window-relative sample counts, visual change, RMS, clipping and
  low-frequency acoustic energy. Absence stays null.

**These are measurements, not judgements:**

- Large grayscale changes can be camera shake, fast pans, a jump cut,
  moving wildlife or actual forward travel. No absolute km/h/mph.
- Edge gradients are affected by water particles, lighting, snow and
  compression; they are not confirmed crispness/focus.
- Bright snow and underwater darkness are expected across many clips.
  Bright/dark fractions alone do not prove bad exposure.
- Consecutive image embedding dissimilarity does not prove rare wildlife,
  novelty, scene changes or action quality.
- Subject location, correct crop, horizon roll, obstacle technique,
  continuous animal behavior, and completed trick/action remain unverified.
- The Studio H.264 preview intentionally contains no audio; audio is
  sourced only from separate verified source-audio caches.

Once the proxy evidence is validated against independent human ratings
and sport-specific examples, a future version can learn a mapping into
the shared Social Lens input space. Versioned provenance and a
reproducible holdout will be required.

## Run on your Mac

From a clean worktree:

```bash
git status --short
git fetch origin
git switch --track origin/feat/social-lens-temporal-evidence-v1
python -m pip install -e '.[dev]'

export WS="$HOME/AdventureAI-clean.QJnopt"
export FEATURES="$WS/reports/social-rubric-template-v1/feature-template.json"
```

The existing 300-clip template remains valid. `--limit 8` uses the
first eight entries. For a better sport/rank-balanced pilot, optionally
create a *separate* 40-interval template:

```bash
klip --workspace "$WS" social-lens-feature-template \
  --study "$WS/reports/archive-value-study-pilot-v1/study.json" \
  --limit 40 \
  --output-dir "$WS/reports/social-rubric-balanced40-v1"

export FEATURES="$WS/reports/social-rubric-balanced40-v1/feature-template.json"
```

### Stage 1: inspect evidence coverage without decoding

```bash
klip --workspace "$WS" social-lens-temporal \
  --features "$FEATURES" \
  --limit 8 \
  --output-dir "$WS/reports/temporal-coverage8-v1"

open "$WS/reports/temporal-coverage8-v1/index.html"
```

This checks trusted semantic/audio/motion caches, and whether a
checksum-verified Studio preview already exists for each exact interval.
It does not read an original video or run FFmpeg.

### Stage 2: analyze existing verified previews only

```bash
klip --workspace "$WS" social-lens-temporal \
  --features "$FEATURES" \
  --limit 8 \
  --run \
  --output-dir "$WS/reports/temporal-cached8-v1"

open "$WS/reports/temporal-cached8-v1/index.html"
```

If all eight previews are missing, this may correctly yield
`measured: 0` and eight `missing_verified_preview` rows.
**Do not fill missing measurements with zero.**

### Stage 3: opt in to preparing missing previews from originals

Only once you approve a source-read and local derivative operation:

```bash
klip --workspace "$WS" social-lens-temporal \
  --features "$FEATURES" \
  --limit 8 \
  --run \
  --prepare-previews \
  --output-dir "$WS/reports/temporal-proxy8-v1"

open "$WS/reports/temporal-proxy8-v1/index.html"
```

On absent previews, this invokes the same **fully source-SHA-verified**
Studio H.264 preview function used for the local judge. It hashes
the original read-only video, creates a private bounded 960px H.264
derivative and then extracts fixed-size grayscale frame measurements
from that verified derivative. New derivatives live in
`$WS/reports/studio-preview-cache-v1/`, not alongside your camera files.

Source hashing may read large files. It does not modify, replace,
upload or delete originals. Source-check failures are reported per
clip and never silently converted into `skip`.

### Stage 4: expand only after reviewing the first eight

```bash
klip --workspace "$WS" social-lens-temporal \
  --features "$FEATURES" \
  --limit 40 \
  --run \
  --prepare-previews \
  --output-dir "$WS/reports/temporal-proxy40-v1"
```

The first 40 IDs are sampled by the feature-template generator across
sport and ranking tiers if the balanced40 file was used. Each command
requires a **new** output directory. User data remains local.
The output contains `temporal-evidence.json` and `index.html`.

## Qualification gates before making ranking claims

1. Confirm the footage is genuinely different across sport, capture
   environment, and valuable/missed moments.
2. Blind-rate **20–40** sampled intervals in Studio, including low-ranked
   systematic controls and at least two different camera mounting
   conditions. The human review is not generated by this extractor.
3. Check whether high frame-change intervals are actually good, or
   camera disturbance, poor underwater visibility, or useless pans.
4. Check that dark/marine and bright/snow scenes are not systematically
   demoted solely due to luminance, and test action continuity over
   a full episode rather than only 2–3 stills.
5. Add a calibrated temporal subject/crop/flow extractor and use
   validated feature mappings on **held-out outings**. Keep the
   externally permissioned creator training corpus wholly separate.
6. Build a blinded pairwise A/B Klip versus Social Lens comparison
   only after both heads can evaluate the same observed features.

This v1 deliberately adds **no learned score** and writes zero values
to the existing ten personal human rubric fields. It is an evidence
qualification instrument, not a viral clip predictor.

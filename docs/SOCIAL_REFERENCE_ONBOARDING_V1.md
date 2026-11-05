# Social Lens external corpus: why the training command failed and what to do

## What happened on the user's Mac

The following command succeeded:

```bash
klip --workspace "$WS" social-lens-feature-template \
  --study "$WS/reports/archive-value-study-pilot-v1/study.json" \
  --output-dir "$WS/reports/social-rubric-template-v1"
```

It produced **300 Klip candidate identities but zero observed feature values**.
These are private action-camera candidates to compare later, not examples of
independently permissioned successful Instagram or TikTok posts.

The next command failed because the requested external corpus file does not
exist, not because the model failed:

```bash
klip --workspace "$WS" social-lens-train \
  --corpus "$WS/reports/social-reference-authorized-v1.json" \
  --output-dir "$WS/reports/social-action-head-v1"
```

**Neither Klip nor the old social-lens-feature-template command creates an
external reference corpus.** Social-media engagement metadata and
video-training permission cannot be inferred from the private Klip library.
We must not invent high-performing videos, engagement numbers, or rights grants.

## New, explicit zero-fabrication recovery commands

Stop any running Studio and switch from a clean worktree to
`feat/social-lens-corpus-onboarding-v1`. Reinstall editable package.

```bash
git status --short
git fetch origin
git switch --track origin/feat/social-lens-corpus-onboarding-v1
python -m pip install -e '.[dev]'

export WS="$HOME/AdventureAI-clean.QJnopt"
export CORPUS="$WS/reports/social-reference-authorized-v1.json"

# Reads metadata only; missing file does not invoke a model or return a stack trace.
klip --workspace "$WS" social-lens-preflight --corpus "$CORPUS"

# Only create the file if it does not already exist.
klip --workspace "$WS" social-lens-corpus-init --output "$CORPUS"

# Expected status: awaiting_permissioned_examples; trainingReady=false.
klip --workspace "$WS" social-lens-preflight --corpus "$CORPUS"
```

The created file has exactly:

```json
{
  "version": "licensed-social-action-features-v1",
  "items": []
}
```

Do not run `social-lens-train` again until permitted real examples exist.
The new training error explains the missing data and suggests preflight.
The preflight also checks license declarations, creator groups, balanced
matched pairs, feature completeness and held-out creators before fitting.
License declarations remain **self-attested** and require independent review.

## A smaller, more informative Klip-side pilot

The existing 300-entry template is valid and remains untouched. Avoid
manually assigning 3,000 raw ratings before we have qualified an extractor.
Build a separate **40-clip** template; small samples are now balanced across
ski, bike, marine, unknown groups and high/mid/low Klip ranks:

```bash
klip --workspace "$WS" social-lens-feature-template \
  --study "$WS/reports/archive-value-study-pilot-v1/study.json" \
  --limit 40 \
  --output-dir "$WS/reports/social-rubric-balanced40-v1"
```

This writes null features, NOT inferred or invented ratings. We need a
human UI, and eventually a validated shared temporal feature encoder,
to actually fill them. Treat each clip as a source-relative time window
with an independent clip/source SHA, not a whole recording verdict.

## Real external reference collection: recommended staged approach

### Phase A: lawful, creator-consented reference dataset

1. Recruit a small set of action sports creators or collaborators willing to
   grant explicit ML-training/use rights for particular original/edited
   videos and permission to use metric snapshots. Prefer original raw action
   sequences as well as their posted versions so filming quality and
   packaging/editing effects can be separated.
2. Include ordinary and *unsuccessful* uploads, not only viral favorites.
   Match each sport and creator in time and duration; avoid comparing
   someone else's huge audience with a beginner account on raw views.
3. Store stable pseudonymous creator and content-family IDs, platform,
   post/observation dates, organic-versus-paid status, views, likes, shares,
   comments, saves (only when directly observed) and documented media rights.
   Keep usernames, account tokens and identifiable creator records out of
   trained model artifacts. Revoke/expire records if terms require it.
4. Apply the *same* defined raw-footage rubric to permitted external videos
   and private Klip clips. If any dimension is unassessed, preserve null and
   abstain. Use temporally complete 20–30s candidate windows, not a
   three-still vision guess.
5. First qualified study: 100–200 external clips from enough independent
   creators to supply both training and held-out comparable pairs. For useful
   generalization, target several hundred with diverse cameras, conditions,
   editors and sports. The **v1 minimum of six records is a software
   validation threshold, not a scientific adequacy threshold**.

### Phase B: official research access (optional and purpose-restricted)

- TikTok's official Research Tools require eligibility, institutional or
  nonprofit affiliation in supported jurisdictions, research approval and
  an eligible noncommercial/public-interest purpose:
  https://developers.tiktok.com/products/research-api/
- TikTok's data codebook gives the available metadata scope:
  https://developers.tiktok.com/docs/en/research-api-codebook
- Meta Content Library/API supports controlled research access to eligible
  public Instagram creator/business content:
  https://about.fb.com/news/2023/11/new-tools-to-support-independent-research/

**Official access to public engagement data does not automatically authorize
downloading or training video encoders on creators' footage.** Terms and
purpose restrictions must be checked before using either endpoint. Do not
scrape social networks, circumvent authentication or repurpose restricted
research exports for commercial model training.

### Phase C: reusable raw-video temporal feature extractor

The priority engineering bottleneck is not the current ranker's coefficients.
The Klip sample has 300 candidate windows with `observedFeatures=0`, so
there is nothing for the social head to score. Build a **single, consistent
extractor** for both permitted creator footage and Klip's local media:

- Source-verified, bounded video window/proxy; original never modified.
- Per-window subject visibility and center/crop recoverability, with
  accurate horizon/gyro analysis only where the geometry supports it.
- Actual temporal flow, trajectory continuity and action choreography
  across the full 20–30s window, not just 3–7 snapshots.
- Scene segmentation, early hook, escalation, climax and departure;
  animal behavior versus empty water or poorly shot subject.
- Provenance for model checkpoint, preprocessing, temporal sampling,
  supported sport, uncertainty and residual domain shift.
- Acoustic wind and source-motion features remain candidate cues, never mph.

Do not silently treat edited social-post composition, captions, music,
hashtags or pacing as raw-camera filming quality. Train a **separate packaging
head** if those creator choices matter.

### Phase D: actual model-versus-Klip experiment

- Train only on creator-disjoint external records. Keep complete creator/video
  families and post-date leakage separated.
- Freeze the social head, baseline Klip policy and feature extractor.
- Evaluate on unseen outings, comparing **the same** Klip candidate windows.
- Blind-review Social Lens promotions, Klip promotions and exploratory
  low-ranked controls, with A/B/Tie/Neither and reason tags.
- Compare human Keep@20, missed valuable controls, preservation of
  exceptional underwater encounters, duplicate fraction, event boundary
  quality and time spent per useful accepted moment.
- If the external head has no convincing gain, keep it as a second opinion.
  Social engagement does not replace the user's personal preferences.

## Operating and safety boundaries

- An **empty reference JSON** is a useful initialized file, not model data.
- Rights in the input manifest are assertions, not automatic legal proofs.
- `social-lens-preflight` does not train, decode, request APIs or access videos.
- `social-lens-feature-template --limit 40` preserves all missing features
  as null and samples across score tiers for a more useful pilot.
- The local user's source footage, human judgments and selection history
  are never automatically uploaded or deleted by these commands.

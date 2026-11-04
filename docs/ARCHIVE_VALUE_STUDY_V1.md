# Archive-wide Clip Value Study v1

## Purpose

Klip's first 16-interval pilot and cached Gemma 3 judgment compare clips
too locally. An attractive standalone clip can be redundant among 70 identical
dives; a fast ski run can be visually compelling for 28 seconds despite
a weak three-frame answer. This study draws a *much larger, source-backed*
sample across the existing catalog and makes review labels independent
from the model's suggestions.

This is the next **measurement** required before training an "accurate"
ranking system. It is not an automatic best-of-archive assertion and does
not transcode/export/delete any original file.

## Outputs

`klip archive-value-study` reads SQLite metadata and already verified
feature/semantic caches only. It writes:

- `study.json`: coverage accounting, candidate hypotheses, per-sport
  ranked shortlists, source-relative redundant clusters, limitations.
- `assignments.json`: frozen, token-validated Studio-compatible blind
  benchmark with candidate windows plus randomized non-overlapping
  source-time controls. All IDs derive from SHA-256 original source identity.
- `index.html`: offline summary of candidate hypotheses, never an
  assertion of verified footage content.

Where existing episode and semantic caches are absent, that portion
cannot be claimed negative. A recording with no cached episode can
still contribute a control for human exploration. The selected controls
are *not* an unbiased archive census: source selection is stratified
and candidate-overlap exclusion changes their inclusion distribution.

### Clip proposal timing

| Sport | Default window | What to verify |
|---|---:|---|
| Ski | 28 s | Sustained descent, speed corroboration, turning/terrain, approach and exit |
| Bike | 24 s | Technical feature approach, execution and recovery, not camera jolts |
| Snorkeling | 20 s | Visible subject, underwater habitat, animal encounter and readable departure |
| Unknown | 18 s | Determine actual activity; false negatives matter |

This timing is a human-review *proposal*, not an automatic edit
boundary. Later temporal refinement should adjust each candidate to
scene changes and event boundaries, with pre/post roll.

### Ranking ingredients and limits

1. Existing `editorial_candidates` combines episode proposals, completed
   activity context, motion, acoustic-change context and human-curated
   exclusions. Its score is only a heuristic.
2. `scene_atlas.enrich_candidates` reuses verified homogeneous SigLIP2
   embeddings and eligible prior reviewed exemplars. No new model
   inference, video decoding or source SHA reads occur in this command.
3. `diverse_representatives` produces a per-sport shortlist, with
   near-duplicate suppression only in the shortlist. No original
   is excluded or deleted.
4. Candidate/control sampling mixes the available sports and unknown
   activity pool, with deterministic seed and recorded source identity.
5. The blind review hides candidate/control roles, model judgments,
   their scores and selection reasons from the Studio browser view.
   The manifest remains inspectable by the workspace owner; this is
   practical review blinding, not a controlled double-blind trial.
6. Current prior human reviews can influence heuristic scores. This
   is *not* an unbiased, held-out scientific accuracy estimate.
   True holdouts need confirmed outing/session IDs and frozen training
   vs evaluation trips before any preference learning or calibration.

No file deletion, wildlife species verification, GPS/true-speed inference,
or claim that the analysis saw every frame is permitted.

## Run the next large analysis on the Mac

Finish your current Studio session with Ctrl+C and make sure worktree
changes are committed or stashed before switching branches.

```bash
git status --short
git fetch origin
git switch --track origin/feat/archive-value-study-v1
python -m pip install -e '.[dev]'

export WS="$HOME/AdventureAI-clean.QJnopt"
```

If already tracking the new branch, use
`git switch feat/archive-value-study-v1`.

Start with a **64-source, 96-review** qualification sample:

```bash
klip --workspace "$WS" archive-value-study \
  --max-assets 64 \
  --review-limit 96 \
  --seed 104 \
  --max-reel-seconds 900 \
  --output-dir "$WS/reports/archive-value-study-pilot-v1"

open "$WS/reports/archive-value-study-pilot-v1/index.html"
```

When the qualification report looks correct, use **every accessible indexed
recording** (`--max-assets 0`) in a **new** output folder. This remains
cached-only, but it can read and verify large feature-cache documents
and perform a substantial number of similarity comparisons:

```bash
klip --workspace "$WS" archive-value-study \
  --max-assets 0 \
  --review-limit 192 \
  --seed 104 \
  --max-reel-seconds 900 \
  --output-dir "$WS/reports/archive-value-study-all-v1"

open "$WS/reports/archive-value-study-all-v1/index.html"
```

The 192-interval set is a **human review budget**, not a promise that
every clip has been covered or evaluated.

Open the pilot in *blind* Studio, **without** the model flag:

```bash
klip --workspace "$WS" studio \
  --benchmark "$WS/reports/archive-value-study-pilot-v1/assignments.json"
```

Studio masks candidate/control roles and model suggestions in this study
even if you accidentally pass a model flag. Give human Keep/Skip/Unsure,
visible-event yes/no/unknown, clarity/framing defects and independent
ratings. A model Skip and a file deletion are completely unrelated.

After saving human reviews, generate the existing independent comparison:

```bash
klip --workspace "$WS" archive-review-results \
  --assignments "$WS/reports/archive-value-study-pilot-v1/assignments.json" \
  --output-dir "$WS/reports/archive-value-study-pilot-results-v1"
```

The results compare candidate/control human preference rates and
what the model was missing, with denominators. If there are too few
assessed controls or source sessions, **do not** report precision or
whole-archive recall. Existing review-results flags these limits.

## Acceptance criteria for a truly accurate editor

Do not optimize exclusively for "Human Keep rate" on suggestions,
which can be gamed by hiding uncertain and rare scenes. Report:

- **Human Keep@20/@50** for each sport separately and overall.
- **Control discovery rate:** human Keeps found in random/exploratory
  controls that the ranked system never suggested.
- **Defect precision:** fraction of human-confirmed crooked, blurred,
  obstructed and badly composed clips among algorithmic QA flags.
- **Temporal boundaries:** human-rated scene entry/peak/exit completeness,
  and proposed-vs-reviewed boundary adjustment in seconds.
- **Diversity:** near-duplicate fraction in selected highlights;
  distinct outings, sports and scene families.
- **Budget:** minutes of user reviewing per 10 accepted moments.
- **Coverage:** minutes of source with verified semantic evidence,
  unprocessed footage, failed video decodes and excluded sources.
- **Safety:** zero original writes, zero auto-deletions, no animal rarity
  or athletic speed/skill claims without qualified evidence.
- **Held-out evaluation:** confirm real outing/session IDs and freeze
  the scoring policy before comparing on completely unseen trips.

## What should follow the first 192 human reviews

A second phase should **decode targeted additional frames** in uncertain,
high-value and control intervals, then create timestamped scene/event
graphs with continuous temporal boundaries. It should prioritize
underwater wildlife subject tracking, smooth-but-fast skiing, and
long-form action recovery segments. Review uncertain failures rather
than pretending that a sparse model verdict is truth.

For expensive model-assisted analysis, only select the top 15–25% of
uncertain or high-value moments and a protected random-control floor.
Re-evaluate on held-out outings. No amount of re-prompting Gemma 3
can replace source coverage or actual human preferences.

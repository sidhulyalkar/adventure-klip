# Klip archive feedback v1: play, review, evaluate

## Context

The real archive pilot processed two frozen eight-video batches:
- Batch 1: 118 scheduled frames, six previously cached complete jobs, two failed.
- Batch 2: 113 scheduled frames, seven completed and one failed; 112 newly computed histogram frames.
- Batch 2 source verification took 547.253 seconds, decoding 178.976 seconds and histogram inference 0.417 seconds. I/O and decoding dominate this stage.

These are sparse *histogram* measurements, not verified wildlife or athletic-action detections.

## Install

Wait until active Klip work is finished before switching Git branches. Check for uncommitted work first.

~~~bash
cd "$HOME/Documents/Projects/adventure-klip"
git status --short
git fetch origin
git switch --track origin/feat/archive-review-feedback-v1
source .venv/bin/activate
python -m pip install -e '.[dev,semantic,images]'
export WS="$HOME/AdventureAI-clean.QJnopt"
~~~

## 1. Seed the candidate + systematic-control review assignments

~~~bash
klip --workspace "$WS" archive-review \
  --campaign "$WS/reports/archive-pilot-v1" \
  --max-assets 16 --window-seconds 14 \
  --output-dir "$WS/reports/archive-review-first16-v1"
~~~

The destination must not already exist. If you previously created this
review dataset, reuse its original assignments.json without regenerating it.
Candidate and control are *sampling roles*, not known positive/negative
labels. A rare animal or a valuable ski/bike maneuver might be found in either.

## 2. Open the same assignments with playable local video

~~~bash
klip --workspace "$WS" studio \
  --benchmark "$WS/reports/archive-review-first16-v1/assignments.json"
~~~

Studio opens a local-only, token-protected review interface with the
original source window in the player. Review fields include keep/skip,
observed event, observed activity, description and notes, plus twelve
independent dimensions for event significance, local subject visibility,
ambient visibility, technical execution, outcome, narrative completeness,
visual quality, novelty, learning, sharing and personal meaning.

The first playback may rehash the original video for integrity. Avoid
running this concurrently with a source-heavy batch. Source originals
are served read-only; ratings go to append-only SQLite revisions.
An unchanged repeat review creates no new row. Unassessed ratings remain null.
Species taxonomic verification is deliberately disabled in the generic form.

## 3. Produce a feedback report

~~~bash
klip --workspace "$WS" archive-review-results \
  --assignments "$WS/reports/archive-review-first16-v1/assignments.json" \
  --output-dir "$WS/reports/archive-feedback-evaluation-v1"
open "$WS/reports/archive-feedback-evaluation-v1/index.html"
~~~

Reports summarize reviewed-vs-unknown intervals by sport and candidate/control
role, human observed-event and keep rates with explicit denominators, and
potentially interesting systematic controls that candidate sampling overlooked.
They also order already-reviewed moments for *learning* and *share review*
separately, without making any predictions for unreviewed footage.

Use a new output folder for each saved report snapshot.

## Offline review import alternative

The standalone assignment HTML exports a local JSON file of reviews. To
import it atomically without using the Studio player, run:

~~~bash
klip --workspace "$WS" archive-review-import \
  --assignments "$WS/reports/archive-review-first16-v1/assignments.json" \
  --reviews "$WS/reports/klip-human-reviews.json"
~~~

Adjust the exported JSON path to its actual location. All entries are validated
before database writes. Blank rows are skipped and exact reimports are idempotent.

## Quality and evidence boundaries

- Missing judgments and failed frames remain unknown.
- A systematic control interval is not necessarily boring or event-negative.
- Human preference and factual event observation are separate judgments.
- Murky ambient water and clear local animal visibility can coexist.
- A static ski jump or bike rock feature is not proof it was completed.
- A ray-like silhouette does not verify a species or individual count.
- Human-reviewed score order is not a trained model or calibrated probability.
- Current deterministic assignments cannot measure unbiased whole-library recall.
- Future validation must use randomly sampled intervals and session/day-level holdouts.

## After the first human-reviewed intervals

Prioritize source integrity and decoder cost: the prior stage spent most time
hashing and seeking, not computing histogram inference. Build provenance-rich
frame/subject-quality features (sharpness, local contrast, visibility, temporal
continuity); then spend expensive marine or sport reasoning only on intervals
with strong reviewed significance, uncertainty or evidence gaps. Preserve
a separate random exploration budget to detect missed events. Validate
performance at fixed frame and model-time costs.

This release does not process the entire video bank, identify species,
diagnose skier biomechanics or auto-publish highlight clips.

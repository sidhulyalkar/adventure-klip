# Archive Benchmark Feedback v1: Review real footage, save evidence, measure blind spots

PR #44 generated candidate-like and systematic-control review windows from
the first frozen archive batches. This change closes the next loop: **play
the exact source interval inside Klip Studio**, rate it, save append-only
human evidence, and generate an honest analysis of selected versus overlooked
intervals. It never infers true animal species or athletic skill from
histogram scores.

## First two batches: measured, not guessed

- Batch 1: 8 videos, 118 planned timestamps, 6 complete jobs cached, 2 failed.
- Batch 2: 8 videos, 113 planned timestamps, 7 completed, 1 failed.
- Batch 2: 112 newly analyzed frames, source verification 547.253 seconds,
  decoding 178.976 seconds, histogram inference 0.417 seconds and final
  source verification 9.488 seconds.
- These logs cannot tell us which clips are actually interesting. They
  justify improving decode/source-I/O efficiency, but we need a benchmark of
  human-worthy events before training a ranker.

## Run on your Mac

After any ongoing Klip source analysis finishes, update the repository:

```bash
cd "$HOME/Documents/Projects/adventure-klip"
git status --short
git fetch origin
git switch --track origin/feat/archive-review-feedback-v1
source .venv/bin/activate
python -m pip install -e '.[dev,semantic,images]'
export WS="$HOME/AdventureAI-clean.QJnopt"
```

If you already created an archive review folder with PR #44, **reuse it**.
Otherwise create one once:

```bash
klip --workspace "$WS" archive-review \
  --campaign "$WS/reports/archive-pilot-v1" \
  --max-assets 16 --window-seconds 14 \
  --output-dir "$WS/reports/archive-review-first16-v1"
```

Start the private Studio directly in benchmark mode:

```bash
klip --workspace "$WS" studio \
  --benchmark "$WS/reports/archive-review-first16-v1/assignments.json"
```

Studio runs at a private loopback address with an unguessable per-session
token. In the new **Archive Benchmark** screen, you can:
- play the **original source video**, automatically seek to each proposed
  interval and stop at the end;
- navigate one interval at a time, seeing actual activity strata, coverage
  state and *candidate versus control* roles;
- record whether an event actually happened; keep, skip or mark uncertain;
- score **12 dimensions independently** on a five-point anchored scale
  (0, .25, .5, .75, 1), leaving any field unassessed;
- write observations and technique/improvement notes;
- save revisions **in private SQLite, not in the DJI source or browser
  local storage**; review entries reload in subsequent Studio sessions.

The first video stream in a Studio session performs an **entire original
SHA-256 verification**; seeking a large DJI file can therefore cost
source I/O. Subsequent requests for an unchanged, inode-matching file
within the same Studio session reuse that verification. Some browser
codecs may not play DJI MP4 containers; that is a playback limitation,
not a declaration that the source is missing or corrupt.

Evaluate saved human reviews without reopening original media:

```bash
klip --workspace "$WS" archive-review-results \
  --assignments "$WS/reports/archive-review-first16-v1/assignments.json" \
  --output-dir "$WS/reports/archive-evaluation-first16-v1"

open "$WS/reports/archive-evaluation-first16-v1/index.html"
```

The results include total reviewed/unreviewed, separate observed-event and
keep rates per sport and role, mean assessed values per review dimension,
and a **list of potentially overlooked interesting controls**.
The JSON preserves all most-recent human judgments and revision IDs.

This is an **evaluation of a small, nonrandom pilot**, not a valid
whole-archive event recall estimate. Candidate/control assignments were
deterministic, systematic controls can contain meaningful events, and
numerical scores are subjective observations, not verified species
or calibrated athlete measurements.

## Data model and safety

An additive `benchmark_review_revisions` table holds:
- independently generated revision ID and benchmark SHA-256;
- stable assignment review ID, source asset SHA and interval start/end;
- selection role, JSON ratings, reviewer note and creation timestamp.

Saving unchanged data is idempotent. New judgments append a revision;
the latest revision is used in current reports, while previous decisions
remain inspectable.

Revisions stay attached to the exact frozen benchmark document SHA. If
the assignment file changes, they will not silently attach to a different
timestamp, role or asset. Original footage remains immutable. No external
video upload, species-rarity bonus, automatic clip publication or
catalog relabeling occurs.

## Next high-value implementation

Once 15-30 mixed sport/control intervals have been reviewed:
1. Quantify unexpected keeps/observed events in controls and inspect
   the specific source windows. Expand the systematic exploration floor
   if rare wildlife or smooth technical maneuvers are being missed.
2. Evaluate how reliable source-backed visibility and image-quality
   features are, separating **ambient scene clarity** from the ability
   to **see the key subject**.
3. Create human-verified temporal event boundaries and adjacent
   before/during/after context with no invented achievement/outcome.
4. Group train/test by **trip/session**, including negative and unknown
   intervals. No global recall claim from the first 16 recordings.
5. Benchmark asset SHA/FFmpeg costs at the same sampled-frame budget
   before enabling more expensive OWLv2 or video-language inference.

The design intentionally prioritizes getting **useful, representative
truth data** ahead of adding further model confidence numbers.

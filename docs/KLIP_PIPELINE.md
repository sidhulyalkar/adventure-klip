# Klip pipeline: from indexed footage to highlight reels

**Evidence level:** every stage is implemented and synthetic CI passed. The stages had
reached owner footage as of 2026-11-21 as follows:

| Stage | Owner footage |
|---|---|
| index | ran (10 batches, 113 recordings) |
| judge pass | running |
| stories | not run |
| reels | not run |

No ranking or reel has been owner-reviewed yet.

One entry point: `scripts/klip_pipeline.py`. How each design decision is measured and decided: [KLIP_EVALUATION.md](KLIP_EVALUATION.md).

```
index        klip_value_experiment.py index      reads footage once per recording
  │          REPORTS/discovery-NNN/              motion, audio, segments, review clips
  ▼
judge        klip_judge_batches.py               two-family VLM fact panel per review clip
  │          .../judgments/                      facts agreed / disputed, provenance
  ▼
stories      klip_outing_stories.py              quality gate (no model) → VLM descriptions
  │          REPORTS/outing-stories-v1/          → LLM story checked by code, repaired once,
  │                                               violations removed and listed
  ▼
reels        klip_pipeline.py reels              recipe per outing → preview from review clips
             REPORTS/highlights-v1/              → gallery → master from originals, approved
                                                  recipes only
```

## Commands

```bash
# Where everything stands (reads files only)
python scripts/klip_pipeline.py status --reports "$WS/reports"

# Judge -> stories -> reels, each resumable; rerun to continue
caffeinate -i python scripts/klip_pipeline.py run --reports "$WS/reports" \
  --roster "$ROSTER" --trusted-source-root "$SSD"

# Reels only, after stories exist; then watch and read why each clip is in
python scripts/klip_pipeline.py reels --reports "$WS/reports"
open "$WS/reports/highlights-v1/index.html"

# Full-quality master for a reel you approve (recipe SHA from the gallery or recipe file)
python scripts/klip_pipeline.py reels --reports "$WS/reports" --trusted-source-root "$SSD" \
  --approve <recipeSha256> --master
```

`run` never starts the library index; a full read of the footage is always its own decision.

## Guarantees

- **Originals are only read:**
  - by the index;
  - by an approved master render, which also checks that the file is under the trusted
    root and still has its verified size.
- **New files only:**
  - Earlier reports, stories and recipes are kept under `history/` when replaced.
  - Approvals are appended with a timestamp.
- **Owner labels stay separate from model output:**
  - Model output is marked as a proposal (`isModelProposalNotOwnerJudgment`).
  - Rosters holding model-guessed activities are refused by every evaluation command.
- **A reel is only as grounded as its story:**
  - Recipes are built only from the story's validated shortlist.
  - Unusable scenes are never included.
  - Each clip records its anchor (cited observation, event candidate, motion peak or
    middle) and the moment's "why".

## How clips are cut

Each shortlisted scene becomes one clip around its anchor. The lead-in and follow-through
depend on the sport:

| Sport | Lead-in | Follow-through |
|---|---|---|
| ski, bike | 3 s | 5 s |
| snorkeling | 4 s | 6 s |

Clips are clamped to the scene. The reel keeps the story's best scenes until the
`--target-seconds` budget (default 60) is used, then plays them in clock order (or
`--order rank`). Snorkeling dissolves; action sports hard-cut. Previews are 960×540 from
review clips, with sound when every clip has it. Masters are 1080p from the originals.

## Variety and underwater colour (v1)

- **Near-duplicates.** Going down the story's ranked shortlist, a clip is dropped when:
  - it overlaps a clip already kept from the same recording, or comes within 2 s of one; or
  - it looks like a kept clip: cosine similarity ≥ 0.92, using the stored SigLIP2
    embeddings, else a colour-thumbnail descriptor from the review clip.

  Each drop is listed in the recipe with its reason. The recipe records the threshold and
  the highest similarity among kept clips, so the threshold can be calibrated when you
  flag repetitive reels. Change it with `--similarity-threshold`.
- **Underwater colour.** For snorkeling clips that the judges agreed are underwater (or
  that measure a red deficit), a correction is proposed:
  - red is restored from green (Ancuti et al. 2018, linear form);
  - then a gentle per-channel levels stretch.

  Parameters are measured per clip and stored in the recipe, so a master looks exactly
  like its preview. It is **off by default** and is decided by your eyes, not by a metric:

```bash
python scripts/klip_pipeline.py reels --reports "$WS/reports" --ab-color
open "$WS/reports/highlights-v1/color-ab/index.html"     # pick per pair, Download answers
python scripts/klip_pipeline.py ab-score --reports "$WS/reports" \
  --answers ~/Downloads/color-ab-answers-<deck>.json
# if corrected wins (exact sign test, p < 0.05, at least 10 decided pairs):
python scripts/klip_pipeline.py reels --reports "$WS/reports" --color underwater-v1
```

Changing the colour mode changes the recipe SHA. An approval covers one exact look, and
the earlier recipe is kept under `history/`.

## What is not decided by the pipeline

- **Whether the ranking is good.** `klip_value_experiment.py evaluate` answers that, on
  held-out trips, from the owner's grid and pair answers.
- **Publication.** No stage publishes, uploads or marks anything approved except an
  explicit `--approve`.

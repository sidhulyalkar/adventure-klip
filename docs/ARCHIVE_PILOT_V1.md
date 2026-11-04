# Archive Pilot v1: one command to inventory, plan, run and resume

The orchestration is a native `klip archive-pilot` subcommand, not a
downloaded script. It uses Klip's real catalog and adaptive scheduling,
works with the existing source SSD, and **does not** merge SQLite
workspaces or change recording labels.

## Install

If another Klip process is active, let its current run finish before
switching branches and starting the new pilot.

```bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/archive-pilot-orchestrator-v1
source .venv/bin/activate
python -m pip install -e '.[dev,semantic,images]'

export WS="$HOME/AdventureAI-clean.QJnopt"
```

## Most useful one-command workflow

Inspect and plan the **first eight videos**, comparing the older
`~/AdventureAI` catalog read-only:

```bash
klip --workspace "$WS" archive-pilot --legacy "$HOME/AdventureAI"
```

Read the output before spending compute. It includes a frozen roster, an
immutable adaptive plan, candidate sport strata, counts, source footprint
and the old-workspace overlap. The default **does not decode media or load
AI models**. It writes only small JSON reports under
`$WS/reports/archive-pilot-v1`.

Run the same frozen first batch using inexpensive local histogram semantics:

```bash
caffeinate -i klip --workspace "$WS" archive-pilot \
  --legacy "$HOME/AdventureAI" --run
```

The `--run` flag performs **source SHA verification, sparse batched
FFmpeg decoding, histogram inference and checkpointing**. It does **not**
run SigLIP2, OWLv2, video-language models or high-resolution rendering.

If interrupted, rerun **the exact same command**. Matching frame
checkpoints are reused. Incomplete frame decoding is reported as an
error rather than silently interpreted as no interesting content.
Review the error output before increasing compute.

After the first batch, change only the batch number:

```bash
caffeinate -i klip --workspace "$WS" archive-pilot --batch 2 --run
```

Continue with `--batch 3`, etc. A frozen campaign roster ensures the
underlying asset selection cannot shift as caches accumulate. A new
activity label or cache result will not rewrite or reorder the original
asset batch selection.

For a small first hardware stress test:

```bash
caffeinate -i klip --workspace "$WS" archive-pilot \
  --batch 1 --batch-size 4 --max-frames 16 --run
```

Choose these options at campaign creation time. To switch batch size or
frame budget, create a **new** campaign using a new `--output-dir` with a
different parent directory, because the roster and plan are frozen.

## Safety and interpretation

- Source files on `/Volumes/Sid_DJI_SSD` remain read-only. No uploads.
- Old and new workspaces are **compared only by asset hash and metadata**;
  artifacts and catalogs are not copied or silently combined.
- The former `activity-overnight-v1` report supplies **selection
  hypotheses**, checked using stored payload checksums. Its proposed
  `snorkeling`, `ski` and `bike` labels DO NOT change catalog labels.
- Candidates are chosen round-robin across supported sports and unresolved
  footage, with reviewed and previously analyzed footage included.
- Each batch has a stable signed adaptive plan, small JSON manifest and
  a frozen master roster. Batch reruns preserve these bytes.
- One explicit `--run` controls when substantial local I/O begins.
  Histograms are cheap compared to model inference but still require
  reading the source video and decoding planned frames.
- Missing source paths or invalid durations never trigger fabricated
  estimates. The command reports them in the inventory and skips those
  assets from the eligible roster.
- Not every wildlife encounter can be observed at sparse sampling
  intervals. This command is only a **coverage and cache-reuse pilot**,
  not the final best-of highlight ranker.
- Multiple Klip writer jobs in one workspace serialize through the
  workspace lock, but an older process operating in another workspace
  needs to be stopped or allowed to complete separately.

### Outputs

Default first batch:

```text
$WS/reports/archive-pilot-v1/
  roster.json             # frozen per-asset selection and campaign config
  batch-0001/
    manifest.json         # inventory, hypotheses, costs and selection
    plan.json             # exact validated sample timestamps
  batch-0002/
    manifest.json
    plan.json
```

Use `--output-dir` if you want a separate campaign with new selection
parameters. The parent folder of the output directory determines the
campaign root. Never point it inside the original DJI SSD.

## What happens after the cheap first pass?

The next stages require **measured** evidence from the small representative
pilot before they are automatically expanded to the full archive:

1. Index scene transitions and validate segment-level sport classification,
   including pale sandbar snorkeling, above-water kayak negatives,
   chairlift/transit skiing and mountain-bike climbs.
2. Select mixed high-scoring **and random** intervals to estimate rare-event
   recall at fixed decoding/inference budgets.
3. Run expensive marine object detection only on targeted snorkeling
   intervals; run temporal action reasoning on promising ski/bike episodes.
4. Consolidate contradictory object labels and preserve source PTS,
   coverage gaps and before/during/after evidence.
5. Measure personal learning and sharing preferences separately, with
   human-reviewed source footage and real clip boundaries.
6. Expand through additional source batches only after the processing
   ledger demonstrates reliable resume, coverage and compute control.

The repository has an explainable `rank-moments` policy, but it must
not be used to manufacture event significance, rare species, sport skill,
or share-ready video quality from model similarities alone.

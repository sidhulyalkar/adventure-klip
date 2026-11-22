# Klip evaluation: how decisions get made from evidence

**Evidence level:** implemented; synthetic CI passed. No owner labels have been collected
with these tools yet, so no decision below has been made.

Every design choice in the pipeline (which judges, which colour, which clip boundaries,
whether LLM stories beat a plain ranker) is decided by a measurement on the owner's own
footage, with a rule fixed before the data comes in. This page lists the measurements, the
rules and the safeguards.

## Where things live

All paths are under `REPORTS/`.

| Kind | Where | Written by | Ever owner truth? |
|---|---|---|---|
| Model observations | `discovery-*/*/judgments/`, `outing-stories-v1/`, `highlights-v1/*.recipe.json` | judges, describer, storyteller, compile | **No.** Marked `isModelProposalNotOwnerJudgment` / `isModelObservationNotOwnerTruth` |
| Measurements | `outing-stories-v1/segments/*.quality.json`, `unjudgeable/` | code (no model) | No |
| Keep/skip judgments | `owner-ledgers/` evidence ledger | owner (review apps, grid decks) | Yes |
| Boundary labels | `owner-ledgers/boundary-labels.jsonl` | owner (trim handles) | Yes |
| Fact labels | `owner-ledgers/fact-labels.jsonl` | owner (fact-check deck) | Yes |
| Roster | `owner-ledgers/roster*.json` | owner. Model guesses are tagged `activity-guessed` and refused by evaluation | Owner rows only |
| Held-out days | `holdout-v1.json` | owner, once | n/a |
| Evaluations | `evaluations/*.json` | scoring commands, timestamped, never overwritten | n/a |
| Run records | `runs/*.json` | every command that does work | n/a |

Owner label files are append-only. Importing the same answers again adds nothing, and a
changed answer is a new record (the latest wins). Every record keeps the deck, the SHA-256
of the answers file and the import time.

## The held-out days

`klip_holdout.py freeze` picks one capture day per sport, once. It is replaced only with
`--replace --reason`, and the old file is kept.

**Never drawn from held-out days:**
- the colour A/B deck;
- the reel A/B deck;
- the fact-check deck;
- the regression set;
- story examples.

**Reported on held-out days:** `eval-trims` and `evaluate`, separately from development
days.

Score held-out days once a method is settled, not while tuning it.

## Decisions and their rules

| Question | Measurement | Command | Rule |
|---|---|---|---|
| Can the judges be trusted per question? | Agreement and Cohen's kappa per question, no owner input | `klip_fact_check.py agreement` | Questions with kappa < 0.4 or > 40% disputed are not used as story evidence until owner labels settle them |
| Which judge is right when they disagree? | Owner yes/no on disputed facts plus agreed controls | `klip_fact_check.py deck` → `import` → `reliability` | A dispute is settled only when each voting model has at least 8 labels on that question and the reliability-weighted P(yes) is ≥ 0.85 or ≤ 0.15. It is marked *resolved*, never *agreed* |
| Can the slow judge be skipped? | Calls saved vs disputes hidden | `klip_fact_check.py cascade` | Adopt only if, on owner-labelled facts, the fast-only panel is not less accurate than the two-judge panel |
| Did a prompt or model change make judging worse? | Accuracy on the frozen owner-checked facts | `klip_regression_set.py build`, then `score --model M` | A drop of more than 5 points against the same model's last score under a different prompt exits 1: don't ship it |
| Are clip boundaries right? | Owner trims on the reel clips | gallery → `klip_pipeline.py import-trims` → `eval-trims` | A boundary method replaces the fixed windows only if it lowers the truncation rate without raising median error, on development days, then confirmed on held-out days |
| Does underwater colour correction help? | Blind pairs: same clip, two looks | `reels --ab-color` → `ab-score` | Exact sign test; at least 10 decided pairs; p < 0.05 for the correction to become the default |
| Do LLM stories pick better moments than a plain ranker? | Blind pairs: story reel vs a reel of the same scenes, budget and windows ranked by one signal | `reels --ab-reels motionPeak` → `ab-score --deck reel-ab` | Same test. If the story is not preferred, the story layer does not justify its cost |
| Does the ranking match what the owner keeps? | Grid and pair answers, held-out trips | `klip_value_experiment.py evaluate` | Its gate: at least 5 held-out groups, 100 pairs, and beating the best single baseline |
| Does the index miss keepers? | Grid draws from whole recordings | grid deck → `evaluate` (`sampledKeepRecallAtBudget`) | Low recall means building detectors comes first |

## Understanding the dataset

`klip_pipeline.py profile` writes `dataset-profile.md`, one row per capture day and sport:
- recordings, footage hours and micro-recordings;
- review segments, judged and unjudgeable;
- where the sport label came from;
- quality verdicts;
- owner labels of every kind;
- the held-out flag.

Use it to see where labels are thin before choosing what to collect next.

## Safeguards built in

- **One model-heavy run at a time:** a machine-wide lock in `~/.klip/locks`. A second run
  waits and names the holder; the benchmark refuses to run under contention.
- **A record of every run:** `runs/*.json` keeps the commit, local changes, module
  versions, arguments, duration and errors. `klip_pipeline.py runs --export` writes a
  redacted table for `logs/experiments/`.
- **Blind comparisons stay blind:** sides are random, file names are neutral tokens, and
  the key lives outside the page.

## Suggested order with the owner's time

1. Freeze the held-out days (2 min).
2. Confirm the roster (10 min).
3. Do one grid session (25 min).
4. Do the fact-check deck (about 40 questions, 10 min). Then run `reliability`, `cascade`
   and `regression build`.
5. After the first story run: trims while watching the reels, the colour A/B, then the
   reel A/B.

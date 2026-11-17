# Learning what Klip should keep: runbook

**Evidence level:** implemented; synthetic CI passed (planted-signal recovery, held-out
isolation, gate behaviour); tap page driven end to end in headless Chrome with real
clips. **Not yet run on owner footage. No claim about what the owner values exists yet.**

## The question this answers

Given one outing's footage and a review budget, does a ranking put the moments the owner
would keep near the top, on outings it has never seen? Everything here serves that one
question. Category labels, judge facts and embeddings are inputs to it, never the answer.

## Pieces

| Step | Module | What it produces |
|---|---|---|
| Outings | `outing_roster.py` | Proposed outings from capture-time gaps; provisional until you confirm |
| Evidence | `discovery_experiment.py` (now with an `audio` stage and proxies with sound) | Per-recording motion, loudness, segments, proxies, judge facts |
| Features | `value_features.py` | Per interval: motion level and peaks, audio surprise, novelty within the outing and against the archive, judge facts, mean embedding. Missing stays missing |
| Pairs | `value_pairs.py` | "Keep A over B" from the evidence ledger (same outing, same goal, non-overlapping) and from taps |
| Taps | `value_pairs.build_deck` / `deck_html` | A blind page: two clips from one outing, pick one. 130 pairs plus 20 swapped repeats |
| Head | `value_head.py` | Ridge Bradley–Terry on evidence differences. Readable weights. Scaling, fact list and embedding PCA fitted on training outings only |
| Evaluation | `value_eval.py` | Leave-one-outing-out pair accuracy and keep-recall at 10% and 25% of footage, for chance, each single measurement, a fusion, and two head variants; outing-bootstrap intervals; a gate |
| CLI | `scripts/klip_value_experiment.py` | `roster`, `deck`, `import-taps`, `evaluate` |

## How to run it (about 45 minutes of your time in total)

Everything below reads derived files only, except step 2, which is the bounded discovery
run that already exists and needs the SSD.

1. **Confirm outings (5 min).**
   `klip_value_experiment.py roster --workspace "$WS" > roster.json`. Fix merges and
   splits, then set `"status": "owner_confirmed"`. Every held-out number depends on this.

2. **Get evidence for 8–12 recordings from at least 5 outings.** Pick recordings that
   already have owner Keeps where possible. Use `klip_discovery_experiment.py run` with
   `--stages verify motion audio segments proxies` first; add `judge` later. No model time
   is needed for the first evaluation.

3. **Import existing judgments.** `klip_evidence_ledger.py import ...` for each export,
   as before. Only owner-marked spans count for recall; pipeline windows count for pairs.

4. **Tap (25 min).** `klip_value_experiment.py deck --experiment <dir> --roster roster.json`
   writes `taps-<id>.html` into the experiment folder. Open it, answer with the arrow keys,
   press S to hear one clip, export. Then
   `klip_value_experiment.py import-taps --deck ... --answers ... --pairs-ledger owner-pairs.jsonl`.
   The import prints how often you gave the same answer to repeated pairs. That number is
   the ceiling: no model can agree with you more often than you agree with yourself.

5. **Evaluate.** `klip_value_experiment.py evaluate --experiment <dir> --roster roster.json
   --ledger owner-evidence.jsonl --pairs-ledger owner-pairs.jsonl --workspace "$WS"
   --output "$WS/reports/value-001"`. Read `gate` first, then `pairAccuracy`, then
   `recallAtBudget`. `queue.json` is a ranked list with the evidence behind each score;
   it is in-sample and is not a result.

## How to read the gate

- `pilot_no_claim`: fewer than 5 outings or 100 scored pairs, or the roster is
  unconfirmed. Read the numbers as a smoke test only.
- `head_not_shown_better`: rank with the best single measurement and spend effort on
  evidence or labels, not models.
- `head_beats_best_baseline`: the head's lead over the best baseline has a 95% outing
  bootstrap interval above zero. The best baseline is picked on the same data, which
  favours it, so this is a fair win.

## How to run the loop after the first evaluation

Each round costs about 25 minutes of tapping and decides the next piece of work:

1. **If audio or novelty alone is close to your self-consistency,** the problem is mostly
   solved for that sport; spend time on boundaries and assembly instead.
2. **If everything is near chance,** the evidence does not see what you value. Write a
   one-line reason on a few disagreements and add the measurement they point to (for
   example, a judge question). Do not add model capacity.
3. **If the head wins,** run `deck --active`. Half the next deck is pairs the head
   cannot separate. Those train the head but never score it, so accuracy stays honest.
4. **Only add the frozen video encoder or public highlight priors** (V-JEPA 2, Mr. HiSum)
   as new evidence columns, compared through this same report. K-TIDE returns only if
   a result here shows that temporal context is the missing piece.

## Rules this keeps

- Held-out outing: no pair, scale, fact name or embedding axis from it reaches its model.
  A test checks this by instrumenting the fit.
- Unreviewed footage is unknown, never a loser in a pair.
- Repeated taps merge into their original; a contradictory repeat becomes a tie.
- Uncertainty-sampled taps train but never score.
- Every evaluation writes to a new directory and records the SHA-256 of each input.

## Known limits

- Embedding novelty needs cached SigLIP2 frames for these recordings; without them those
  columns are empty and the `full` head reduces to signals plus facts.
- Pairs from different goals (marine, ski run, taps) are pooled for training and reported
  by goal; the head does not yet condition on goal.
- Segments longer than 60 s are left out of decks; long scenes need a different review.
- Tap proxies are re-encoded at 960 px, which may hide detail the original shows.

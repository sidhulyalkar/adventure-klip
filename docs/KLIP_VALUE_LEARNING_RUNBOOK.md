# Learning what Klip should keep: runbook

**Evidence level:** implemented; synthetic CI passed (planted-signal recovery, held-out
isolation, gate behaviour, every owner-label reader). Both review pages driven end to end
in headless Chrome with real clips. **Not yet run on owner footage. No claim about what
the owner values exists yet.**

## The question this answers

Given one outing's footage and a review budget, does a ranking put the moments the owner
would keep near the top, on outings it has never seen? Category labels, judge facts and
embeddings are inputs to that question, never the answer.

## Every owner label is read

`owner_evidence.collect` gathers every judgment once, in its latest form:

| Where it lives | What is read | How it is used |
|---|---|---|
| Evidence ledger (marine v1/v2, calibration, ski-run imports) | keep/skip/unsure, owner event spans, 1–5 ratings | Keep-over-Skip pairs; owner spans measure recall; ratings order Keeps within one review session (half weight, training only) |
| Catalog `example_revisions` (Studio reviews) | latest revision per example; keep/skip/neutral | As above. An interval you drew or whose edges you moved counts as an owner-marked span |
| Catalog `pairwise_revisions` (Studio A/B) | latest choice per comparison | Direct pairs |
| Catalog `benchmark_review_revisions` | latest keep/skip per review | Keep-over-Skip pairs |
| `reports/**` blind pair reviews | choice, matched to the frozen manifest by SHA-256 | Direct pairs |
| `reports/**` curated highlight manifests | each observer's keep/skip and ratings | Pairs and rating order |
| Tap and grid ledger | your answers on the new pages | Pairs, random-sample keep labels, self-consistency |

Rules:
- A later revision replaces the one it corrects.
- The same judgment reached by two routes counts once.
- A keep and a skip of the same interval under the same goal are set aside and listed for
  you to resolve.
- Pairs that span two outings train only when neither outing is held out, and never score.
- `harvest` lists every review file it recognises but does not use, with the reason:
  - marine, calibration or ski-run exports not yet imported to the ledger;
  - blind-pair reviews whose manifest is missing;
  - formats not read yet (moment annotations v2, reel timed reactions).

## Which review interface, and why the grid

The interfaces you have answer different questions:

- **Marine/scene review and curation review** (one clip, keep/skip, ratings, a written
  reason): the richest per clip, and the only place you explain *why*. Also the slowest,
  and every clip was chosen by a pipeline, so its keep rate says nothing about the rest of
  the footage.
- **Blind A/B pairs and Studio compare**: fast, and good at ordering, but one bit per
  answer, no sense of whether *either* clip is worth keeping, and usually across outings.
- **Ski-run boundary annotation**: exact edges. Use it for trimming, not for ranking.

**The grid, now the default `deck`, answers the ranking question most efficiently.** Each
screen shows four segments from one outing, drawn uniformly at random from the whole
timeline. You tick the ones you'd keep, star the best, and press Enter. One screen gives:

- four keep/skip labels;
- up to six orderings (every ticked clip over every unticked one, the star over the
  other ticks);
- because the draw is uniform, an unbiased estimate of your keep rate for that outing,
  and of each ranker's recall on the whole outing (`sampledKeepRecallAtBudget`). Before
  this, recall needed owner-marked spans, which you have few of.

30 screens plus 4 shuffled repeats take about 20–25 minutes and yield about 120 labels and
up to 180 orderings. The same time in pairs yields about 150 orderings and no labels.
Repeats measure how consistently you answer: per clip, and for the star.

Keep the written-reason pages for one job: after an evaluation, review the dozen clips the
model was most confidently wrong about and write why. Those notes say which evidence to
add next.

## Commands for your MacBook Pro

Update the code (once):

```bash
cd ~/path/to/adventure-klip            # your clone
git fetch origin
git switch value/within-outing-preference-v1     # or: git switch -c value/within-outing-preference-v1 --track origin/value/within-outing-preference-v1
source .venv/bin/activate              # whichever environment you already use
python -m pip install -e '.[vision,images]'
python -m pytest -q tests/test_value_learning.py tests/test_owner_evidence.py
```

Set paths for the session:

```bash
export WS=~/path/to/klip-workspace     # holds catalog.sqlite3 and reports/
export SSD=/Volumes/YourSSD            # mount the footage drive first
export LEDGERS=$WS/reports/owner-ledgers && mkdir -p "$LEDGERS"
```

1. Outings (about 5 minutes). Open `roster.json`, fix merges and splits, and set
   `"status": "owner_confirmed"`.

   ```bash
   python scripts/klip_value_experiment.py roster --workspace "$WS" > "$LEDGERS/roster.json"
   ```

2. Import file exports into the ledger, then see what's used. `harvest` lists any export
   still missing from the ledger. Import each with `klip_evidence_ledger.py import` as
   before, and repeat until `notConsumed` holds only formats marked "not yet read".

   ```bash
   python scripts/klip_value_experiment.py harvest --workspace "$WS" \
       --ledger "$LEDGERS/owner-evidence.jsonl" --roster "$LEDGERS/roster.json"
   ```

3. Pick recordings that cover your labels. This takes the most-labelled recording of
   each outing first. Run discovery without models, in two steps:

   ```bash
   python scripts/klip_value_experiment.py plan --workspace "$WS" \
       --ledger "$LEDGERS/owner-evidence.jsonl" --roster "$LEDGERS/roster.json" \
       --max 12 > "$LEDGERS/sources-001.json"

   python scripts/klip_discovery_experiment.py run --sources "$LEDGERS/sources-001.json" \
       --workspace "$WS" --trusted-source-root "$SSD" --allow-mounted-ssd \
       --output "$WS/reports/discovery-001" --stages verify motion audio segments
   ```

   Check the timings it prints, then rerun the same command with
   `--stages verify motion audio segments proxies` to cut review clips. It resumes; nothing
   is decoded twice.

4. Review (about 20–25 minutes). Then open the printed `grid-….html` in Chrome or Safari.

   ```bash
   python scripts/klip_value_experiment.py deck --experiment "$WS/reports/discovery-001" \
       --workspace "$WS" --roster "$LEDGERS/roster.json"
   ```

   If the clips don't play from a file, serve the folder locally and open the page from
   there:

   ```bash
   python -m http.server 8765 --bind 127.0.0.1 --directory "$WS/reports/discovery-001"
   # open http://127.0.0.1:8765/grid-<id>.html
   ```

   Press Export when done; the file lands in `~/Downloads`. Import it:

   ```bash
   python scripts/klip_value_experiment.py import-answers \
       --deck "$WS/reports/discovery-001/grid-<id>.deck.json" \
       --answers ~/Downloads/klip-grid-answers-<id>.json \
       --pairs-ledger "$LEDGERS/owner-pairs.jsonl"
   ```

5. Evaluate. Read in this order:
   - `gate`;
   - `ownerSelfConsistency`;
   - `pairAccuracy`;
   - `sampledKeepRecallAtBudget`.

   `queue.json` is in-sample, a worklist rather than a result.

   ```bash
   python scripts/klip_value_experiment.py evaluate --experiment "$WS/reports/discovery-001" \
       --workspace "$WS" --ledger "$LEDGERS/owner-evidence.jsonl" \
       --pairs-ledger "$LEDGERS/owner-pairs.jsonl" --roster "$LEDGERS/roster.json" \
       --output "$WS/reports/value-001"
   ```

For a second batch of recordings, run `plan ... --exclude-experiment
"$WS/reports/discovery-001"`. Then pass both experiment folders to `evaluate
--experiment`.

## How to read the gate

- `pilot_no_claim`: fewer than 5 outings or 100 scored pairs, or the roster is
  unconfirmed. Treat the numbers as a smoke test.
- `head_not_shown_better`: rank with the best single measurement. Spend effort on
  evidence or labels, not models.
- `head_beats_best_baseline`: the head's lead over the best baseline has a 95% outing
  bootstrap interval above zero. The best baseline is picked on the same data, which
  favours it, so this is a fair win.

## The loop after the first evaluation

1. **A single measurement is close to your self-consistency:** ranking is mostly solved
   for that sport; move to boundaries and reel assembly.
2. **Everything is near chance:** the evidence does not see what you value. Write reasons
   on the dozen confident misses and add the measurement they point to (often a judge
   question). Do not add model capacity.
3. **The head wins:** add another grid round on new recordings, or `deck --format pairs
   --active` to spend a round on pairs the head cannot separate. Those train, never score.
4. **Larger pieces come in only as new evidence columns compared through this report:**
   a frozen video encoder, public highlight priors, K-TIDE if temporal context is shown to
   be the gap.

## Known limits

- Moment annotations v2 and reel timed reactions are found and reported, not yet read.
- Goals are pooled for training and reported separately; the head does not condition on
  goal.
- Grid and tap decks skip segments longer than 60 s.
- Review clips are 960 px re-encodes and may hide detail the original shows.
- Embedding columns need cached SigLIP2 frames for the scanned recordings.

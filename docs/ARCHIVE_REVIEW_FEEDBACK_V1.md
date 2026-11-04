# Klip: playable archive benchmark and review feedback

This is the missing human-feedback loop for the frozen archive pilot. It
adds a private, source-backed video review view and versioned human ratings;
it **does not** run new inference or mark any animal or athletic maneuver
as verified.

## What was validated and what remains unknown

The initial source collection comprises 232 indexed recordings in
`AdventureAI-clean.QJnopt`. The first two frozen batches included 16
source recordings with balanced snorkeling, skiing, biking and unresolved
strata. Batch 1 reused six complete histogram jobs and failed on two
recordings; batch 2 completed seven and failed on one while returning
112 new histogram frame results.

A `histogram` cache contains low-level appearance features, NOT
species identification, action completion or clip-value labels.

In particular: a potentially distinctive ray in murky water may be
important even when the broader scene is not visually pristine. Human
review should assess **subject visibility, ambient visibility, event
significance, archive novelty, narrative completeness, personal meaning,
technical learning and sharing value as separate dimensions**.
No score from a single frame proves a wildlife species, athletic technique,
or the quality of a complete scene.

## Step 1: Create benchmark assignments (zero video decoding)

First, ensure the previous archive-pilot job is no longer running.
Then switch to this branch and install it:

```bash
cd "$HOME/Documents/Projects/adventure-klip"
git status --short
git fetch origin
git switch --track origin/feat/archive-review-feedback-v1
source .venv/bin/activate
python -m pip install -e '.[dev,semantic,images]'

export WS="$HOME/AdventureAI-clean.QJnopt"
```

Create a new review assignment packet from the frozen pilot campaign.
The current generator selects one candidate-like and one systematic
control window per asset where nonoverlapping windows exist:

```bash
klip --workspace "$WS" archive-review \\
  --campaign "$WS/reports/archive-pilot-v1" \\
  --max-assets 16 --window-seconds 14 \\
  --output-dir "$WS/reports/archive-review-first16-v1"
```

If you have already generated that folder, **do not overwrite it**.
Reuse its `assignments.json` for all future reviews so the benchmark
identity and review provenance remain stable.

## Step 2: Review the playable original intervals in Studio

```bash
klip --workspace "$WS" studio \\
  --benchmark "$WS/reports/archive-review-first16-v1/assignments.json"
```

A token-protected Studio opens on `127.0.0.1`, displaying one source
video at a time. The player seeks to the reviewed window and pauses
at its end. Use Previous / Next to navigate, and save your ratings
before moving on. Your original video is **streamed read-only** using
the existing source-identity safeguards; no clips are rendered or copied
from the SSD.

**First playback of each original source performs an entire-video SHA-256
check**. That can be slow for large DJI files and is expected for this
safety-preserving version. Browser codec support also depends on the
original MP4 encoding and may require local playback outside Studio.
This version does not produce a transcoded fallback video.

Record:
- whether any meaningful action or encounter actually occurred;
- whether you would personally keep or share it;
- which sport really appeared, not merely the model's proposed stratum;
- 12 independently reviewed dimensions on 0/0.25/0.5/0.75/1 scales,
  leaving unobservable dimensions blank;
- a concise event description and observable technique/framing feedback.

The browser never sends data to an external service. Human revisions
are written to `benchmark_review_revisions` in the current workspace's
SQLite database. Each changed rating appends a new revision; saving
identical content doesn't create duplicate history.

## Step 3: Evaluate what Klip missed

```bash
klip --workspace "$WS" archive-review-results \\
  --assignments "$WS/reports/archive-review-first16-v1/assignments.json" \\
  --output-dir "$WS/reports/archive-review-evaluation-v1"

open "$WS/reports/archive-review-evaluation-v1/index.html"
```

The report displays reviewed vs unreviewed counts, candidate/control
event-observed and human-keep rates, assessed denominators, per-criterion
means and potentially interesting moments in systematic controls. It
does not reprocess videos.

**Interpretation:** a high-quality event found in a control window is
a useful *potential blind spot*, but these controls are deterministic,
not random samples. Therefore, neither true archive-wide recall nor
precision can be estimated from this v1 review packet. Missing reviews
also remain unknown, never negative examples.

## Alternative: import the standalone offline gallery export

The previous `archive-review` command also creates an offline
`index.html` with an "Export my reviews (JSON)" control. If you've
already used that page, bring those saved ratings into Studio without
re-entering them:

```bash
klip --workspace "$WS" archive-review-import \\
  --assignments "$WS/reports/archive-review-first16-v1/assignments.json" \\
  --reviews "$HOME/Downloads/klip-human-reviews.json"
```

The import validates **all** review IDs, values and original intervals
before committing a single transaction. Empty template entries are
skipped; an identical file may be imported again without duplicating
existing reviews. Unsupported species verification claims are refused.

## Next steps after initial human judgments

1. **Prove the sampling policy**: build randomized/stratified held-out
   controls across complete trips, not just deterministic video anchors,
   so missed-event recall can be estimated at fixed decoding budgets.
2. **Instrument the actual source cost**: your batch-2 histogram run
   reported ~547 s source verification, ~179 s decode and <1 s histogram
   inference. Optimize source-read reuse and FFmpeg tail recovery first.
3. **Fill source-grounded event features**: visible subject and local
   crop quality, object track continuity, approach/action/outcome,
   actual ski/bike maneuver execution and clean bounds. Every learned
   score needs provenance and uncertainty.
4. **Learn preferences**: separate sport-conditioned discovery, learning,
   and sharing rankers trained on reviewed clips, including controls and
   negative examples; hold out whole filming days/sessions.
5. **Return value to the user**: generate distinct shareable highlights
   and personal-learning revisits, with access to exactly which visual
   evidence supports each proposed action or wildlife encounter.

No merges or media modifications occur as part of this workflow.
The PR is stacked on #44 and its earlier archive-processing prerequisites.

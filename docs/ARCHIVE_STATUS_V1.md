# Archive Status v1: diagnose the frozen pilot without reprocessing videos

This addresses the real batch-1 run of the current 232-video DJI workspace:

- 8 selected recordings across ski/bike/snorkeling/unknown.
- 118 scheduled frame timestamps.
- 6 complete cached histogram jobs reused.
- 2 failed recordings (`2ddb3eb9…` and `005ebe3c…`).
- 36 per-frame cache hits and **zero newly inferred frames** during that run.
- 276.568 seconds spent in source verification, based on the user's run log.

**36 cached frame hits are not 36/118 total coverage**: the six completed
jobs were reused as artifacts and their individual frames were not counted
in this particular run's `frames_cached` counter.

## 1. Diagnose first, without source I/O

```bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/archive-status-failure-triage-v1
source .venv/bin/activate
python -m pip install -e '.[dev,semantic,images]'

export WS="$HOME/AdventureAI-clean.QJnopt"

klip --workspace "$WS" archive-status \
  --campaign "$WS/reports/archive-pilot-v1" \
  --output-dir "$WS/reports/archive-status-first-review-v1"

open "$WS/reports/archive-status-first-review-v1/index.html"
```

This uses **only** the frozen campaign JSON, SQLite metadata/checkpoints
and small semantic artifacts. It does not open or hash the video files
or run FFmpeg/ML. It reports the exact requested timestamps missing
for every selected asset, saved checkpoint hashes, PTS-backed coverage,
failed job status and exact error.

The source SHA is *not* reverified during `archive-status`. Any
partial-recovery action separately checks the full original source hash.

## 2. Recover the usable partial histogram evidence (when eligible)

In the generated `status.json`, look for
`recoveryEligibleWithoutReinference:true`. Recovery is offered only when:
- the plan/config exactly match, source-PTS-backed frame payloads pass
  content hashes, and no unscheduled or corrupt checkpoint is present;
- no more than 2 requested positions are absent;
- at least 80% of scheduled frame positions are present;
- an originally failed job is unambiguously identified.

Use the exact command printed for that asset. For the known batch-1
failures, the general shape is:

```bash
klip --workspace "$WS" semantic-recover \
  --asset FULL_64_CHARACTER_HASH \
  --plan "$WS/reports/archive-pilot-v1/batch-0001/plan.json" \
  --provider histogram --max-missing 2 --min-fraction 0.80
```

This preserves every missing sample as **unknown** and changes that
one job's status to **partial**, never **completed**. It reads/hashes
the original video for integrity, but performs no frame decoding or
additional model inference.

If an asset fails the recovery criteria, do not relax them just to get
a green check. Inspect the missing timestamp, valid frame count and
FFmpeg error first. A seek near EOF is a possible cause, **not proven**
by the generic `FFmpeg failed` message. Failed timestamps that were
not read cannot be interpreted as wildlife- or action-negative frames.

## 3. Continue with the next *frozen* batch

Since batch 1 has known unresolved samples, **do not repeatedly rerun
batch 1 without a change in decoder strategy**. It would rehash
the six completed recordings and retry the same failed timestamps.

You can process the next untouched eight-video group while preserving
the current batch-1 partial evidence:

```bash
caffeinate -i klip --workspace "$WS" archive-pilot \
  --batch 2 --run
```

Repeat `archive-status` without `--output-dir` at any time for a
read-only status snapshot. Generate a new output directory whenever
you want a saved HTML/JSON report.

Avoid running against the same workspace concurrently with the older
all-in-one process. Source video originals remain immutable.

## 4. Engineering follow-up

**P0:** Preserve valid per-frame successes even if one frame fails;
generate an explicit source-PTS-backed partial artifact directly in
the run. Remove the separate manual recovery step after qualifying
the path for all supported providers.

**P0:** Save FFmpeg per-timestamp stderr snippets and mark precise
decoder failure types rather than a generic error. The batch
decoder should not infer a successful event from a nearby substituted
frame. A future *supplemental* earlier seek must have its own timestamp
and provenance, leaving the failed frozen request marked missing.

**P1:** Record source-verification cache cost separately from model
cost, reduce repeat SHA-256 passes only with appropriately documented
integrity guarantees, and verify exact environment/runtime fingerprints
before sharing model artifacts across workspaces.

**P1:** Improve future adaptive plans to avoid known undecodable end
positions, while preserving the signed, immutable v1 plans. Do not
rewrite already frozen plans or silently change their algorithm version.
Validate against real DJI tail GOP/PTS characteristics using FFprobe
and a bounded set of carefully selected cases.

**P1:** Build an interval evidence ledger and an active scheduler that
selects event-detection work for previously unexamined intervals,
prioritizing wildlife, sport maneuvers, scene transitions and calibrated
human preferences with a persistent exploration budget.

Follow [Issue #41](https://github.com/sidhulyalkar/adventure-klip/issues/41)
for the broader roadmap. This PR addresses the targeted status
and histogram-recovery component only.

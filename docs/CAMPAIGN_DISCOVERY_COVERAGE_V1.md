# Campaign discovery: source coverage, sampling bias and review-ready plans

**Status:** New metadata-only diagnostic and candidate-roster planner. This is not a
new event detector, video model, highlight scorer, playable clip exporter or
training job. It is safe to run during a separate active scan because it reads
only completed workspace-local JSON reports. It does not open/stat/hash/seek
original DJI video files.

## Why we need it

The full archive scanner reads and verifies the source, decodes a 1 fps
64×36 grayscale timeline and proposes up to 8 uniform 20 s intervals
and up to 8 high-motion 20 s intervals per source. Candidate windows
may overlap. Their union can cover only a small portion of source time,
**even when 100% of sources were read and decoded at low resolution**.

The current review-packet sampler is round-robin by source SHA, but can
draw multiple candidates per source in a limited 120-window packet. Its
selected-source diversity, true sample role balance and coverage must be
measured before calling it representative.

This audit reports:
- catalog source, verified-receipt source, failed, blocked, unattempted and
  invalid-receipt counts as distinct categories;
- all windows, motion windows and quiet controls, stratified by catalog activity;
- **union** of proposed candidate-window times, not double-counted overlaps;
- fraction of catalog source-video time spanned by candidates;
- metadata-only alternative roster, cycling activity strata and favoring one
  review window per distinct source before taking second windows;
- source/audit/checkpoint fingerprints, ensuring that a proposal generated
  mid-scan is explicitly **exploratory and stale after further scanning**.

## Live, read-only diagnostics

From the separate, updated feature checkout (not the active scan checkout):

```bash
export WS="$HOME/AdventureAI-clean.QJnopt"
export CAMPAIGN_DIR="$WS/reports/full-highlight-ssd-20261106-184704"
python scripts/klip_campaign_discovery.py \
  --workspace "$WS" --campaign-dir "$CAMPAIGN_DIR" \
  --limit 120 --per-source 2

# JSON summary, source file paths never opened:
python scripts/klip_campaign_discovery.py \
  --workspace "$WS" --campaign-dir "$CAMPAIGN_DIR" \
  --limit 120 --per-source 2 --json
```

**Never pull or checkout branches inside a currently running source-scan
process checkout**. Running this diagnostic is optional; you can inspect
its code and synthetic tests on GitHub while the scan proceeds.

After scan completion, preserve a new immutable plan snapshot:

```bash
python scripts/klip_campaign_discovery.py \
  --workspace "$WS" --campaign-dir "$CAMPAIGN_DIR" \
  --limit 120 --per-source 2 \
  --output-dir "$WS/reports/discovery-selection-v1"
```

The output directory must be **new** and is not overwritten. It contains
`discovery-coverage.json` and `review-selection-plan.json`. The latter
contains **source/time/role recommendations only**, not clips or labels.

## Selection semantics

A first round cycles ski, bike, snorkeling, unknown catalog activity strata
and can select *one window per distinct source*. It alternates quiet and
motion roles globally where both exist and excludes near-duplicate intervals
within the same source. Only if the window budget remains does it begin
choosing second windows per source.

The output is a deliberately exploratory **review** sample, not an optimal
quality ranker, not random-uniform in source time, and not guaranteed
activity-proportional. Selection differences from the current packet must
be quantified. Use blinded review and separate test windows to determine
whether better coverage improves real-world event discovery.

Current `PHASE=packet` does **not** yet accept these planned candidate IDs.
Integrate a validated external selection manifest into the packet generator
in a separate change only after its frozen inputs, source ownership,
per-source limits, source-time bounds and immutable receipt contract are
covered by regression tests. Do not bypass the existing SHA and preview
checks to wire in this plan.

## Engineering critique and system goals

The project should deliver **four independently qualified capabilities**:

1. **Source integrity**: all input SHA hashes, metadata, resumed checkpoints
   and raw-video safety, with truth-in-progress denominators.
2. **Event discovery**: find all meaningful candidate intervals, including
   brief action and near-static wildlife, minimizing miss rate under
   reasonable review/compute budgets.
3. **Temporal event understanding**: detect plausible causes, event phases,
   complete approach/action/outcome/recovery timelines and uncertainty.
   Camera movement is not athlete kinematics. Species and actions must
   remain unknown until source evidence is sufficient.
4. **Personal editorial selection**: given the event evidence plus a
   specified user goal, choose clips with suitable context, pacing and
   technical quality; then let humans amend/reject.

Do not measure a better source candidate denominator as a better learned
model. The four stages have separate metrics and test sets.

### Intended architecture

```text
immutable source SHA + ffprobe + source clock
  -> low-cost global coverage + stratified controls
  -> scene boundaries / source-PTS alignment
  -> candidate unions and coverage audit [this PR]
  -> targeted evidence acquisition (finer frame bursts, audio, object tracks)
  -> temporal hypothesis graph with explicit abstention
  -> candidate clip and trim variants (protect event core and context)
  -> blind review, human value + reasons + pairwise preferences
  -> retrieval and small goal-conditioned preference head
  -> held-out outing / future recording evaluation
  -> reviewed reel, never automatic publishing
```

## Separate discovery vs ranking experiments

**Candidate-recall experiment.** Independent random source-time annotations
must identify the true event set (including missed actions outside any
candidate windows). Compare event recall at fixed review budgets between
uniform source sampling, motion heuristic, SigLIP2 adaptive, temporal lite,
finer video backbone and union/ensemble. A candidate generator cannot
achieve high recall simply by picking favorable samples from its own output.

**Boundary experiment.** For human identified event cores, compare fixed
context, change points and event-phase-aware trims. Measure missing context
and boundary error against manual source-time annotations. Equalize clip
duration where possible.

**Preference experiment.** On the same verified candidate pools, compare
human ranking baselines to semantic, temporal, retrieved-episode and
goal-conditioned preference heads. Keep subjective value separate from
technical quality. Split by actual outings or conservative capture-day
groups and reserve a prospective future-shoot set.

**Efficiency:** Track bytes read, SHA hash passes, decoder runtime, frame
counts, model inference, false negatives, annotation minutes and useful
discovery rates. Escalate heavier models only for informative or uncertain
windows. Keep a fixed exploration floor for unknown/quiet footage.

**Data contracts:** Event, Observation, ClipVariant, HumanFeedback,
GoalContext and ModelRun carry source SHA, source-time PTS, model versions,
uncertainty, provenance and immutable snapshot receipts.

**Sampling safety:** An unreviewed interval is not a negative. A rejected
shot is not a rejected source. Proposed “success” and species identity
must not become truth. Sensor/IMU or optical flow claims require
source-supported calibration. Preserve raw originals and bystander privacy.

## Acceptance gates for this PR

- No original media paths accessed or altered, even if the catalog points
  to an unmounted external drive.
- Invalid source receipts do not count toward verified completions or
  candidate coverage; failures/blocks and unknowns remain distinguishable.
- Candidate union duration deduplicates overlapping windows.
- Source-first plan maximizes distinct sources subject to actual activity
  and control availability and requested budget.
- Every row is `observedEvent: unknown`, `humanApprovalRequired: true`.
- Deterministic identical output for the same audit/scan-state input.
- All report files within bounded workspace reports/exports paths.
- CI synthetic fixtures cover partial scans, tampering, symlink escape,
  source safety, invalid intervals and selection coverage.

## Next engineering PRs (do not merge all in one transaction)

1. Correct source-status report counters with corrupt reports, separately
   from scan success receipt; add wall-time-safe decoder supervision.
2. Integrate the selection roster as an opt-in immutable packet input,
   preserving current packet semantics as control arm.
3. Add PTS-correct multiscale temporal candidate generation using short
   verified bursts, with bounded quiet exploration.
4. Build a source/outing/event graph and human review corrections, including
   event existence vs personal value vs film quality.
5. Benchmark frozen video representations against existing SigLIP2 using
   held-out real outings; only then train lightweight goal-aware ranker.

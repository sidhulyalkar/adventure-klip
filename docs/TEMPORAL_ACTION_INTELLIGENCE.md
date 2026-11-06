# Temporal Action Intelligence: qualification and implementation roadmap

Status: **research-stage / opt-in**. The first code increment is Temporal Lite v1.
Branch dependency: this PR targets `feat/full-archive-highlight-campaign-v1`
(PR #76), which itself belongs to the unmerged Adventure-Klip PR stack.
It MUST NOT be merged directly into an unrelated or older base without
re-running integration, data-safety and CI gates.

## Scientific objective

Determine whether **temporal structure**, beyond interesting individual
frames, improves the retrieval of complete, human-preferred POV action moments.
Separate four falsifiable tasks:

1. **Observation:** image/audio/motion changes at known source-relative times.
2. **Event hypothesis:** what action/encounter may have occurred and its phases.
3. **Editorial hypothesis:** boundaries that make an experience comprehensible.
4. **Human decision:** whether the result is worth keeping and why.

Do not collapse (1) into (2), or (2) into (4). A camera shake is not a
landing, an object query is not a species ID, and camera handling sound is
not proof of breathing, an accident, or the wearer's intention.

## Repo integration map

Already present across the open branch stack:

- PR #28/#29: adaptive frame scheduling, batched source-verified decoding.
- PR #37/#38: sport-specific scene segment hypotheses, temporal sampling,
  marine encounter context.
- PR #49: seven-frame local temporal second look with abstention.
- PR #62/#64/#65: numerical proxy evidence, blinded comparison, scene stories.
- PR #70/#71: rights-aware annotation record and bounded human-label pilot.
- PR #72–#76: review-to-discovery, reel feedback, recovery, archive campaign.

New in this PR: **frozen-model, cache-only visual change graph** that accepts
completed SigLIP2 semantic caches. It does not automatically influence
highlight ranking. This is a comparison arm, not a replacement for the
above source-verified systems.

## Working v1 contract

```shell
klip --workspace "$WS" temporal-lite \
  --asset FULL_LOWERCASE_SHA256 \
  --output "$WS/reports/temporal-lite-ski-001.json" \
  --change-threshold 0.20 --max-gap-ms 7000 --support-radius-ms 3500
```

Requirements:

- Source asset must have indexed duration. Cached semantic job must be
  checksum-verified by the existing `frames_for_asset` reader.
- Model/version/preprocessing must resolve to a single frozen `modelKey`.
  Do not mix vectors of different dimensions or silently substitute
  decoded source PTS for requested, video-relative milliseconds.
- Reject duplicate sample times and malformed parameters. Report missing
  semantic coverage explicitly as **unknown**, not as low activity.
- Form visual-change candidates only between *adjacent, sufficiently
  close* valid samples, using uncalibrated cosine distance.
- Segment supported frame intervals conservatively, then create simple
  chronological edges. No verified event labels or publishable clip edits.
- Read no original media, download no model, modify no database labels,
  export no clips, upload no media, and create only a **new JSON report**
  inside the workspace's reports/ or exports/ path.
- If there is no comparable cache, abstain. The report remains useful as
  a missing-evidence diagnostic.

A 0.20 cosine-distance change threshold, 7-second maximum link gap,
and 3.5-second support radius are **initial inspection knobs**, not
calibrated values. They must not be optimized against held-out trips.

### Output interpretation

`segments`: sampled-visual-support regions, **not** action phases.

`visualTransitions`: candidate visual changes between close sampled frames,
**not** guaranteed edit boundaries, events, or athletic maneuvers.

`unknownIntervals`: source-relative intervals without supported evidence;
coverage is a diagnostic, not event recall.

`edges`: chronological adjacency only; `continuityVerified=false`.

`humanApprovalRequired=true`: no automatic publishing or promotion.

## Development plan: small, separable qualification PRs

| Stage | Deliverables | Exit gate |
|---|---|---|
| T0. Temporal Lite (this PR) | Cache-only graph, source-safe CLI, abstentions, deterministic unit/integration tests | All synthetic invariants pass; zero original-media reads and output overwrites |
| T1. Source-timing cross-check | Compare requested timestamps, decoded PTS, H.264 preview mapping and final rendered frame PTS on real HEVC/VFR cases | No unaccounted timestamp shifts; explicitly abstain where alignment unknown |
| T2. Multiscale segmentation | Preserve micro transitions, medium events and long contextual episodes; overlay human exclusions and event-core protection | No clip proposal crosses an explicit human exclusion; don't erase a confirmed event core |
| T3. Action and object motion | Ego-motion-compensated tracks, approach/departure, skiing takeoff/airborne/landing *hypotheses* | Show gains on manual event-phase labels over flow, object boxes, and frame-only controls |
| T4. Optional frozen video encoder | Contiguous 1–4 s bursts, frozen short-video embeddings, timestamps, versioned model provenance | Better held-out human event completeness or preference at justified measured compute cost |
| T5. Editorial preference | Blind clip variants, revision-linked reasons, event-completeness-aware ranking | Pre-registered human study; no worse missed-event rate and no missing consent/provenance |

Stage T0 can be run without an SSD connected if cached embeddings are
complete. T1–T5 have separate access budgets and require explicit opt-in
for source decoding or licensed training. Never run a full-archive heavy
model pass to validate the first experiment.

### Action hierarchy to evolve toward

- **Micro (0.25–2 s)**: camera versus object motion, scene cuts, onset,
  acceleration, transient audio, occlusion.
- **Meso (2–30 s)**: candidate encounter, turn, jump and landing, recovery,
  approach and departure, surface transition.
- **Macro (10–120+ s)**: challenge, exploration, discovery, execution,
  consequence and resolution. A macro event may contain several micro
  and meso events with different confidence.

These durations are hypothesis ranges, not natural-law or learned parameters.
Each level gets separate evidence, provenance and abstention conditions.

## Controlled evaluation

### Freeze an auditable dataset

Initial experiment: **120 distinct review windows** spread across at least
3 independent outings per included activity (ski, bike, snorkeling), if
available in the user's verified personal archive. Do not invent footage,
labels, dates or sample counts to satisfy quotas. If only fewer distinct
outings exist, report the limitation and treat results as exploratory.

Stratify source windows independently from model scores:

- known Keep clips, random timeline intervals and missed-event challenges;
- chairlift, flat trail, static reef, bubbles, head-bob, pans and blur;
- small fast events, subtle animal approaches, long trick sequences;
- high novelty with low editorial value and low novelty with high value.

A single recording's windows, near-duplicate captures and one outing all
belong to exactly one train/validation/test group. Freeze selection IDs and
baseline feature versions before human preferences are exposed to ranking.

### Four controlled arms

A. Existing ranked highlights and deterministic trim policy.

B. Same ranking with simple fixed before/after context.

C. Temporal Lite change graph and multiscale grouping on cached image
embeddings; same output duration budget and other ranking controls.

D. A frozen contiguous-video model, using the **same candidate set and
time/compute budget**, run only after a small feasibility benchmark.

Temporal Lite alone currently proposes *visual support segments*, not
complete editorial clips. T2 must implement the actual candidate-clip
conversion before A/B comparisons can claim C improves editing.

### Human protocol

1. Independent annotators mark observable onset, peak, resolution,
   interruptions, uncertainty and whether the footage is complete.
2. Review clips from the **same source event** as blind, randomly ordered
   A/B variants, ideally with at least two independent human judgments
   per comparison. Include ties and `cannot_tell`.
3. Ask independently about completeness, pacing, framing and technical
   quality. Aesthetics must not be conflated with action accuracy.
4. Record time-to-usable-edit; do not let users see model name, proposed
   confidence, condition, or previous votes.
5. Evaluate annotator disagreement and stratify by sport, trip, event
   duration, lighting and camera orientation.

**Primary endpoints:** blind preference over A, human event-core retention,
and editorial seconds per usable kept clip.

**Secondary endpoints:** boundary median/p90 absolute error, discoveries
per 20 reviews, recall in random-source windows, candidate coverage,
stale/missing-cache abstention, repetitive-shot rate and compute per
reviewed retained event.

Use trip-grouped bootstrap uncertainty intervals and show all exclusions,
ties and abstentions. A *suggested prospective promotion rule*, to freeze
before the real study: preference over A has a 95% interval whose lower
bound exceeds 50%, while event-core retention does not regress beyond an
agreed margin. Do not describe an exploratory positive result as proven
generalization to all sports or creators.

### Training permissions

Raw media stays local by default. Any external reaction video, bystander
voice, or source from another creator must have documented rights and
training authorization before inclusion. Reference-only material cannot
be silently promoted into training input. Keep labels and model-generated
hypotheses in separate provenance fields. Group-split leakage, consent
revocation, and collaborator access must be auditable.

## Engineering release gates

- **Correctness:** 100% of synthetic tests for bounds, missing evidence,
  duplicate timestamps, heterogeneous embeddings, determinism and abstention;
  no exceptions hidden by blanket error fallbacks.
- **Source safety:** original bytes unchanged; only immutable cache reads;
  no uploads, automatic export, automatic label changes or overwriting prior reports.
- **Regression:** existing review, preview, adaptive scheduler, and rendering
  tests green on the current stacked base.
- **Reproducibility:** frozen model key, policy thresholds, timestamp basis,
  exact asset SHA and version in every output.
- **Performance:** record peak RAM, CPU/GPU runtime, model seconds,
  decoded frames and source hash I/O *separately* before making speedup claims.
- **Human value:** promotion only after random-control and blind-preference
  study. No hidden use of test-set labels to tune thresholds.
- **Operational:** full archive scans remain dormant until the current
  campaign has a validated resumable checkpoint and SSD status is verified.

## Immediate operator checklist

1. Review and run CI for this stacked draft PR in isolation.
2. On a *copy of the workspace or known healthy cache*, run Temporal Lite
   for one ski, one bike and one underwater asset with homogeneous
   completed SigLIP2 cache (if available). Do not guess asset IDs.
3. Visually inspect JSON graph boundaries against original footage in
   Studio; record false visual transitions and unknown gaps.
4. Compare exact same candidate windows with T0 disabled/enabled.
   T0 must not alter ranking; it is a diagnostic only.
5. Freeze a small, independent human annotation set and then implement T1/T2.
6. Only after robust T0–T2 qualification, decide whether additional
   frozen video models improve results enough to justify memory and cost.

## Open-source contribution direction

A future public benchmark should contain synthetic and explicitly
redistributable short clips, timestamps, phase labels, rights manifests,
blind-pair annotations, evaluation scripts, structured model provenance,
and negative footage. Do not commit personal DJI originals, private
human ratings, audio containing bystander identities, or model weights.
Contributions should be reproducible without a paid GPU or proprietary API.

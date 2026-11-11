# Klip Scene Intelligence: evidence, situation, relevance (research V0)

**Status:** research proposal + executable, offline, evidence-only scoring
prototype. The code does not watch a video, run a model, learn parameters,
revisit SSD sources, assign ground-truth species, estimate GPS/ski speed
from optical flow, or approve an edit. The weights are **hypotheses**.
Human goals are not necessarily observed events; source footage must
remain immutable.

## 1. The problem and information hierarchy

A clip is not one label, one frame, or one score. It is a *temporal
subgraph* of an experience. Build four distinguishable layers:

1. **Observations:** timestamped video/audio/telemetry; framing, visibility,
   object tracklets, visual ego-motion, independently verified movement,
   scene context; each with a known source, evidence receipt, sampling
   coverage and timing uncertainty.
2. **Hypotheses:** action, encounter, species, consequences, relations and
   event phases. Proposals remain hypotheses until sufficient independent
   evidence or human adjudication exists.
3. **Episodes:** linked events with source identity, start/onset/peak/outcome
   hypotheses, recurring subjects, situational context, trip/session,
   alternatives, missing intervals and relations between scenes.
4. **Editorial decisions:** purpose, target audience, intrinsic value,
   preserved memory, trim plan, story role, diversity, constraints,
   human approvals and subsequent feedback.

A scene graph can be represented as G_t = (V_t,E_t), where V holds subjects,
scene regions and observed environmental entities, E holds grounded spatial
and interaction relations and edges carry temporal extent/confidence.
An episode graph connects G_t over time with *hypothesized* states
(setup -> onset -> event core -> resolution), including ambiguous/missing
states rather than assuming a complete sequence.

Key traps:
- Camera shake != skier accelerating, falling or jumping.
- Sparse object detection != proof of a species, interaction or absence.
- Easy motion != low personal meaning; a failure != uninteresting footage.
- Optical flow alone cannot establish actual meters/second, nor can audio
  transients establish breath/impact causes without corroboration.
- "Skip this derivative" != "discard the source".
- A clip can be appropriate for multiple goals, or only for research.

## 2. A transparent mathematical prototype: KSRE

Klip Scene Relevance Envelope (KSRE) begins with a **multi-dimensional**
feature vector, not a trained or globally valid "excitement" number:

d = {event importance, arc completeness, subject readability,
     aesthetic composition, technical clarity, situational context,
     archive distinctiveness, personal meaning, learning potential,
     trajectory continuity, relational interaction}.

For a candidate c and measured/annotated feature d, record:
- q_cd in [0,1]: source-backed assessed value under an explicit rubric;
- r_cd in [0,1]: **declared evidence strength**, not a calibrated
  probability and not a measure of biological/physical truth;
- p_cd: evidence source and immutable provenance reference.

Missing evidence and unreviewed model-only hypotheses remain UNKNOWN.

Define the *sensitivity envelope*, not a confidence or credible interval:

    [L_cd,U_cd] = [r_cd * q_cd, r_cd * q_cd + (1 - r_cd)]

For unknown or model-only hypotheses:

    [L_cd,U_cd] = [0,1]

For editorial goal g, declare nonnegative weights w_gd that sum to 1.
The provisional score range on a 0..100 scale is

    KSRE_g(c) = [100 * sum_d(w_gd * L_cd),
                 100 * sum_d(w_gd * U_cd)]

Weighted evidence coverage is

    Coverage_g(c) = sum_d w_gd * [1 - (U_cd - L_cd)].

These values are interpretable **bounds under declared assumptions**, not
statistically calibrated prediction intervals. For example, an unobserved
highly personal moment can have a wide personal-memory utility interval
even when visual aesthetics are poor. A candidate with only a language-
model hypothesis must not acquire a high verified utility score.

Goal weight configurations presently include sport_action, nature_encounter,
scenic_transition, personal_memory, and technique_analysis. Their numerical
weights are research defaults, not fitted to the review export. Keep the
underlying dimension measurements unchanged when the goal changes.

A 4-phase completeness *measurement* is possible after an event-specific
human assessment: weighted observable coverage for setup .20, onset .20,
peak .35, resolution .25; unknown phases retain unknown bounds. This is
not the same as event classification. Not all event kinds have a meaningful
resolution; phase schemas should eventually be event-specific.

## 3. Discovering the environment and situation

### Visual scene
Local, time-indexed embeddings and object proposals should describe
scene composition, independent subjects, visibility, occlusion, water
submersion, terrain, barriers and camera conditions. Use frozen model
version IDs. Separate detection confidence from verified category/taxon.

### Temporal scene
Encode change points, independent object tracks, optical flow corrected
for camera motion (e.g. robust homography with failure detection), camera
orientation, audio envelopes and segment-level visual representations.
A true event proposal needs a coherent *multi-frame* arc, not a high
cosine distance of successive still embeddings. Keep source PTS and
decoded PTS explicit and test fast-seek preview parity before editing.

### Relational scene
Support hypotheses such as wildlife passing a reef, a ski line
approaching a feature, a turn preceded by difficult snow, a fall followed
by slow cautious skiing, or a recurring family scene. Require evidence
for each entity and edge; do not infer causality from temporal adjacency.
"Fatigue caused crash" may be a user's first-person hypothesis; film
alone cannot verify fatigue.

### Episodic memory
Build a local search index of source SHA, approximate time, observed
subjects, context, temporal phases, same-trip links, goal-specific
utility and uncertainty. Neighboring clips and similar encounters can
then be retrieved and compared without losing meaningful source history.

## 4. From single-scene relevance to *portfolio* relevance

A great 90-second reel is not merely the eight highest-scoring scenes.
It must be temporally coherent and cover different valuable experiences.

For a set of human-approved/verified candidate intervals A and fixed
duration budget B, a proposed later objective is

    maximize_A sum_{c in A} LowerKSRE_g(c)
             - lambda * sum_{i<j in A} similarity(c_i,c_j)
             - mu * edit_cost(A)
    subject to total_duration(A) <= B,
               source-time bounds and rights/approval gates.

Similarity must have evidence, stable embeddings and source/trip grouping.
The prototype includes only a *pairwise duplicate-penalty helper*,
not an optimizer, inference engine, renderer or deployment-ready score.

Archive distinctiveness must be compared within verified exposure and
species/action strata. A globally rare event is not automatically a
valuable event. Never inflate rarity from repeated crops of one recording.
An eventual encounter/exposure rate could be smoothed as

    rate_z = (unique_verified_encounters_z + alpha)
             / (verified_observed_hours_z + beta)

Normalize within the appropriate event class, trip coverage and detection
effort before using it as a utility dimension. Long videos with sparse
sampling are not proof of "no encounter".

## 5. Active understanding, not just high-confidence selection

Retain two disjoint queues:

- **Editorial queue:** source-verified and human-accepted candidates,
  prioritized by goals, completion, visibility, variety and accepted edits.
- **Evidence-investigation queue:** wide uncertainty, reviewer disagreement,
  missing event phase, rare-event hypothesis, duplicate ambiguity or
  potentially recoverable poorly framed shot.

High upper bounds with large uncertainty call for *inspection*, not
publication. A low-quality rare event should prompt checking adjacent
timestamps or alternative takes instead of automatically discarding it.

## 6. Concrete failure-oriented examples

**Wildlife:** A clear ray approaches, accelerates past coral, interacts
with other visible animals and leaves the frame. Relevant dimensions:
subject readability, relational interaction, arc completeness and
archive-level distinctiveness. A blurry ray hypothesis stays uncertain,
even if another clearer take exists.

**Ski action:** A jump line requires preparation, takeoff, airborne
movement and landing. Camera pan alone does not prove success. Preserve
continuous runs for coaching, but use a shorter peak-centered edit for
an action reel after source-aligned boundary review.

**Coaching:** A crash may have high learning potential despite poor
aesthetic quality. Preserve observable preconditions, fall and outcome.
Distinguish human-supplied hypotheses about fatigue/technique from
corroborated video measurements.

**Scenic/people:** A family kayak scene or a lake view may carry very
high memory or transition value with low event intensity. This is a
different optimization goal, not a false positive action detector.

## 7. What is implemented in this PR

- src/adventure_intelligence/scene_relevance.py: pure typed evidence
  records, strict normalized inputs, goal-conditioned bounded scoring,
  unknown-propagating phase coverage and optional pairwise penalty.
- scripts/klip_scene_metric.py: read-only offline JSON input evaluator.
- examples/scene_evidence_synthetic_v0.json: artificial input fixture.
- tests/test_scene_relevance.py: uncertainty, missingness, provenance,
  source types, goal independence, phase handling and diversity regressions.

Try from an installed project environment:

    python scripts/klip_scene_metric.py \
      --input examples/scene_evidence_synthetic_v0.json

It prints a JSON report only; it does not read original media or alter the
workspace. Importantly, the 117-review export **does not already contain**
most of these assessed features. We cannot retrospectively invent values
from narrative text and declare the metric calibrated.

## 8. Next controlled experiments and exit gates

P0: Freeze existing blind reviews and source manifest. Preserve exact
timestamps, source/event/trip identities, missingness and explicit goals.
Reconcile packet count versus submitted review count. Archive all original
free-text notes privately.

P1: Independently annotate 30-60 goal-stratified *episodes* with observable
phases, subject/action visibility, editing intent, personal importance,
relation evidence and 2-3 preferred alternative cuts. Include random
source windows, negative controls, failed renders and uncertain footage.

P2: Compare four evidence tracks with the same sampling budget:
(A) current motion/novelty, (B) frozen frame embeddings,
(C) dynamic multi-rate proposals and temporal change graph,
(D) optional frozen clip/video encoder.
Record energy, CPU/GPU cost, decode failures, provenance and frame/PTS
alignment. No training required for initial ablations.

P3: Fit objective weights only using independently approved human
preferences. Possible pairwise utility model:

    Pr(c_i preferred to c_j | g)
      = sigmoid(theta_g^T [f(c_i) - f(c_j)]).

Pairwise preferences must be collected **under the same goal**, not
inferred from arbitrary keep/skip decisions. Avoid random frame splits:
hold out source families, outings and ideally geographic/trip contexts.

P4: Evaluate scene event recall (random source coverage matters),
human-accepted top-K clips, annotation/selection time, temporal
intersection-over-union and phase-retention, pairwise preference
accuracy/calibration, duplicate ratio, coverage, abstention, quality
across activities, and cost per accepted minute. Audit rare encounters
and personal memories as separate strata. Compare against the unchanged
existing baseline.

P5: Only after reliable held-out improvement, integrate a goal-aware
ranking policy into the existing campaign-reel planner. Never allow an
uncalibrated model score or keyword heuristic to delete raw footage
or produce an automatically published reel.

## 9. Safety and epistemic limitations

This metric quantifies *declared evidence and editorial assumptions*.
It does not demonstrate visual scene understanding on real footage.
Numerical scores should be shown with their intervals and supporting
evidence, never reported as objectively verified importance or certainty.
Observational and model-derived evidence need independent qualification.
Publication, external enrichment, telemetry ingestion, and source
alteration remain gated separately.

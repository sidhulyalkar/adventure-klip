# Klip V3 proposed architecture and evidence contracts

**Status:** design proposal. A full source-wide scene and event scanner is NOT yet implemented.

## Dataflow

    Source recordings + verified catalog
           |
    Coverage index: source SHA + spans + model/cache keys
           |
    Coarse full-timeline observations, including random/periodic controls
           |
    Broad overlapping SCENE HYPOTHESES
           |
    Specialist temporal event analysis on promising / uncertain intervals
    ski: approach, feature, landing, linking turn, run
    water: habitat, subject, approach, behavior, departure
    bike: terrain, action, payoff, flow
           |
    Grounded description + event timeline + uncertainty
           |
    Candidate full-scene and short-moment edit variants
           |
    Goal-conditioned ranking + story and diversity constraints
           |
    Blind owner review: description, why keep, Keep/Skip/Unsure,
    filming intent, personal meaning, optional animal/trick evidence
           |
    Human evidence ledger (separate from machine proposals)
           +---> outing-held-out benchmark and ranking-model comparisons
           +---> local reel preview, then approved native master

## Canonical proposed entities

**SourceRecording:** full SHA, duration, PTS/timebase, camera metadata and outing group. Continuity across separate files is a verified hypothesis, not implied by filenames.

**CoverageSpan:** source SHA + [startMs,endMs], stage, model and input fingerprints, sampling schedule, feature schema, completion/missing/error status. Cache reuse only when keys match. Track uncovered intervals.

**Observation:** timestamp/span, kind (subject, terrain, movement, defect, audio), score, calibration state, method/version and evidence frames. A model category is never a human label.

**SceneHypothesis:** overlapping contextual span, setting, activity, subjects, significance hypothesis, alternative interpretations and supporting observations. A scene may contain no specialized sport/animal event.

**EventHypothesis:** onset, phases, changes and possible outcome. Owner point markers do not automatically prove complete action boundaries.

**EditorialCandidate:** source-backed scene/event group plus reversible edit proposal, optional story role, chronology and why it might be worth reviewing. Does not claim finalized cut quality.

**OwnerReview:** immutable sample/version and source identity, explicit Keep/Skip/Unsure, sceneDescription, whyKeep, sceneType, storyRole, filmingIntent, personalImportance, sceneInterest, specialized visible-subject observations, ratings and optional boundaries. Preserve legacy exports via explicit adapters.

**RankedSelection:** goal-specific ordering and rank evidence tier, score provenance, source/outing diversity, exclusions and uncertainty. Distinguish recommended review leads from verified highlights.

**ReelRecipe:** ordered scenes, transitions, pacing, media references, trim suggestions and independent export approval.

## Ranking = three distinct layers

1. **Candidate eligibility:** real source evidence and verified media; unknown/short/model-failed is for review/context, not an automatic negative.
2. **Scene utility:** substantive action/encounter, aesthetic and technical quality, subject readability, narrative completeness, novelty, personal meaning and uncertainty.
3. **Reel composition:** order and diversity by editorial goal, repeated subjects, source/outing caps, transition continuity and target duration.

Do not count model-suggested tags and present the sum as aesthetic quality. A personalized Top-K review queue is not a finished reel.

## Review: scene-first, specialist second

The earlier 57-clip snorkeling surface led with animal presence. It missed the distinction between a beautiful reef scene and a wildlife encounter. Current new modules:

- scene_review_narrative.py: validated user-authored textual/contextual judgment.
- marine_encounter_review.py: v2 scene-first form with optional animal evidence, legacy JSON import.
- klip_prepare_marine_encounter_review.py: sidecar upgrade of existing frozen 57-clip study.
- complete_highlight_rerank.py: provisional source-safe ranking. **Does not yet score free text or learn from it.**

## Invariants

- Full SHA + source-relative interval + original vs proxy timebase status for every scene.
- Preserve model predictions separately from human text and review choices.
- An old Keep attached to another interval is not transferable merely by filename. Match SHA + time overlap + editorial goal and log conflicts.
- Old review JSON, prediction checkpoints and original footage remain immutable.
- Use source/outing-disjoint holdouts to evaluate any learned scoring model.
- Retain coverage holes and abstentions; do not assume silent footage is boring.
- Never put raw source videos, personal review JSON, media caches or absolute private paths in Git.

## Interfaces to build next, in priority order

1. Read-only evidence index across prior human-review schemas, with conflict records and source overlap. Import does not imply adjudication.
2. Source-wide coverage matrix from cached coarse observations and embeddings; work only on missing spans.
3. Full-timeline scene proposals, with mandatory recall controls and no arbitrary 20-second scene ceiling.
4. Temporal event and subject trackers for ski runs, marine encounters and travel between features.
5. Description retrieval with frozen text/vision representations; held-out Recall@K against owner-written scene explanations and Keep/Skip.
6. Calibrated pairwise preference ranker and goal-conditioned reel sequence planner.
7. Native PTS plus audio render after explicit preview sign-off.

Each ships via a separate draft PR, synthetic tests, a bounded real-footage pilot and a PASS/FAIL evidence report. Do not begin a whole-library re-decode before proving recall on the approved park line and representative underwater scenes.

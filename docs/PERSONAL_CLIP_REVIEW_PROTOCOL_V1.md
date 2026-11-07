# Personal clip annotation protocol v1

This protocol prepares review/evaluation and does not require raw SSD video
access. Apply it only to verified playable clips once the active source scan
is idle. Intended to preserve objective visible evidence, personal utility,
technical filming quality and uncertainty as *separate measurements*.

## 1. Two-stage review UI

**Stage A: what can be observed?** Review model-score-blinded clip, with
optional wider context available, and log:
- `event_presence`: observed / not_observed_in_window / cannot_tell.
- `event_family`: skiing, biking, snorkeling, landscape, other, unknown.
- `event_kind`: optional human text, not forcibly assigned from a known list.
- `phase_visibility`: approach, initiation, action, outcome, recovery as
  visible / not_visible / ambiguous, with source time spans.
- `outcome_status`: visible_success / visible_failure / not_visible /
  ambiguous / not_applicable. No event automatically equals success.
- `scene_context`: terrain, water visibility, camera orientation, other
  actors and distinct objects as actually supported.
- `uncertainty_reason`: occlusion, camera movement, cut, bad lighting,
  distance, missing context, insufficient model resolution.
- `timing`: source start/end in ms, adjusted bounds, need for more context.

**Stage B: why does it matter to this user?** After saving Stage A:
- `goal`: athletic_skill / wildlife_nature / personal_memory /
  cinematic_story / other.
- `personal_value_1to10`: optional ordinal label, always WITH goal.
- `keep_decision`: keep / skip_window / unsure / cannot_tell.
- `reason_tags`: technically_difficult, novel_situation,
  athletic_execution, unusual_environment, animal_behavior,
  shared_memory, beautiful_scene, clear_story, audio_value, or
  user_defined. Multiple tags permitted; these tags are user reports,
  not automatic model event ground truth.
- `technical_quality_1to10`: optional independent rating;
  note blur, exposure, stabilization, POV, audio, and framing.
- `editorial_action`: use_full_clip / trim / extend_context /
  find_another_angle / defer.
- `user_notes`: optional private free text.

A `skip_window` applies ONLY to this goal and this interval, never to a
whole source and never as evidence that an action didn't happen.
A `cannot_tell` is not a negative training label.
For personal memories, an event can be valuable without being cinematic.

## 2. Anchor examples for optional 1–10 ratings

These anchors guide human consistency, not a model's claimed knowledge:

- 1–2: Little personal value for the selected goal.
- 3–4: Some recognizable interest, not worth a standalone clip.
- 5–6: Potentially worth saving with additional context or a tighter edit.
- 7–8: Clearly worth keeping as a standalone event or part of a story.
- 9–10: Especially meaningful for this goal; would actively revisit or share.

A perfect camera shot can still have low `personal_value`, and personally
important moments can have low `technical_quality`. Store both.

## 3. Pairwise comparison

On clips from different recordings/episodes, ask:
"Which clip better serves the selected goal?" A / B / tie / cannot_tell.
Allow one short reason and optional request for more context. Randomize
left/right placement and hide system scores and selection roles. Avoid
near-duplicates from the same outing in both train and holdout.

Pairwise preferences are evidence for ranking, not for whether an event
occurred or whether a species is correctly identified.

## 4. Sampling plan and failure preservation

First usability packet: 24–40 verified playable clips, balanced across
observed activities and quiet/motion/random candidate origins. Purpose:
discover missing context or confusing UI, NOT train a generalizable model.

Benchmark: 150–300 unique clip windows selected across source/outing,
activity and source duration. Plan roughly balanced quiet controls,
high motion, semantic novelty, uncertain transitions and random windows;
adjust as real availability permits, record actual denominators.

Blind baseline predictions to avoid anchoring.
Hold out entire confirmed outings/capture days before any training.
Treat unknown outing identity conservatively.
Resample annotator uncertainties rather than filling them with model labels.
If user explicitly makes a correction, maintain history (previous value,
new value, time, reason) instead of mutating the frozen record.

## 5. Privacy and provenance

Save reviews in the user's local Klip workspace, with exact source SHA,
source start/end, preview SHA, annotation protocol version, human/not-human
origin and model run ID (when applicable). No raw originals are uploaded.

Do not conflate camera wearer with the visible actor without evidence;
do not infer feelings, identity, injury or location from ambiguous video.
A memory note can be private and omitted from exported benchmark artifacts.

## 6. Definition of ready

- Stage A and Stage B can be completed independently.
- At least 24 verified, time-linked, playable candidates available.
- Clip bounds are editable and mapped back to original source timestamps.
- Export and reimport round-trip without losing keep/skip/cannot_tell or
  event uncertainty.
- Preview and model score are separated; reviewer can make an unbiased call.
- Frozen holdout and preference training labels never silently overlap.
- All results count missing/unavailable, ambiguous and rejected candidates.

Reference research:
https://ego4d-data.org/docs/benchmarks/episodic-memory/

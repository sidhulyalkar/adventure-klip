# Klip scene understanding: configurable goal x sub-sport profiles (V1)

**Implementation:** a local, deterministic scoring *research* module and CLI
which consume only independently supplied normalized observations, not video.
All numeric priorities, thresholds, and suggested trim lengths are editable
hypotheses. **No dataset was used to fit or validate these weights.**
Human notes do not grant media deletion, uploading or publication permission.

## 1. Why goal x sub-sport, not a single "great clip" classifier?

The same source recording can be valuable for several separate purposes:
action highlights, ecological encounter retrieval, technical coaching, scenic
story structure, and personal memory. Even within skiing, jumps, tree lines,
moguls, slow carving, halfpipe and crash analysis need different features.

V1 preserves the original 11-dimensional KSRE V0 metric as a foundation and
adds 24 named specialized dimensions. All dimensions are 0..1 normalized
**usefulness** variables under explicit reviewer-defined rubrics, not
automatically computed truths.

V1 has 5 editorial goals, 18 sub-sport profiles and **35 total dimensions**.
The 90 combinations are allowed, even when a particular combination is
unusual (e.g. personal memory during a park session). The default goal is
never silently guessed from footage or inherited from old annotation UI.

### Supported profiles

| Activity | Sub-sport profile | Distinguishing requirements |
|---|---|---|
| Ski | skiing_cruise | Rhythm, consistent framing, route progression |
| Ski | skiing_speed | Long exciting descents, control, optional synchronized telemetry |
| Ski | skiing_trees | Obstacles, terrain complexity, spacing and visual line continuity |
| Ski | skiing_moguls | Turn cadence, precision, terrain, enough contiguous run to assess |
| Ski | skiing_terrain_park | Complete approach, maneuver, takeoff, landing, recovery |
| Ski | skiing_halfpipe | Multiple turns and arcs, continuous line, trick completion |
| Ski | skiing_crash_analysis | Before/fall/after, visible terrain and technique learning |
| Mountain biking | mountain_biking_trail | Obstacles, trail evolution, flow and control |
| Mountain biking | mountain_biking_downhill | Intensity, flow, line changes and terrain |
| Mountain biking | mountain_biking_jumps | Approach, takeoff, landing, recovery |
| Dirt biking | dirt_biking_beach | Acceleration impression, terrain, scenery and framing |
| Snorkeling | snorkeling_reef | Water clarity, habitat aesthetics, uninterrupted submersion |
| Snorkeling | snorkeling_ray_shark | Subject visibility, approach, interaction, departure |
| Snorkeling | snorkeling_fish_school | Group movement and distinct fish/reef relations |
| Snorkeling | snorkeling_shallow_coral | Confined-space navigation, coral detail, visibility and safety |
| Kayaking | kayaking_scenic | Composition, stillness, route progression, transition potential |
| Kayaking | kayaking_family | Meaningful people/context, scenic quality and memories |
| General | general_adventure | Original global-goal baseline |

The labels are **editorial profiles**, not a trained activity recognizer.
A real source's activity, viewpoint and event type should be checked against
source-backed evidence before automatic routing.

## 2. Full feature vocabulary

**Core KSRE V0 (11):** event_importance, arc_completion,
subject_readability, aesthetic_composition, technical_clarity,
situational_context, within_archive_distinctiveness, personal_meaning,
learning_potential, continuous_trajectory, relational_interaction.

**Action and terrain (8):** maneuver_difficulty, terrain_complexity,
control_precision, action_intensity, flow_rhythm, takeoff_landing_visibility,
obstacle_readability, run_duration_value.

**Underwater biology (5):** wildlife_behavior_distinctiveness,
multi_subject_dynamics, marine_habitat_distinctiveness, water_visibility,
subject_proximity_readability.

**Media / observability (6):** camera_stability, occlusion_control,
submersion_continuity, shot_recoverability, audio_event_alignment,
framing_quality.

**Narrative / context (5):** route_progression, social_context,
external_telemetry_alignment, narrative_transition_fit,
safety_context_readability.

All specialized quantities require a declared source, temporal alignment
where relevant, and honest missingness. A subject's large image-space bounding
box does not imply a known distance in meters. Perceived acceleration in
a POV image is not a measured m/s^2, and raw video movement is not ground
truth skiing speed. Wildlife behavior and species hypotheses require explicit
evidence and adjudication.

## 3. Math: independent observations and editable utility interpretation

Let x(c) contain **source-linked** measurements for a clip c. Each observed
feature d supplies q_cd in [0,1], evidence strength r_cd in [0,1], a source
category and immutable receipt IDs. If q is unknown or model-hypothesis
only, it remains [0,1] no matter how confident the model sounds.

For goal g, sub-sport s, and feature d, first compose raw weights:

    raw[g,s,d] = base_goal[g,d] + specialist_additive[s,d]

Then apply optional explicit user overrides and multipliers in that order:

    raw[d] = override[d]                   (if supplied)
    raw[d] = raw[d] * user_multiplier[d]   (if supplied)
    w[g,s,d] = raw[d] / sum_j raw[j]

Any zero-weight dimension is excluded from this profile's utility, not
declared nonexistent. Missing *active* dimensions propagate uncertainty.

For independently supplied evidence values, the **sensitivity envelope** is:

    [L_cd, U_cd] = [r_cd*q_cd, r_cd*q_cd + (1-r_cd)]

Otherwise:

    [L_cd, U_cd] = [0,1]

The 0-100 profile score remains:

    KSRE[g,s](c) =
      [100 * sum_d w[g,s,d] L_cd, 100 * sum_d w[g,s,d] U_cd]

This is **not** a calibrated prediction interval or P(clip is good).
Evidence coverage measures weighted reduction in envelope width:

    Coverage = sum_d w[g,s,d] [1-(U_cd-L_cd)]

The interval midpoint is *only for inspection*. Do not sort unknown clips
ahead of observed clips by midpoint; prioritize further inspection instead.

## 4. Temporal evidence coverage and observations

The new temporal_span_metrics(durationMs, spans) helper takes sorted,
disjoint, grounded [startMs,endMs) intervals with already normalized values.

    observedFraction = observedMs / durationMs
    observedMean = sum_i(deltaTime_i * value_i) / observedMs
    lower = sum_i(deltaTime_i * value_i) / durationMs
    upper = lower + (durationMs-observedMs)/durationMs

The [lower,upper] bounds treat **unmeasured video time as unknown**.
No value is interpolated over a 60-second gap. This helper also reports
the largest unobserved gap, making it useful for selecting a next
inspection window. It does not detect events from video.

### Practical measurement backlog

| Dimension / metric | Candidate measurement | Verification gate |
|---|---|---|
| Temporal completeness | Per-event onset, core, and resolution overlap | Original PTS; independent review |
| Turn rhythm and ski lines | Turn segmentation and time-varying optical flow | Camera/ego-motion compensation, manual labels |
| Jump/landing confidence | Distinct takeoff, flight and landing observation | Multi-frame evidence, uncertainty for occluded landing |
| Real descent speed | Consent-controlled Slopes/GPS/IMU data | Time synchronization and GPS quality |
| Flow and obstacle readability | Terrain masks, track continuity and gap duration | Held-out expert review, no imagined scene geometry |
| Marine clarity | Underwater contrast, turbidity proxy, obscuration | Lighting compensation, source-level quality labels |
| Animal visibility | Annotated track size/time on-screen and occlusion | Entity tracking, avoid fabricated species/distance |
| Animal interaction | Two verified tracks and temporal/relational contact | Reviewer adjudication; proximity not proof of behavior |
| Family/memory significance | Owner-controlled human label | Never inferred from appearance alone |
| Scenic transition value | Edge composition, subject motion, shot stability | Blind A/B assembled edits and trip continuity |
| Novelty / repetition | Similarity over deduplicated source-level encounters | Trip/source split; exposure and missingness aware |
| Camera corruption | Out-of-water frames, drops, blur, 2x metadata | FFprobe + visual validation, no invented restoration |
| Telemetry overlays | Verified activity measurements and scene blank regions | Correct time base, units, overlay review |

For each signal, record producer version, extraction version, frame/PTS
coverage, normalization reference, validity conditions, source SHA,
failure rate and human adjudication status.

## 5. How to customize and evaluate without rescanning your footage

The example only contains **fabricated** synthetic evidence:

    python scripts/klip_scene_profiles.py --list-profiles

    python scripts/klip_scene_profiles.py \
      --input examples/scene_profiles_synthetic_v1.json \
      > /tmp/ksre-synthetic-report.json

Edit the top-level profiles -> subsport -> goal object to apply preferences
to every episode with that combination:

    {
      "weightOverrides": {
        "takeoff_landing_visibility": 0.5,
        "aesthetic_composition": 0.02
      },
      "weightMultipliers": {
        "arc_completion": 1.5
      },
      "parameterOverrides": {
        "contextBeforeSeconds": 8,
        "contextAfterSeconds": 6,
        "minContinuousActionSeconds": 5,
        "targetMaxShotSeconds": 50,
        "reviewIfUncertaintyWidthAbove": 35,
        "reviewIfEvidenceCoverageBelow": 0.65
      }
    }

Episode-level customization overrides top-level values for that episode.
Only declared dimension/parameter names are accepted, and weights must be
finite and nonnegative. Zeroing every weight is rejected. JSON read is
bounded, no output file is written except explicit shell redirection, and
no original camera media is accessed. There are no automatic trim/render,
delete, training, publication or network actions.

**The context parameters are inspection preferences, not executable edits.**
The existing scene-relevance V0 module and CLI continue to work unchanged.

## 6. Correct path toward learned weights and more complete models

A personal preference model could eventually learn theta from the owner's
explicit per-goal pairwise edit comparisons:

    P(c_i preferred to c_j | g,s) =
      sigmoid(theta_(g,s)^T [x_i - x_j])

Use a hierarchical prior to share signal across related sub-sports with
few labeled examples. Train only on independently confirmed observations,
include missingness masks, and regularize toward versioned baseline
weights. Do NOT label all skipped derivative clips as negatives for their
entire source recording.

The dataset must be source-/trip-disjoint for evaluation, not random
overlapping windows. Report top-K acceptance, event recall on random
source windows, frame/time boundary errors, inferred-species/athletic-action
false claims, relative rank agreement, editing minutes saved, calibration
and abstention, across individual profiles and goals.

**Next architectural connection:** integrate this evaluator with an
append-only, receipt-backed temporal scene graph. Score only events with
known source windows and measured feature provenance. Keep outcome and
causation hypotheses separate from what is observed. The V1 code is not
the temporal model itself and does not mean all previous experimental PRs
have been qualified or merged into main.

## 7. Source and release safety

- Raw originals are immutable. No source deletion from user notes.
- No user private reviews, locations, recordings or trip metadata in Git.
- Unreviewed footage is unknown, not uninteresting.
- No assumed speed, animal species, impact force or fatigue from pixels.
- No automatic model download, upload, publishing or source-card scan.
- Configuration changes are auditable research parameters, not evidence
  of improved human agreement until tested on a held-out review set.

# K-MOMENT: Evidence-grounded scene dossiers and "momentworthiness" V1

**Status: design specification, NOT a working video understanding model.**
This is the next research and product specification after K-TIDE's successful
histogram smoke run (46 windows, 16 sources, 5 epochs MPS). Model scene judge
succeeded for 5/16 selected sources; it did not process the entire archive.
No source videos have been inspected while authoring this document. It is
intended for implementation through bounded, independently tested phases.

## North-star question

**What happened, in what context, what can we actually see, what may have
motivated the recording, why does this moment matter relative to the whole
trip and archive, and can it be improved into an honest, compelling clip?**

A moment is not synonymous with optical-flow energy, aesthetic quality,
an animal detection, a model's enthusiasm, or the loudest sound. Good outputs
must simultaneously recover chronology, context, material uncertainty,
subject/action relations, novelty, editor intent and recoverability of the
footage. Preserve unremarkable/failed footage for context and training
controls; never delete originals automatically.

Current V1 infrastructure provides source SHA receipts, completed semantic
features, local visual judging, provisional capture-group assignments,
K-TIDE training, blind retrieval evaluation, low-level optical flow and
a transparent scene-relevance rubric. It **does not yet** perform the richer
cross-video reconstruction, body mechanics, animal verification, or restoration
defined below. Existing local LLM judge uses four evenly sampled 384px
stills per source, not dense video input.

## 1. Four levels of understanding

1. **Visual observation:** snow texture, slope orientation, ski tips, hand,
   waterline, reef, rock edge, occlusion, blur, animal-like silhouette,
   camera roll, audio transient, frame/camera PTS. Recorded with source
   SHA + source-relative interval + model/preprocessor version + spatial
   region/tracking identity where available + quality/missingness.
2. **Physical/situational hypotheses:** skier appears to link turns on
   irregular snow; moving subject approaches reef; apparent launch/landing;
   camera follows an object; repeated background motifs across recordings.
   Attach evidence and alternatives. No inferred muscle activation, exact
   speed, jump height, place, animal species, motivation, or causality
   unless independently measured or corroborated.
3. **Episode reconstruction:** setup -> approach -> action -> result ->
   aftermath, plus overlapping events, camera movements, missing time,
   matched neighboring recordings and repeat encounters. A clip can
   contain several meaningful episodes.
4. **Editorial/explanatory layer:** why a particular interval may be
   distinctive to a viewer, participant or coach; alternatives in archive;
   quality/readability; best trim; optional non-destructive enhancement;
   what remains unknown; what human input would be most informative.

An episode with an unknown ending must not be described as a completed
landing. A nature encounter with ambiguous species must retain coarser
identification. An aesthetically weak but personally important moment
must not be discarded by a single global score.

## 2. Target "scene dossier" presentation

Each candidate clip receives both a **grounded narrative** and a **machine-
readable evidence graph**. The narrative can be thorough (100-400 words for
an important event, much shorter for unremarkable footage); length follows
evidence density, not model verbosity. Every factual statement must link to
one or more evidence spans or carry explicit uncertainty.

Scene dossier sections:

- **Film and outing context:** source-relative times, footage continuity,
  likely sport, filming mode (helmet/handheld/underwater), camera and sensor
  metadata, capture time provenance, related source IDs, known or suspected
  trip, approximate segment of an outing.
- **Where are we?** Observed landscape, terrain, snow/water/lighting,
  distinguishable landmarks, sign text, weather/visibility, orientation
  cues. A named trail, run, resort, latitude/longitude or dive location
  requires corroboration from GPS/route metadata, legible trail signs,
  and/or an owner-verified linked recording. Visual resemblance alone
  yields a **candidate**, not verified geolocation. GPS itself has error,
  so check sensor provenance and geospatial map-match uncertainty.
- **What is unfolding?** A time-linked account of setup, anticipation,
  movement, interactions, outcome and continuation with explicit
  observation gaps. No "landed" unless the landing and stability follow-up
  are actually visible.
- **Movement and performance:** visible equipment and body keypoints;
  camera frame vs athlete frame; turn timing, direction changes, trajectory,
  rhythm, obstacle clearance, stability and quality evidence. In first-
  person footage where hips/knees/ankles are occluded, technical coaching
  is mostly *unobservable*. Do not claim muscle recruitment, contraction
  angle, edging force, injury, precise balance, or actual velocities from
  image-plane flow. Pose estimates need visibility, calibration, uncertainty,
  and optional side-view or inertial sensor corroboration. Explicitly
  distinguish coach interpretation from a physical measurement.
- **Ecology and entities:** likely taxa ranked from broad animal -> family
  -> genus -> species only as supported by visible morphological traits,
  optical clarity, locale (when verified) and reference databases. Avoid
  manufactured certainty. Preserve separate "seen animal", "tracked same
  individual" and "species ID confirmed" status. Add habitat, reef
  morphology, vegetation/terrain context, occlusion and uncertainty.
- **Camera and sound storytelling:** subject legibility, subject occupancy,
  foreground and horizon, image stabilization quality, blur/sharpness,
  focus/exposure/clipping, compression, water color cast, glare, occlusion,
  audio continuity and distraction. Note when the image is deliberately
  rough but event evidence remains valuable.
- **What was the filmmaker likely trying to show?** Intent hypotheses
  based on framing/recapture, deliberate camera following, camera stop/start,
  similar recordings and owner context. An apparently accidental camera
  roll, pocket shot, mis-aim or long setup is a *proposed interpretation*.
  A model cannot read the filmer's mental state. Preserve a distinct
  user-supplied intent field and "intent unknown". Never auto-trash.
- **Why might it be momentworthy?** Recoverable event arc, visceral
  trajectory, unusual interaction, scene revelation, athletic technique,
  emotional/social importance (only when user-supported), scene legibility,
  distinctiveness across verified exposure/related videos, and the viewer
  context for which it matters. List opposing reasons and what evidence
  would change the editorial decision.
- **Suggested edit, with fidelity:** precise source-relative candidate in/
  out boundaries, anticipation/peak/aftermath protection, alternate cuts,
  and optional non-destructive restoration with risk warnings. Raw
  originals must never be replaced.

## 3. Ski run example: *illustrative, not the user's footage*

"At the beginning of the selected interval, the forward-facing view looks
down an open snow-covered slope with a band of trees to the right and
irregular, rounded snow forms ahead. The skier appears to maintain a
downhill line while the camera's horizontal sweep alternates over a short
series of turns. Snow spray becomes visible near the lower edge of the
frame on two occasions. The turn pattern changes as the view approaches
a rougher patch of terrain. Following an abrupt camera roll, the horizon
returns toward level, but the sparse observations do not establish whether
there was a fall or only rapid camera/body reorientation.

The local landscape suggests a ski-mountain setting. The specific run,
resort, elevation, pitch, and GPS location are unknown without a usable
location trail or sign corroboration. Visible ski tips support observations
about their *apparent image-plane alignment*, not precise edge angles,
pressure distribution or lower-limb joint mechanics. The skier's hips,
knees and muscles are not visible and no conclusions about muscular
activation or technique faults are defensible.

If denser frames establish a recognizable approach, several continuous
turns, an apparent challenge, and a stable exit, this could be a compelling
skill/progression segment. The preferred edit should retain some setup,
not only the largest motion spike. Its strongest frame is not necessarily
the moment's most useful start. Better-than-average clarity versus other
similar ski footage must be computed from the relevant ski-run cohort,
not guessed from a general adventure-video average."

All above is a sample of *appropriate language under hypothetical evidence*,
not a report about the current archive.

### Technique interpretation gating

| Claim | Minimum evidence | Allowed output |
| --- | --- | --- |
| "alternating camera sweeps" | temporally aligned calibrated video/flow | observed image motion |
| "linked turns" | repeated ski/trajectory cues, sufficient dense temporal sampling | action hypothesis |
| "clean landing" | continuous takeoff, contact and stable runout visible | observed outcome or hypothesis |
| "ski edge angle = 45 degrees" | reliable full ski geometry and calibrated camera/terrain or instrument | measured only, with error |
| "deep knee flexion" | unobstructed visible joint landmarks and calibrated pose quality | pose hypothesis, uncertainty |
| "quadriceps activated" | verified synchronized physiology such as EMG | cannot infer from camera alone |
| "top of specific resort run" | GPS/map-match or legible trail sign + corroboration | qualified place assertion |

### Nature encounter example

A model may observe a dark ray-like shape near a reef, changes in its
position over consecutive frames, and the camera reorienting toward it.
This is a "possible encounter" rather than a verified eagle ray. Species
confirmation requires diagnostic visible features or human expert review;
a regional distribution prior may filter candidates but cannot turn an
ambiguous silhouette into an identification. A compelling clip may begin
*before* the animal is in focus if the approach/reveal sequence matters.
Water clarity and color, subject scale/occupancy, and the full tracked
entrance/exit trajectory should drive the restoration/cropping plan.

## 4. Archive graph: how adjacent recordings improve context

Represent archive as three linked levels:

- **Sources:** immutable camera-file hash, camera metadata, recording
  start/end time and clock-quality, optional GPS/IMU, scene signatures.
- **Outings/places:** conservative provenance-backed source groups.
  Use time proximity, continuous GPS, overlapping scenery, recording
  continuity, device clock corrections, visible signs and owner notes
  as *different kinds of links*. Do not equate same-day or similar snow
  with same exact run. Mark outing links provisional until validated.
- **Episodes and entities:** object and scene tracklets within a source;
  candidate associations across sources, no automatic same-animal or
  same-person identity merely from appearance; observed vs inferred
  transitions and cross-video near-duplicates.

Roughly:
    P(same_outing | time, GPS, scenery, device, user_context)
may inform *suggested associations* but is not calibrated ground truth.
For held-out training/evaluation, use independently verified groups.
A single long trip may span several days and multiple mountains or dive
sites; distinguish **trip**, **outing**, **run/dive**, **scene**, **event**.

Do not expose private location tracks in exports or public reels by default.

## 5. Two-pass archive intelligence and targeted refinements

**Pass A: discovery and baseline** (all available sources, bounded work):

1. Inventory duration, capture-time quality, camera metadata and checksum.
2. Generate reusable coarse visual + audio + motion/quality signatures.
   Record frame coverage and *actual* decoded PTS alongside requested seek.
   Non-connected or inaccurate segments are explicit missing evidence.
3. Scene-change detection and top-level sport/environment hypotheses.
   Keep negative/quiet scenes and low-quality sources represented.
4. Build provisional related-video neighborhoods; detect exact/near
   duplicates, repeated events and candidate outings.
5. Establish quality distributions **within matched contexts**, never
   from only a hand-picked set of beautiful clips. Emit provenance and
   selection-bias warnings.

**Pass B: evidence-driven deep understanding** (a prioritized queue):

1. Schedule dense decode around high-interest, unresolved, or disputed
   event arcs, plus a random low-salience audit/control sample.
2. For each interval, fuse visual embedding, video motion encoder,
   optical flow/ego-motion, audio, optional GPS/IMU and object tracks.
   Use optional experts for mountain-ski technique, underwater habitat,
   fauna identification, wildlife movement and scene storytelling.
3. Run local multimodal model over ordered short video clips/timestamped
   frames; make structured claims with visible evidence IDs and
   alternatives. Compare multiple prompts or models only when compute
   justifies and disagreements trigger abstention rather than voting truth.
4. Form an evidence-backed event graph, connect scenes, generate rich
   narrative, identify unresolved hypotheses and edit candidates.
5. Update source-level and archive-level quality/relevance baselines.
   Revisit earlier clips when archive context or feedback changes.
   All interpretations are versioned and reproducible.
6. Request owner input for *high information gain only*, e.g. "Was this
   the same run?", "Did you intentionally follow the ray?", or "Which
   version better represents the experience?" Never require 232 manual
   scene labels merely to begin.

Scheduling priority should combine estimated discovery value, uncertainty,
expected reduction in error, diversity, fair coverage of rare activities,
and compute budget; **not just high previous model scores**.
Set hard per-source, per-trip and total budget limits. Retain a seeded
random audit subset to estimate recall and avoid selection bias.

## 6. Quality comparisons and relevance: separate their objectives

Measure distinct, partially independent axes:

- **Technical signal quality:** useful sharpness, exposure clipping,
  compression artifacts, dropped/duplicate frames, noise, motion blur,
  color fidelity, readability, subject scale and audio artifacts.
- **Visual filmmaking:** composition, subject placement, reframing,
  smoothness/horizon, motion readability, coherent entry/exit and shot
  stability. Deliberately shaky action can still be a strong experience.
- **Event evidence:** setup, change, peak and result visibility;
  event temporal continuity, ambiguity and evidence gaps.
- **Sport/ecology interest:** observable movement challenge, visible
  wildlife/habitat interaction, novelty *within verified opportunity
  exposure* and scene-specific content.
- **Personal value:** owner-provided goals, memories, people, skill
  progress and explicit preferences. If never supplied: UNKNOWN.
- **Archive value:** rarity, diversity, redundancy, "best representative"
  of a specific encounter and useful role in a reel.

A percentile for quality must be stratified by comparable activity,
conditions, camera class/setting and footage purpose, with explicitly
reported N and selection-bias/coverage. Until cohort size and coverage
are sufficient, display a descriptive within-cohort rank or unknown, not
a globally calibrated percentile. A video can be physically blurry and
still the archive's only documented wildlife encounter.

### Momentworthiness is multi-goal, not one magic score

For a candidate interval c and selected audience/context g:

    M(c,g) = {
       legibility, event_arc, anticipation, outcome, novelty,
       distinctiveness, authenticity, personal_value,
       audience_interest, editability, evidence_coverage
    }

Each dimension is a source-backed estimate or UNKNOWN, with uncertainty.
Only after preference study may a model learn a goal-conditioned utility
with verified source and split controls. Distinctiveness must compare
against archived *observed exposure*, not a selected highlight pool.
Implement Pareto/goal-specific clip ranking before trying one universal
"viralness" number. Track uncertainty and missingness separately
from low scores.

For a reel under duration budget B, choose a diverse sequence of
non-overlapping source intervals preserving event narrative:

    maximize sum utility(c,g)
             - lambda * redundancy(selected)
             - mu * transition_edit_cost(selected)
    subject to duration <= B, source preservation, rights,
               timeline-continuity and owner approval constraints.

A clip can be personally momentworthy without being visually optimized,
and vice versa.

## 7. Intent, mistakes, and "can be ignored"

Never train a binary "filmed intentionally" or "worth keeping" label
from camera motion alone. Film-maker purpose is latent.

Report:
- intentionalSubjectTrackingHypothesis: yes/no/unknown + evidence
- accidentalOrMisAimedCaptureHypothesis: yes/no/unknown + evidence
- ownerDeclaredIntent: separate editable statement or null
- usableMomentInsideLongRecording: source-relative intervals + evidence
- editorialRecommendation: "review", "possible_control",
  "revisit_later", "suggest_skip_derivative", never "delete source"
- why: missing subject, lens occlusion, sustained camera pointed away,
  start/stop artifact, duplicate, extreme blur, idle interval, etc.
- riskOfDiscardingRareOrPersonallyImportantEvent: explicit unknown/flag

For **suspiciously unintentional footage**, seek a positive reason for
rejection and test recall on a random sample. Long idles, dark frames,
buried cameras, poor focus and redundant takeoffs can still precede
important events. "Skip derivative" never means source deletion,
training-negative truth or permanent suppression.

## 8. Restoration and edit planning

Use source-preserving, reversible derivatives and record a per-operation
recipe + before/after comparison + provenance.

Order from low to high risk:
1. Trim dead lead/tail without severing the approach, peak, landing or
   aftermath; explicitly preserve narrative handles and original audio.
2. Correct rotation/horizon and moderate stabilization with crop/warp
   budget, masked subject or horizon if needed; retain fast movement.
3. Adjust exposure/color/white balance, including underwater backscatter
   and color-cast correction, with color and habitat fidelity checks.
4. Conservative denoise, mild deblocking/sharpening; do not invent
   textures, wildlife diagnostic marks, ski edges or trail signs.
5. Optical flow assisted interpolation or temporal deblur ONLY after
   source/display timing checks and before/after artifact inspection.
   Do not generate missing event evidence or treat restored details as
   proof of species, technique or physical motion.
6. Crop/track subject with aspect ratio, target-platform safe zones,
   horizon and object coverage; test against original field of view.
7. Optional slow motion/speed ramp, with explicit timebase and audio
   transformation; never silently change apparent athletic speed.
8. Offer alternate outputs ("authentic POV", "clean social",
   "technique analysis", "nature story") but always display the original
   beside enhanced versions with one-click revert.

For each derivative measure visibility/contrast/blur, subject loss,
temporal warping, output duration, pixel hallucination risk and human
preference. Quality gain on a metric does NOT by itself prove
greater authenticity or story value. Auto-publish/delete forbidden.

## 9. Validation and acceptance gates

**Stage 0: Contract and observational coverage**
- Versioned episode IDs, source SHA, precise timestamp basis, sampling
  denominator, missingness, model and transform receipts.
- Diagnostic for genuine temporal connectivity and PTS discrepancy.
- No unsupported exact place, muscle activation, species or private
  intention claim without independent evidence.
- Unit tests for source-swapping, evidence outside interval,
  empty/fabricated citations and derived-file write protections.

**Stage 1: Rich, auditably grounded descriptions**
- Annotate >=30 short clips across skiing, biking, snorkeling, quiet
  intervals, near-duplicates and poor-quality controls, grouped by
  verified outings; report actual coverage and inter-rater disagreement.
- Blind human judges score factual support, useful detail, event arc,
  false assertiveness, temporal localization and edit intelligibility.
- Compare coarse 4-still description with a denser context + video
  condition on the *same* clips. Target improvement in grounded
  completeness **without increasing hallucination**.

**Stage 2: Archive-aware quality and triage**
- Compare quality ranking within matched cohorts; inspect false-low
  decisions on rare/only-source encounters. Verify accidental-capture
  proposal with owner feedback, not model confidence.
- Duplicate and near-duplicate retrieval measured by precision/recall
  at outing and source levels. Provide coverage diagnostics.

**Stage 3: Independent meaning and editorial quality**
- Blind human preference across matched candidate pairs, complete arc
  versus static-peak cuts, original versus reversible restorations,
  and contextual versus decontextualized story; randomized review order.
- Held-out by independently verified outings, no leakage from linked
  videos or duplicate events. Report nDCG/recall/coverage and
  temporal-boundary errors with uncertainty and failure examples.

**Stage 4: Adaptive compute and robustness**
- Compare budgeted hierarchical pipeline to uniform inference at equal
  GPU time, decoded minutes and original media reads.
- Track failure reasons by stage and resume identifiers; never repeat
  completed inference by default.
- Test long recordings, VFR and DJI timebase drift, EIS, motion blur,
  occlusion, underwater color, near-black, water bubbles, overexposed
  snow, partial animals and subject exiting frame.

### Implementation order

1. Define and validate the scene dossier/evidence contract.
2. Fix current temporal observability audit and K-TIDE order-loss
   confound before asserting genuine temporal encoding.
3. Add coarse metadata + source-neighborhood graph (provisional only).
4. Implement adaptive dense video sampling with a bounded local VLM
   and compare against 4 stills on controlled examples.
5. Add quality vectors, matched-archive baseline, species/terrain
   hypotheses and reviewer-controlled editorial intent.
6. Introduce non-destructive trim/stabilize/color/edit candidates and
   blind evaluation; build stronger model supervision only with
   grounded examples and approved context.

*Status boundary:* This spec is actionable research design, not a claim
that the current Klip pipeline can estimate exact body kinematics,
identify a resort run from scenery, assign species, know why the camera
was recording, restore lost detail, or rank human memories accurately.

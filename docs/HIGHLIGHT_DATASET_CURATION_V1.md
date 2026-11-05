# Klip: Action Highlight Dataset Curation Contract v1

STATUS: PRE-TRAINING DESIGN. No audiovisual source data is collected, no training is authorized, and no model weights are trained by this specification.

The main learning objective is not one universal excitement score. Train systems that can find an action in unedited source footage, recognize what is observable, explain why it matters to an explicitly selected audience and goal, and assess whether the shot effectively communicates the moment.

## Five independent targets

1. Action and phase: what happened, setup, initiation, peak, outcome, follow-through; mark visually observed versus interpretation versus unknown. Off-camera outcomes are unknown, not landed or failed.
2. Athletic significance: difficulty, execution, feature/terrain context, progression and consequences. Avoid physical speed, rotational force, species rarity or risk claims without measurement.
3. Filming quality: technical image and audio quality, framing, spatial orientation, camera tracking, intentional versus detrimental camera motion, visibility of the important moment.
4. Observer meaning: sport expert, participant, casual spectator, film editor and safety/nature specialist opinions, with disagreement and reasons preserved.
5. Editorial selection: highlight objective, boundaries, buildup, payoff, distinctiveness in the entire reel and personal utility.

Action significance, camera execution, observer interpretation and editorial polish must remain separately labeled.

## Five linked datasets

A. Source event registry: immutable original source hash, pseudonymous outing/creator family, source-relative event time window and any linked camera angles. Store untrimmed setup/outcome context and multiple genuine unknown/nonhighlight controls. Do not mark unseen scenes as negative.

B. Filming/observer quality registry: same event rated for image fidelity, subject/action visibility, technical aesthetics, camera movement, perspective, visual readability, emotional or storytelling payoff as a **preference**, not physiological inference. Preserve matched POV/third-party views and matched raw/edited pairs. A spectacular but low-visibility event and a beautiful but ordinary scene are essential hard negatives for different objectives.

C. Perspective/reaction registry: explicit spoken claims and observer-created rationales, separate source-video and commentary/reaction times, pause/replay/cut mappings, human-verified source-time alignment and observation support. Do not infer observer emotions or intent from face or loudness. Blinded action-only ratings must be frozen before reactions are revealed.

D. Sport expertise registry: universal event-phase ontology plus independent sport-specific judgments and vocabulary. Each sports-specific label needs an evidence requirement and an unknown option.

E. Frozen evaluation benchmark: source/outing-disjoint, randomly sampled source-time controls and hard negatives. Human-confirmed event existence, multiple perspectives, temporal localization, blind within-sport pair preferences and editorial reel evaluations. Never use the evaluation set to train or tune prompts.

## Common cross-sport event schema

All sports share:
context -> preparation -> initiation -> action/peak -> resolution -> aftermath

Each temporal phase has absolute source time in milliseconds, visibility state, evidence kind, annotator, and optional uncertainty tolerance. Multiple actions remain separate event IDs. Model and LLM generated labels are hypotheses, never human-verified ground truth.

Sport extensions:
- skiing/snowboarding: terrain, line choice, carving transitions, approach/takeoff/air/landing, trick attempt and completion only when visible;
- mountain biking: line, feature approach, cornering, drops/jumps, airborne phase, landing, control and continuity;
- skating/longboarding: stance/approach, trick components, grind, contact, landing and continued ride;
- surfing: wave section, approach, maneuver, exit and unbroken ride;
- soccer/team sport: possession/context, tactical opportunity, decisive action, game consequence only when video proves it;
- snorkeling/nature: encounter, subject motion, environmental context, underwater visibility, behavior hypothesis, encounter significance with expert-verified identity and rarity if available.

Video framing bias is explicit: a helmet POV may hide rider body joints; image motion may be camera motion; perceived size is perspective-dependent; no biomechanical quantities are automatically measurable without calibrated sensors.

## Filming rubric: never collapse into one score

Technical: exposure, sensor/compression artifacts, focus, blur, resolution, audio, rolling shutter.
Cinematography: composition, subject placement, foreground-background depth, usable viewpoint, tracking, visual continuity, intentional camera movement.
Observability: approach/peak/outcome is visible, subject occluded, landmark context, unresolvable camera angle.
Editorial: trim boundaries, cut pacing, replay, color grade, slow motion, soundtrack, captions, viewer visual hook and story payoff.

Use raw-versus-edited matched variants. Rate athletic achievement from raw video and filmmaking from each specific variant. Do not transfer a publisher's edit quality to original footage. DOVER/DOVER++ and professional multi-attribute aesthetic datasets such as VADB provide research baselines, not calibrated truth for snow or underwater POV.

## Observer panel protocol

Stage 1: Action only. Raters are blind to algorithm score, commentary and reactions. Watch enough unedited context for setup and outcome. Mark time indices, observed event/outcome, preferred highlight goal, a short rationale, uncertainty, and A/B preferences (A/B/tie/neither/cannot tell).

Stage 2: Filming panel. Separate trained reviewers evaluate technical quality, aesthetics, effective action visibility, framing, camera tracking and continuity.

Stage 3: Reaction reveal. Separately time-align the commentator's claims with the original action, including delays, replays and cuts. Record what was literally said, what the viewer believes it means, and whether visual evidence supports it. Do not overwrite blinded stage-1 ratings.

Stage 4: Technical adjudication. Qualified athletes or naturalists review specific uncertain claims and disagreement. Factual action adjudication can be resolved, but subjective preference disagreements must remain labeled per observer.

Stage 5: Editorial sequence. Reviewers independently choose a diversity-preserving fixed-length reel with good context and minimal duplicate source families. This is a separate task from finding single best clips.

## Sampling, inclusion and negative controls

Initial curation budget hypothesis:
- 25% high-scoring algorithm-proposed moments;
- 25% medium/low-scoring or near-tie moments;
- 25% independently randomized source-time windows, especially from unrepresented sources;
- 25% explicit counterexamples: great feat poorly shot, beautiful scene without action, loud reaction to failure, success with no reaction, non-highlight with edited music, incomplete off-screen landing, duplicate slow motion.

Within each stratum cover sport, camera viewpoint, resolution, lighting/weather/water visibility, action outcome, event duration, source outing, athlete/filmer, shot editing and novice/expert audience. Pilot proportions are deliberate stress tests, not a presumed natural-world event distribution. Unlabeled is UNKNOWN, not negative.

Diversity across recording source and outing matters more than collecting thousands of similar jump cuts or multiple views of a single famous event. Random source-time controls are needed to measure missed discovery events, not just ranking precision within already-proposed highlights.

## Train/validation/test leakage and provenance

Treat the same original action, copies, crops, slow-motion edits, reaction reposts, camera angles, short clips and frames as one source family. Never split members across train/validation/test.

Where feasible also group by outing/day, athlete, filmer, creator and event. Keep held-out conditions for unseen places, light/snow/water conditions, camera mounting, sporting disciplines and unedited POV footage. Cross-sport transfer is only valid if held-out domains were excluded from tuning as well.

Use content hashes, source family, annotation version, permission ledger, split-frozen snapshot, exact clip boundaries, observable fields, rater role, rationale, missingness and provenance in every example.

## Rights and privacy gates

Only use clips delivered through a permitted source, with documentary evidence of applicable rights for original/action media, reaction host, embedded third-party clips, soundtrack/music, voice/participant consent, derivative and model-training permissions, plus retention and revocation. Public YouTube availability, Trainability API status or Creative Commons labels are insufficient to assume unrestricted access/training. YouTube API policies prohibit unauthorized audiovisual downloading and scraping.

Keep the nine public reaction URLs in the research-reference-only catalog. Do not fetch videos, transcripts, thumbnails or commentary for training until rights are explicitly verified. Any new licensed corpus is separate. Avoid public exposure of raw private video paths, detailed outing/geolocation traces and identifiable commentary.

## Evaluation and scientific gates

Baseline ablations:
- existing Klip heuristic only;
- action-video/embedding model only;
- action plus filming/observability attributes;
- action plus authorized reactions;
- action, filming, sports expertise and reaction jointly;
- mismatched reaction to unrelated action as an adversarial control.

Primary metrics: held-out within-goal human pairwise agreement; temporal event and peak localization; correct/unknown outcome calibration; source-time discovery recall on random audit; top-K human accepted highlights with diversity constraints; human review minutes per accepted highlight.

Secondary metrics: human-labeled visual readability, camera/aesthetic rank correlation, reaction claim grounding and alignment error, observer disagreement calibration, near-duplicate avoidance. Compare held-out source clusters with confidence intervals. Never report frame-level counts as independent subjects.

Stop a reaction-aware training path if it only produces more fluent story language without better held-out event identification, evidence grounding or human highlight selection. Evaluate reaction timing shifted +/- several seconds and commentary swapped across sports to catch spurious acoustic correlation.

## Practical staged pilot

Phase 0: catalog/metadata audit without touching blocked SD-card source paths. Retain previous embedding caches and all verified previews; repair source metadata first.

Phase 1: 100-150 diverse candidate hypotheses plus an independent random source-time audit. Human-label 60-100 original moments from >=20 source recordings across >=3 categories, with 30-50 challenging negatives or unknowns. Annotation overlap and reviewer calibration required. Pilot numbers are target budgets, not statistically established generalization adequacy.

Phase 2: 10-20 authorized action intervals with TWO independent observer commentaries each, one sport expert and one casual/editorial observer where possible. Freeze action-only ratings first. Align source action and reaction axes. No need to train a foundation video model yet.

Phase 3: frozen source/outing-disjoint holdout including unselected source time, 100+ within-sport blind pair preferences and multiple quality contexts. Evaluate baselines before fitting any multi-aspect preference heads.

Phase 4: only after clean licences, labels and held-out baselines: a frozen video encoder with compact per-aspect calibration/rankers. Consider larger end-to-end model training only if data and ablations justify it.

## Research reference implementations (license status must be checked separately)

Ego-Exo4D expert commentary: https://docs.ego-exo4d-data.org/annotations/expert_commentary/
Ego4D moment retrieval: https://ego4d-data.org/docs/benchmarks/episodic-memory/
FineDiving phase-specific sport quality: https://openaccess.thecvf.com/content/CVPR2022/html/Xu_FineDiving_A_Fine-Grained_Dataset_for_Procedure-Aware_Action_Quality_Assessment_CVPR_2022_paper.html
DOVER technical and aesthetic separability: https://vqassessment.github.io/DOVER/
VADB professional aesthetic ratings: https://proceedings.neurips.cc/paper_files/paper/2025/hash/81846dc80cf522fd8205d5a9a0fe1bf8-Abstract-Datasets_and_Benchmarks_Track.html
QVHighlights query-specific salience: https://proceedings.neurips.cc/paper_files/paper/2021/file/62e0973455fd26eb03e91d5741a4a3bb-Paper.pdf
SVHighlights long-form source-to-edit alignment: https://leedongkyu2019.github.io/SVHighlights/
YouTube developer policy: https://developers.google.com/youtube/terms/developer-policies

# Adventure-Klip Personal Episodic Action Intelligence, v1

Status: research and implementation **plan**, not a claim that the SSD originals,
all archive videos, neural models or personal rankers have been processed.
Stacked after G1 SSD-native source recovery (draft PR #84). Date: 2026-11-06.

## Mission

Build a local-first, source-backed **personal video understanding system**:
(1) locate and describe events over time; (2) recognize what differs from
previous episodes; (3) learn what this user values in a particular context;
(4) surface relevant, high-quality clips from new recordings with explicit
uncertainty. Preserve complete original recordings on the owner's SSD.

A highlight is NOT the same as a high-motion interval. A reliable personal
model must recognize quiet wildlife, anticipation, technical action, reactions,
environmental changes, memories, and disappointing or ambiguous outcomes.

Current known archive evidence:
- 232 SHA-ID catalog identities, 234 SSD video paths discovered.
- All 232 catalogued paths exist under the verified SSD root with matching
  filenames/sizes. Two additional SSD paths require SHA indexing; the totals
  do not establish 234 unique identities until SHA verified.
- Historic catalog duration ~34,879 s (~9.69 h) before indexing the two extras.
- SSD volume identity enrolled; no current device-health proof.
- Two-file SHA+ffprobe+FFmpeg pilot has NOT yet been reported as completed.
- Current coarse pass is 1–2 fps at 64x36 grayscale **motion proposals**,
  eight uniform-control windows plus at most eight motion hypotheses per video.
  It does NOT perform temporal action recognition or train a model.
- Cached proposal/review pipeline already has per-source SHA receipts,
  append-only/frozen audit patterns, blind packet creation, local previews,
  human feedback planning, and a read-only campaign status endpoint.

## Scientific questions

**RQ1: Temporal events.** Can temporal representation and before/during/after
context detect and localize meaningful occurrences better than peak motion,
uniform timelines, or image-only SigLIP2 novelty?

**RQ2: Personalized utility.** Does user preference feedback improve held-out
outing clip rankings relative to generic semantic similarity + technical
quality, while preserving quiet-but-meaningful events?

**RQ3: Generalization.** Can retrieval from prior episodes, and a lightweight
personal preference head, improve human assessment of *new* environments,
actions and filming conditions without overconfident unsupported claims?

**RQ4: Annotation efficiency.** How much label quality and held-out ranking
benefit comes from representative, disagreement-driven active sampling
compared with random control clips per minute of user review?

These are hypotheses, not established results. No model is assumed superior
until compared under a frozen benchmark with paired evaluation.

## Architecture and data boundaries

```text
Read-only external SSD originals
  |  verified SHA, ffprobe, media timestamps, safety/identity guard
  v
G1 Complete catalog coverage & coarse motion proposals
  |  immutable per-source scan receipts + unknown/error statuses
  v
G2 Hierarchical temporal observations
  |  boundary candidates + semantic/motion/audio/camera features
  |  source-time provenance and uncertainty at every step
  v
Episodic memory / semantic index / action graph
  |  outing -> source -> scene -> event -> phase -> clip
  +----------+------------------------------+
             |                              |
        G3 human feedback            G4 retrieval
     event vs personal value     similar past episodes
             |                              |
             +-----------+------------------+
                         v
              G4 personalized ranker
                         |
              G5 held-out evaluation
                         |
          user-approved clips / new video
```

Keep feature encoders frozen initially; cache content-hash/version-tagged
representations. Use a separately replaceable user preference model. Do NOT
train a large foundation model from scratch from ~10 hours of footage.

## Data contracts: implement before model training

All derived artifacts stay in the Mac workspace (not beside SSD originals).
Use Parquet or SQLite with versioned schemas, content SHA provenance, source
start/end PTS where possible, model/weights revision, transform settings, and
immutable run manifest. Existing records remain valid unless source identity
or processing settings change.

**Asset:** `asset_sha256`, canonical path, mount UUID or durable root mapping,
source bytes, capture time (with timezone certainty), ffprobe metadata,
duration, camera/stream IDs, source identity verification status.

**Outing:** `outing_id`, source IDs, day/session segmentation and confidence,
weather/location only if authorized, modality/camera changes, unknown links.
Current placeholder `unresolved-outing-all` MUST NOT be used as a real
session label or train/test split.

**Scene:** `source_sha256,start_ms,end_ms`, approximate real PTS, view type,
environment, scene-change cause (camera vs actor vs environment vs cut),
embedding revisions, QA receipts.

**Event:** `event_id`, interval, event class (possibly unknown), actor,
action phase labels (setup, initiation, action, outcome, recovery), visible
outcome/unknown, source evidence spans, camera-motion ambiguity, observed
objects, contradictory evidence and probability/calibration status.

**Preference (independent from event truth):** `user,goal,event_id/window,
keep/skip/unsure, ordinal 1–10 optional, pairwise A/B, reason tags,
uncertainty, user-adjusted in/out timestamps, reviewed_at, annotation version`.
Goals must be separate: memory, sports skill, wildlife, aesthetics,
storytelling/filming, educational. A rejected clip is NOT a rejected source
and NOT an absent event.

**Feature records:** semantic image embeddings, temporal motion embeddings,
optical flow/camera stabilization diagnostics, audio event features
(non-biometric, optional), composition/sharpness/exposure estimates,
appearance tracks, novelty relative to nearest neighbors, quality model
and uncertainty. Never infer speed, injuries, precise wildlife species,
a successful trick, internal mental state or skill from camera shake alone.

## Processing plan

### G0: Qualify 2 videos

Require two completed SHA-backed source reports, zero failed/blocked, real
ffprobe metadata, decoded timeline coverage, unchanged source before/after,
reproducible resume, no new originals under `WS/originals`, and a check that
source bytes and scan wall times are measured. If SSD stalls or macOS reports
I/O errors, STOP; the mount UUID is not a disk-health test. Source byte reads
are NOT currently bounded by supervised timeouts, so never hot-unplug during
a read.

### G1: Complete source pass

Do not change the frozen catalog/audit during scans. Once the two-file pilot
passes, use the SAME `CAMPAIGN_DIR` and exact root+FPS+SSD identity
settings. Increase to 8 assets per pass, first two passes (=16 new), inspect
health and per-file throughput. Then expand to e.g. 8 x 40 passes, keeping
workers=1. The launcher verifies identity before each batch. Interrupted
work resumes with per-source receipts; failed originals are NOT auto retried.

```bash
# From the same shell with real WS, CAMPAIGN_DIR, HEALTHY_SOURCE_ROOT and
# SSD_IDENTITY_FILE set by the actual two-video pilot:
test -n "${CAMPAIGN_DIR:-}" && test -f "$CAMPAIGN_DIR/audit/campaign-audit.json"
PHASE=scan CAMPAIGN_DIR="$CAMPAIGN_DIR" \
  HEALTHY_SOURCE_ROOT="$HEALTHY_SOURCE_ROOT" \
  SSD_IDENTITY_FILE="$SSD_IDENTITY_FILE" \
  ALLOW_MOUNTED_SSD=YES ENABLE_ORIGINAL_READS=YES \
  SCAN_ASSETS_PER_PASS=8 SCAN_PASSES=2 SCAN_FPS=1 \
  caffeinate -i bash scripts/klip_full_archive_highlights.sh

# If success, repeat with SCAN_PASSES=40 after checking progress.
```

Acceptance: completed SHA-receipt count equals frozen audit source count,
report integrity failures=0, blocked=0, failed=0, 100% catalog duration
coverage. If any cannot be completed, report exact partial denominators.
Do not accept a `notYetAttemptedEligible=0` status as success when failures
were skipped. Preserve failures as unknown; user must decide if/when retry
is safe. A new index/catalog update means a new audit campaign.

Cost gating: catalog duration ~9.7h means about 35,000 1-fps frame samples,
but source hashing plus FFmpeg decode require reading entire videos and are
NOT equal to 9.7 wall hours. Report per-video:
`source_sha_wall_s,MiB/s,decode_wall_s,video_s/decode_wall_s,
bytes_opened,per-source_status`; estimate subsequent batch time
from measured per-source pilots, not a promised ETA. Do not parallelize
SSD full-source decoders until measured safe throughput improves.

### G2: Build richer temporal observations (new implementation)

The v1 low-resolution grayscale motion series is not enough to recognize a
landing, ray encounter or turn. Implement a separately versioned
`temporal-proposals-v2` pipeline that:
1. preserves FFprobe/FFmpeg presentation timestamps where available;
2. segments long videos into overlapping 30–60s context chunks;
3. measures cuts, blur, exposure, camera rotation/ego motion separately;
4. extracts semantic stills (existing SigLIP2 cache reuse);
5. extracts short ordered-frames video representations on only selected
   regions, comparing frozen video backbone against frame-only baseline;
6. uses optional audio energy/environment change as *supporting* evidence;
7. retains 15–25% spatially/temporally stratified quiet uniform controls;
8. keeps multiple event hypotheses and explicit unknown outcomes.

Track scene/state transitions, not just high scores:
`terrain -> approach -> movement -> landing/fall/unknown -> recovery`,
or `seabed -> first sighting -> approach -> near pass -> departure`.
Event timestamps must be actual source-relative clock positions. Sliding
window overlap must be deduplicated, while preserving original fine-grained
boundaries and context.

Model candidates to benchmark rather than blindly install:
- existing SigLIP2 image-text retrieval and visual similarity (cheap baseline);
- V-JEPA 2 style temporal representation (motion-aware backbone; resource gated);
- a compatible bounded video VLM for open-vocabulary descriptions
  (expensive; only on diverse / confusing windows);
- optional TAL head only after enough real event-boundary annotation.
Validate licenses, checkpoint availability, local MPS/CUDA execution and
peak RAM/VRAM before adding a dependency.

### G3: Information-efficient human study

Initial annotation budget: 150–300 short windows total, **not** all
coarse-generated windows and not a full read of every video. Randomize and
balance by outing/activity/source, local semantic novelty, motion bins,
high/low lighting or visibility, and control-vs-peak role.

A starting design: 40–60 uniform/quiet controls, 40–60 motion-dominant,
40–60 semantic-novel or rare-context, 30–60 high-model-disagreement and
20–40 ordinary/negative examples; deduplicate overlaps. Reserve a separate
blind test set before training. These are planning allocations, not a claim
those examples or strata exist yet.

For each short playable candidate, collect:
- observed activity / visible action / phases / event boundary / outcome
  with `yes/no/unknown` and confidence;
- quality: clarity, composition, stability, framing, exposure;
- user intent: personal memory, athletic skill, nature/wildlife,
  novelty, filming aesthetics and story structure;
- optional 1–10 value rating and pairwise choices, with reason codes;
- exact time trim, “show more context”, and “not enough evidence”.

Use paired comparisons for nuanced value disagreements. Allow multiple
personal objectives, e.g. a calm ray encounter may be top-ranked for nature
even when an action-heavy skiing jump ranks higher under sports highlights.
Never convert unreviewed, unplayable or skipped clips into negative labels.

### G4: Baselines first, personalized model second

Build a local episodic index with separate feature spaces and source/scene
context; retrieve similar experiences for an incoming 5–30s interval. Start
with deterministic nearest neighbors and explicit citations to source
timecodes. Then compare:
- B0 random and uniform controls;
- B1 maximum motion peaks;
- B2 existing SigLIP2 semantic/novelty + recording quality;
- B3 temporally conditioned video features / event graph;
- B4 B3 + retrieved previous episode analogs;
- B5 B4 + small user preference model (logistic regression, gradient-boosting,
  or tiny MLP with user goal conditioning).

Personal ranker may estimate `P(user_keeps | clip, goal, context)` and
`P(A preferred to B | goal)`. It must NOT rewrite what action happened.
Use multiobjective scores and a transparent reason/explanation, not one
uninterpretable 1–10 “true interest” value. Separate preference observations
from objective event labels.

Train light heads or adapters only after enough curated labels. Do NOT
fine-tune a billion-parameter general video foundation model on 150
feedback clips and expect it to generalize. Avoid re-encoding all footage
after every feedback event; feature caches are immutable/version-tagged.

### G5: Tests and promotion rules

Split by **real outing/session** rather than clip or neighboring source
video. If session grouping remains unresolved, use conservative held-out
capture days / sources and report possible leakage; never claim a
session-independent benchmark from `unresolved-outing-all`.

Freeze baseline, train, validation and test manifests before inspecting
test outcomes. Hold out at least one distinct outing per activity where
possible; for small numbers use leave-one-out by outing. Reserve one
**chronologically future** set of new recordings for prospective
evaluation, not model selection.

Metrics:
- source and duration coverage, exact SHA-backed report integrity;
- event detection precision/recall and temporal IoU against labeled events;
- source-clock boundary error and missed brief events;
- retrieval recall@K of human-tagged events, nDCG@10, precision@10;
- preference agreement on A/B pairs, calibration and abstention rate;
- rare/quiet event recall, stratified by activity and lighting/visibility;
- review minutes per useful verified discovery and local inference cost;
- per-outing macro statistics with paired bootstrap confidence intervals.
Prefer a measured positive held-out improvement over a chosen existing
baseline. A reasonable *promotion goal* is a reproducible >=10% relative
nDCG@10 improvement without losing quiet-event recall; if test support is
insufficient, report uncertainty instead of passing a threshold.

Safety and integrity: unknown events stay unknown, disagreement is preserved,
and no fabricated action/outcome/wildlife species is silently accepted. Keep
a model/evaluation card recording failure modes, data provenance, privacy,
resource use and reproducibility. Never promote models using only
model-generated labels on unreviewed media.

## Personalized understanding examples

Skiing: distinguish run start, approach to feature, hop/takeoff, air,
landing vs uncertain outcome, recovery and continuous scenic runs. Camera
shake is neither proof of a jump nor proof of a failed landing.

Mountain biking: separate trail turn, berm, jump, pause and rough-surface
camera shake. Compare terrain/conditions only when visually defensible.

Snorkeling: distinguish featureless transit, quiet scenic habitat, animal
appearance, first close pass and disappearance. Species identification
remains unknown if visual evidence is insufficient. Motion-only sampling
must not miss low-motion wildlife or subtle behavior.

New or mixed situations: use a provisional narrative with explicit
confidence and retrieved similar episodes; prefer “unfamiliar, please
review” rather than force a prior sport label. New activity classes may be
proposed but remain human-unconfirmed until annotated.

## Branching / milestones / deliverables

- **P0 (now):** finish 2-file G0; save actual pass/fail, throughput and
  immutable campaign path; no source files touched by generated artifacts.
- **P0:** scan 16 additional sources, inspect rate/drive behavior; then
  complete G1 archive with zero unreported source exclusions.
- **P1:** define schema and outing clustering, implement `temporal-proposals-v2`
  and the hard baseline study before model selection.
- **P1:** create source/time balanced, blinded 150–300-window review campaign.
  Save user annotations in append-only versioned records.
- **P2:** implement semantic + temporal retrieval/episodic scene graph; compare
  B0–B5 under held-out outings and freezing.
- **P2:** adapt personalized ranking from feedback with calibrated
  uncertainty, user-goal controls, opt-in update and rollback.
- **P3:** evaluate unseen future SSD imports and publish a privacy-safe
  methodology, results, and failure-preserving examples without raw media.

Do not merge speculative research quality metrics with source-provenance
metrics. A 100% coarse scan proves source coverage, not 100% event recall.
A 100% human-ranked packet proves reviewed candidate coverage, not
knowledge of unreviewed parts of a video.

## Key prior work

- SigLIP 2 (Tschannen et al., 2025): image/text representations,
  https://arxiv.org/abs/2502.14786
- V-JEPA 2 (Meta, 2025): temporal/video physical understanding,
  https://ai.meta.com/research/publications/v-jepa-2-self-supervised-video-models-enable-understanding-prediction-and-planning/
- Ego-Exo4D V2: timestamped activity, expert commentary, multimodal signals,
  https://docs.ego-exo4d-data.org/
- Ego4D episodic memory tasks: natural language, visual and moments queries,
  https://ego4d-data.org/docs/benchmarks/episodic-memory/

Public benchmarks are reference designs, not evidence that Klip itself
achieves their reported results. Apply compatible licenses and avoid
uploading private footage to third-party inference providers without
explicit informed authorization.

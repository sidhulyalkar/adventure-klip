# Adventure-Klip: parallel workstreams during the SSD archive campaign

Status: concrete execution plan, not evidence that the 2-pass or 40-pass
source scans completed. This branch is intentionally **documentation-only**
relative to the running Mac scan. All off-source tasks should use synthetic
data, cached reports, or separately mounted/local preview copies already
verified; do not compete for the actively scanned SSD.

## Current source and campaign truths (Nov 6, 2026)

- The exact mounted root is `/Volumes/Sid_DJI_SSD/DCIM`; volume UUID
  `77835823-7d54-386b-b6c2-2e65ab80c5b8` was checked.
- SQLite previously had 232 catalogued SHA identities; all 232 existing
  catalog paths matched enumerated SSD file paths and file sizes.
- Two additional SSD files were not catalogued at those paths:
  `DJI_20241227150526_0107_D.MP4` and
  `DJI_20260411004259_0274_D.MP4`. Indexing status and resulting unique
  catalog cardinality must be confirmed from the actual local output.
- No source content verification follows merely from path or size matches.
- User reports an active `run_ssd_scan 2` execution and plans
  `run_ssd_scan 40` after it exits. Given a batch size of 8, 2 passes
  may attempt 16 recordings. No assumption about completions until receipts
  are inspected.
- The existing coarse 1 fps grayscale sampler produces action/scene motion
  *hypotheses*, not semantic event labels.

## Operational rule: do not disturb the running source job

While a `PHASE=scan` process is active:

- **NO** concurrent `PHASE=scan`, `PHASE=packet --prepare-missing`,
  `klip index`, `klip ingest`, full-video FFmpeg, bulk SHA, or a model
  that reads the SSD.
- **NO** index/catalog mutation, campaign-audit replacement, SSD eject,
  new campaign directory, or changes to the campaign's source path/UUID/FPS.
- **NO** `git pull`, checkout or package upgrade in the live process
  checkout; the launcher invokes `klip` repeatedly and should execute a
  stable code version until the current multi-pass process exits.
- **YES** to brainstorming, writing docs and schemas, tiny synthetic unit
  tests on a separate checkout, review UX, split design, model comparisons,
  source metadata from already frozen workspace-only reports, and offline
  analysis of already verified cached features (only if it doesn't compete
  materially for CPU/RAM).
- If the SSD stalls, stop initiating new work; allow the current operation
  to fail or diagnose safely, do not hot-unplug it.

## Workstream B0: the personal goal definition (now)

Write 10–20 *hypothetical or remembered* moments, not inferred facts:
which sport, context, what happened, why it mattered, and the ideal
video start/end. Include ordinary examples and examples that are valuable
despite poor cinematography.

Personal goals may change with usage; define at least:
1. `athletic_skill`: approach, technique, landed outcome, difficult terrain.
2. `wildlife_nature`: animal interaction, habitats, subtle motion, rare views.
3. `personal_memory`: meaningful outing, friends, first attempts, full story.
4. `cinematic_story`: composition, pacing, sequence coherence, atmosphere.

Keep a distinction between `event_present` and `user_keep` and between
`user_keep` and `film_quality`. Never train on missing/unreviewed examples
as negative clips. Unknown context and unseen outcome are first-class labels.

Deliverable: initial event/goal/reason taxonomy + reviewer UX specification.

## Workstream B1: immutable activity/outing graph (now, synthetic tests)

Document source clock, true presentation timestamps when available, SHA,
source lineage, candidate interval and human trim. Discover outing groupings
using user-confirmed date/session information, not solely by chronological
filename. `unresolved-outing-all` is a placeholder, **not a valid holdout
group**. De-duplicate overlapping adjacent proposals without erasing before
and after context.

Deliverable: source -> outing -> scene -> event -> phases graph, plus
sample JSON contracts for the UI, retrieval index and tests.

## Workstream B2: model selection study (now, no model downloads)

Specify frozen baselines and compute questions before model download:
B0 uniform random, B1 motion, B2 SigLIP2 semantic retrieval,
B3 temporal video representation (compare V-JEPA 2 and current 2.1
checkpoints under compatible hardware), B4 episodic retrieval,
B5 B4 + preference head. Measure peak RAM, input frame count, decoded
pixels, embedding size, wall time, cache reuse and new unseen-event accuracy
on identical test windows. A temporal backbone does not automatically
improve highlight ranking; compare paired outcomes.

Source/time split: avoid adjacent clips in both train and test. Prefer
held-out outings. When date/session labels are uncertain, say so rather than
claim outing-level generalization. Freeze tests before training.

Deliverable: short reproducible design, limited-model benchmark script
on fixture frames or previously cached local previews **after** SSD job
stops. No large-model training is required yet.

## Workstream C0: build the human annotation instrument (now)

Use the protocol in `docs/PERSONAL_CLIP_REVIEW_PROTOCOL_V1.md`.
Implement separate observed events/phase visibility and subjective
preferences; blind the reviewer to model score and sampling category.
Support `cannot_tell`, trim-in/out, extend-context, add text reasoning,
source SHA/timecode and export of immutable review revisions.

Start with a 24–40 clip usability test from verified previews once the
source scan is idle. Expand to a balanced 150–300-window benchmark drawn
across activities, outings, quiet controls, high motion, semantic novelty,
uncertain scenes and random samples. A high motion peak is not a positive
label and a non-highlight clip is not an absence-of-event label.

Deliverable: stable schema, human review UI and offline export/import.

## After run_ssd_scan 2 (up to 16 video attempts)

Check `$CAMPAIGN_DIR/progress.json` **after the process exits**.
It is written by the launcher near the end, so an earlier version may be
stale during an active scan. If necessary, inspect current SHA-receipt status
from the local campaign without modifying it:

```bash
test -n "${CAMPAIGN_DIR:-}"
klip --workspace "$WS" highlight-campaign-status \
  --audit "$CAMPAIGN_DIR/audit/campaign-audit.json" \
  --scan-dir "$CAMPAIGN_DIR/coarse-scan" \
  > /tmp/klip-campaign-status.json

python - <<'PY'
import json
s=json.load(open("/tmp/klip-campaign-status.json"))["scan"]
for k in ("completed","failed","blocked","interruptedOrUnattempted",
          "reportIntegrityFailures","completedDurationSeconds",
          "candidateWindowsProposed",
          "meanVerifiedDecodeSecondsPerWallSecond",
          "scanDurationCoveragePercentOfEntireCatalog"):
    print(k, s.get(k))
PY
```

This is workspace report I/O, not source-video reading. Ensure the active
scan's `scan-state.json` exists before using `--scan-dir`.

**Gate to full 40 passes:** no new SSD errors, success reports properly
SHA backed, status denominator matches the frozen audit, progress timing
reasonable based on observed throughput. Incomplete recordings remain
unknown. If failures occurred, inspect per-source error reasons and SSD
health rather than silently treating them as skipped negatives.

**After the running process has exited**, and only with a clean working
tree, `git pull --ff-only origin fix/full-archive-source-root-preflight-v1`
before starting the next long run. That update includes the safer
per-pass log numbering so `scan-pass-001.json` and previous logs are not
overwritten by normal resume.

```bash
# Same, unchanged frozen CAMPAIGN_DIR, SHA catalog, mount UUID and SCAN_FPS=1.
run_ssd_scan 40
```

Do not launch 40 in parallel with the 2-pass job. A 40-pass maximum is a
budget, not a guarantee of 100% coverage or success. Log/source receipts
are the evidence.

## After run_ssd_scan 40

Compute campaign progress and insist on:
- completed equals frozen catalog count, or explicitly record blockers;
- failed=0, blocked=0, reportIntegrityFailures=0 for 100% success;
- duration coverage=100% of catalogued duration (independent denominator);
- notYetAttempted=0; sources with no duration/identity are not ignored;
- no source-level content or semantic claims beyond what was decoded.

Next create one **bounded** frozen review packet (start 80–120 mixed
candidate windows, per source 4), using verified cached previews when
available. `PREPARE_MISSING_PREVIEWS=YES` reads original video again and
may be expensive. Run this after the SSD scan has ended and remain
mindful of workspace free space:

```bash
PHASE=packet CAMPAIGN_DIR="$CAMPAIGN_DIR" \
  HEALTHY_SOURCE_ROOT="$HEALTHY_SOURCE_ROOT" \
  SSD_IDENTITY_FILE="$SSD_IDENTITY_FILE" \
  ENABLE_ORIGINAL_READS=YES ALLOW_MOUNTED_SSD=YES \
  PREPARE_MISSING_PREVIEWS=YES PACKET_LIMIT=120 \
  caffeinate -i bash scripts/klip_full_archive_highlights.sh
```

This creates the campaign's `review-packet` at most once. To design
another blinded sample, use a separate immutable packet directory/manifest,
not manual mutation of the existing one. Inspect preview readiness and
sourceScanFingerprint match before rating.

## Technical follow-up implementation order

1. G1: source and duration coverage UI, throughput checks, source error
   classification, per-file stall supervisory boundary.
2. G2: source-PTS-aware event/scene schema + outing identity graph,
   control windows and outcome abstention.
3. G2: 2–3 frame/temporal encoders evaluated on matched windows, not on
   qualitative demos alone.
4. G3: blind review app, provenance and export, editable boundaries,
   event/quality/preference separated.
5. G4: episodic retrieval QA, small task-aware preference ranker,
   ablation metrics and held-out outing evaluation.
6. G5: new SSD imports become a frozen forward test. Humans correct
   source-backed predictions before any incremental model update.

## Metrics for an MVP release

- `source_coverage`, `duration_coverage`, `report_integrity_failures`.
- `event_temporal_iou`, `rare_quiet_recall`, `false_semantic_claim_rate`.
- `retrieval_recall_at_5`, `ndcg_at_10` per *goal* on held-out outings.
- `pairwise_preference_agreement`, calibrated uncertainty/abstentions.
- `review_minutes_per_useful_discovery`, model cache reuse, wall time,
  peak RAM and bytes read from external SSD.
- All evaluations retain clip-level mistakes and confidence intervals,
  not only mean score or curated examples.

Model choice note: Meta's V-JEPA repository announced V-JEPA 2.1 on
2026-03-16, with dense temporally consistent features. Treat V-JEPA 2.1
as an evaluation candidate, not an upgrade mandate. Sources:
https://github.com/facebookresearch/vjepa2 and
https://ego4d-data.org/docs/benchmarks/episodic-memory/.

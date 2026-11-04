# Adventure Klip editorial evidence v1: from scene recognition to useful archive moments

## Observed problem and current boundary

The four-item adaptive-v2 dry-run was run twice. Both times it selected the same
four IDs, with eight human reviews already on file, because a dry-run does not
invoke Ollama, create cache entries or change selection eligibility.
`excludedFromThisRun: 1` means three of the four excluded IDs were already
outside the unreviewed pool. Repeating the same dry-run cannot improve rank.

The first 16-interval review benchmark is **not** full-archive evaluation. The
new evidence audit works only on intervals in this frozen assignment file and
reads only existing checksum-verified audio, motion and preview cache evidence.
It cannot infer the actual best moment in an unsampled portion of a recording.

## Why "more action" is not just volume or optical flow

At minimum store these independent measurements for each candidate:

| Axis | Source | Evidence boundary |
| --- | --- | --- |
| Activity and scene type | sparse SigLIP context, cached adaptive-v2 | zero-shot hypothesis, not verified |
| Motion and possible fast traverse | low-res motion / optical flow | camera shake, turns, obstacles and cuts confound travel |
| Wind-like microphone energy | 1-second source audio RMS + low-band fraction | microphone gain, headwind, clothing, clipping confound speed |
| Actual speed | opt-in synchronized GPS/telemetry | only if available, time-aligned and calibrated |
| Framing, roll and focus | human tags now, gyro/geometry in a later pipeline | no fabricated angle or subject center from three stills |
| Subject and event | tracked ROI plus human-confirmed chronology | static detection cannot certify a landing or animal behavior |
| Originality | source-compatible SigLIP2 embeddings, archive/session diversity | similarity is not event or animal identity |
| Editorial utility | human preferences, complete temporal arc, cinematography | separately learned after held-out evaluations |

**Audio loudness is not an absolute velocity estimator.** Cache-only audit
computes source-relative percentiles for RMS and image motion. Only a *ski
descent context* with elevated motion, wind-like spectrum and no obvious
clipping can propose expanding a frozen 14s interval to a **26s playback
review**. These are timing proposals, never automatic video edits.
The policy intentionally abstains when evidence is missing.

## Human recording-quality review

Studio's optional recording-defect section records ten bounded human tags,
including `crooked_mount`, `off_center_subject`, `soft_focus`,
`obscured_lens`, `unstable_framing`, `unusable_audio`, `repeated_take`,
and `needs_rotation`. Tags are stored in append-only
`benchmark_review_revisions`. They are not outputs from a model and do not
change the original clip.

The `deletionCandidate` boolean requires a **human Skip** and at least one
recording-defect tag. It flags only the reviewed interval for a future
**full-source deletion review**. The audit returns an asset-ID queue whose
`mayDeleteOriginal` is *always false*, because sampled bad intervals cannot
establish that a complete original recording is disposable. Even complete
sampled failure does not authorize source deletion. No delete command exists
in this change. Keep backups and source SHA identity before any independent,
manual deletion decision in a later workflow.

## Run the new audit after CI qualification

Finish any active Studio session and stop it with Ctrl+C before changing
branches. From a clean checkout:

```bash
git status --short
git fetch origin
git switch --track origin/feat/archive-editorial-quality-and-action-v1
python -m pip install -e '.[dev]'
python -m pytest -q tests/test_archive_editorial_evidence.py \
  tests/test_benchmark_feedback.py tests/test_triage_failure_recovery.py

export WS="$HOME/AdventureAI-clean.QJnopt"
export REVIEWS="$WS/reports/archive-review-first16-v1/assignments.json"

# Read-only cache report, no inference, no deletion, no source-video reads
klip --workspace "$WS" archive-editorial-audit \
  --assignments "$REVIEWS" --model gemma3:4b

# New report directory (choose a fresh path each invocation)
klip --workspace "$WS" archive-editorial-audit \
  --assignments "$REVIEWS" --model gemma3:4b \
  --output-dir "$WS/reports/editorial-evidence-pilot-v1"
open "$WS/reports/editorial-evidence-pilot-v1/index.html"
```

Only populated relative motion / audio percentiles from existing artifacts
count as measurements. For a result with `adaptiveSceneHypothesis: null`,
the scene is not verified or the appropriate cached adaptive-v2 observation
is absent. For `suggestedLongerWindow: null`, do not conclude that the source
contains no action. Missingness is information about **coverage**.

Studio still starts as before:

```bash
klip --workspace "$WS" studio --benchmark "$REVIEWS" \
  --local-judge-model gemma3:4b --judge-policy adaptive-v2
```

When reviewing kayaking/snorkeling defects, tag crooked mount separately from
off-center subject. Rotation might rescue the first. A subject entirely
outside the recorded pixels cannot be restored by image rotation. Do not
penalize intentionally angled skiing/biking turns unless the fixed camera
mount was misaligned for a sustained period.

## What the next iteration must do before claiming the best possible clips

1. **Evidence coverage**: stratified time windows across every asset, grouped
   by outing/session; enough independent short and sustained scenes, not
   single frozen 14s snippets. Measure uncovered time and failed decode.
2. **Scene graph**: track timestamped scene type, subjects, candidate event
   trajectories, camera quality, provenance, uncertainty, and source SHA in
   separate fields. Validate temporal change points and maintain continuous
   chronology (entry -> peak -> exit).
3. **Quality QA**: use orientation/gyro metadata where trustworthy; sky/water
   horizon fit only when a measurable horizon actually exists; assess whether
   crop/rotation can rescue footage. Calibrate visual quality against explicit
   human recording-defect tags rather than guessing from blue or shaky pixels.
4. **Motion/action**: seek sustained coherent downhill ski movement, carving
   and terrain features, not the loudest single second; use 15-30s defaults,
   with pre-roll and post-roll. Test speed estimates only against held-out,
   synchronized GPS or controlled trials where ground truth exists.
5. **Diversity**: cluster embeddings only within compatible model revisions.
   Suppress near-identical clips in the final shortlist, not in discoverability
   or deletion evidence. Preserve rare encounters and unseen controls.
6. **Evaluation**: reviewer-blinded session-disjoint, stratified controls.
   Track Human Keep@K, missed high-value Keeps, temporal boundary quality,
   defect precision, duplication rate, and minutes per useful 20s clip.
   Tune sport-specific policies only on training outings, then lock and test
   on independent sessions.

No unsupported species rarity, skiing mph, athletic outcomes, or whole-file
deletion claims should be generated from a sparse still-frame pilot.

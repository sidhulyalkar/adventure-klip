# Adventure Klip: Event-centered context, without losing partial evidence

## Why this change

The real, manually verified recording
`DJI_20241227104236_0077_D.MP4` (full SHA
`2ddb3eb9b9918bc9931c8156dc5318656e09df70403997cdd7e484a6a0d7b837`)
produced **19 of 20** requested ecology-owlv2 frame results and failed on the
last FFmpeg frame. Model inference used ~138 seconds; decoding used ~59.
Klip writes each successful frame into `semantic_frames` with a row SHA-256,
but previously did not expose those frames because the overall job was
`failed`. This was a large waste of useful review evidence.

### Command 1: recover the saved partial data, no new video decoding

After the current job exits and when it is safe to change branches:

```bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/event-evidence-recovery-v1
source .venv/bin/activate
python -m pip install -e '.[dev,semantic,images]'

export WS="$HOME/AdventureAI-clean.QJnopt"
export ASSET="2ddb3eb9b9918bc9931c8156dc5318656e09df70403997cdd7e484a6a0d7b837"

klip --workspace "$WS" semantic-recover \
  --asset "$ASSET" \
  --plan "$WS/exports/sandbar-ray-pilot-v2.json" \
  --provider ecology-owlv2 \
  --max-missing 2 --min-fraction 0.80
```

The recovery operation:

- validates the existing plan hash, exact asset ID, indexed duration, unique
  failed config ID, stored checkpoint payloads and per-row checksums
- re-verifies the **original source file SHA-256**; never writes video bytes
- requires PTS-backed frames and >=80% sampled coverage with at most two gaps
- creates an auditable **partial** semantic document and preserves the missing
  requested timestamps
- runs **zero new decoder/model calls**; no automatic interpolation
- exposes partial results only to explicitly opt-in review readers; normal
  editorial similarity/ranking continues to demand a complete result

An invalid, changed or missing original video correctly stops recovery.
A different adaptive plan, changed preprocessing or corrupt checkpoint
also stops rather than attaching evidence to the wrong source.

If all 20 frames are already available, **do not recover**: complete the
original semantic job normally instead.

### Command 2: recover provisional marine encounter review windows

```bash
klip --workspace "$WS" ecology-encounters \
  --asset "$ASSET" \
  --output "$WS/reports/ray-encounters-partial-v1.json"
```

This cached-only report explicitly records
`audit.partialCoverageProviders`; any marine object labels remain
**unverified**. A missing 20th frame is **unknown**, not evidence that no
animal appeared there.

### Command 3: generate *before/during/after* embeddings and a private gallery

```bash
caffeinate -i klip --workspace "$WS" event-context \
  --asset "$ASSET" --max-events 4 \
  --output-dir "$WS/reports/ray-event-context-v1"

open "$WS/reports/ray-event-context-v1/review.html"
```

This command only runs if source-verified object detections exist. It:

- groups **overlapping review windows** from different cached detector
  proposals into a small set of candidate moments; no individual-animal
  identities or abundance are inferred
- selects approximate approach, encounter and aftermath timestamps, using
  detected timestamps, not guessed player motion or generated video
- decodes **up to 3** source frames per event, at most **6** events total
  (18 operations), using the original camera recording and measured source PTS
- confirms central event source PTS against the original detector when possible
- computes **separate normalized SigLIP2 embeddings** for each phase,
  plus a weighted context vector and cosine scene-change measurements
- writes all evidence, source model provenance, PNG-derived preview checksums
  and uncertain/missing phases into a private `manifest.json`
- creates a standalone local `review.html` contact sheet; never uploads
  video images or changes the asset's manually confirmed activity

A scene-change embedding can change because of camera orientation, turbidity,
lens reflections or lighting. It is not an athlete achievement score,
animal rarity measure or proof of event occurrence.

## What an event should become (future layers)

For each candidate moment, preserve a structured evidence record:

1. **Source**: immutable asset hash, exact video-relative start/end, sampled
   frame PTS and any unresolved gaps.
2. **Scene**: waterline, pale seabed, coral, ski lift, downhill or bike trail
   (provisional and temporally reviewed).
3. **Event**: organism-like object, berm, jump, carving turn or terrain
   feature; independent detection/track evidence with abstentions.
4. **Temporal representation**: before/during/after embeddings, visual
   differences, optional optical flow/motion cues, and a complete-window clip.
5. **Outcome**: what visibly happened, e.g. a ray passed through the
   camera field or a rider completed a line. Avoid interpreting a mere
   static terrain image as an executed maneuver.
6. **Review value**: observation opportunity, technique-feedback opportunity,
   audio/visual clarity, rarity *within the user's verified footage*,
   story completeness and duplicate similarity.
7. **Feedback**: human keep/skip, factual action corrections, note about
   subject framing and what should be improved on the next outing.
8. **Context**: session, neighboring clips, original metadata; no fabricated
   species/skill/physiological states.

The output representation should be a **temporal evidence graph**, with
references to verified source frames and decisions, rather than one
unsupported image caption.

### Learning from footage

*Snorkeling*: distinguish novelty of visually distinct organisms from
valid species identification; recognize framing, approach and withdrawal,
habitat context, water clarity and whether the camera captures the encounter
continuously. Critique **camera positioning and composition** from evidence,
not inferred intentions or ecology unsupported by the video.

*Skiing*: lift and flats are usually navigation context; downhill episodes
should include the entry, technical sequence and completion. Scene labels
are not proof of speed, carving technique, jump performance, or safety. To
evaluate ski mechanics, add verified motion/pose/turn sequences with
calibration and disclose POV limitations.

*Mountain biking*: separate climbs/transfers from downhill trails,
obstacles and continuous feature combinations. Retain line approach and
recovery. Never equate camera shake to jump execution or technical skill.
Annotate recoverable riding quality only when visual context supports it.

### Cost and quality gates

- Benchmark random clips and selected candidate windows to measure **missed
  important moments**, not just precision of recommended clips.
- Track source bytes read, full-source checksum calls, frames decoded,
  inference frames, wall time, GPU/MPS time, peak memory and coverage.
- Reuse complete cached semantic frames and verified partial data only where
  the reading workflow explicitly accepts gaps.
- The best sampling policy maximizes reviewed event recall, temporal
  completeness and personal keep rate at **fixed budgets**.
- Do not discard entire scenes automatically from single-frame sport labels.
- Fine-grained training requires manually verified clips from different
  sessions, including ambiguous examples and negative controls.

The present v1 event-context release creates inspectable embeddings and
review packets. It **does not yet produce an end-to-end calibrated
achievement/technique evaluator** or a fully edited public highlight reel.

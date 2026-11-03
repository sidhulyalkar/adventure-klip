# Segment Intelligence v1: multi-sport time-based scene discovery

This is an **opt-in one-recording pilot**, not a full archive rescan. It
does not modify indexed activity labels, source DJI footage, the first-pass
classification reports, or any earlier ecological or adaptive plan.

## Design

The user-verified `DJI_20241227104236_0077_D.MP4` is now registered as
snorkeling. The early marine OWLv2 run failed before inference because
`scipy` was absent. The semantic optional dependency now includes SciPy,
so use `python -m pip install -e '.[dev,semantic,images]'` and rerun
the original ecology adaptive plan. A missing detector result means **no
completed detector evidence**, not absence of rays or fish.

**Segment observations** have three separate clocks/concepts:

- `requestedMs`: video-relative sampling target used for all navigation.
- `decodedPtsMs`: actual source stream PTS evidence, **not** presumed zero
  normalized. Keep both to audit timing.
- `startMs/endMs`: bounded provisional navigation regions defined by
  neighboring requested sample midpoints and a 12-second evidence radius.
  A gap without nearby observations stays unclassified.

The model first samples approximately every 15 seconds with a cap of
`max_frames-refine_frames`. It then allocates a small number of additional
frames to uncertain interpretations or putative scene transitions. It never
interpolates a 2-second positive frame into ten minutes of confirmed snorkeling.
Only two or more consecutive matching observations yield a provisional
multi-frame state. Single observations remain visibly marked as navigation
hints, not verified behavior. Uncalibrated text-image cosine similarities
are **not probabilities**.

### Snorkeling states

- `underwater_sand`: bright white sandy bottom, sun ripples, shallow sandbar.
- `underwater_reef`: reef, rocks, coral, vegetation.
- `underwater_open`: open water with few visible landmarks.
- `surface_swim`: partial waterline, floating or swimming at the surface.
- `kayak_boat`: above-water kayak / paddleboard / boat, distinct from snorkeling.
- `shore_wading`: beach and near-shore wading.
- `underwater_habitat_unresolved`: evidence favors underwater filming, but
  sandy versus reef habitat remains ambiguous.
- `unknown`: abstain when relative visual evidence is insufficient.

**Wildlife is a separate overlay**, not a mutually exclusive scene state.
A sandy seabed can contain a fish-like or ray-like proposal. Verified cached
`ecology-owlv2`/`owlv2` frames add unverified group hints without
inflating counts when providers report the same group at the same timestamp.
No species identity, individual-animal count, or animal behavior is inferred.

### Skiing states

`ski_lift`, `ski_downhill`, `ski_technical_terrain_candidate`,
`ski_flat_traverse`, `ski_stopped`, `ski_active_unresolved`, `other`,
`unknown`.

Images of steep snow or moguls are **terrain proposals**, not proof of
jump execution, skier skill, speed, or an incident. Temporal motion
and reviewed video must qualify the maneuver before highlight ranking.

### Mountain biking states

`bike_technical_terrain_candidate`, `bike_riding`,
`bike_climbing_or_flat`, `bike_stopped`, `bike_walking`,
`bike_riding_unresolved`, `other`, `unknown`.

Same caution: handlebar motion alone does not establish a jump, rider
technique, climb gradient, speed, or risk.

## Run one verified snorkeling recording

After the SciPy fix, confirm ecology inference has completed using the
previously generated adaptive plan:

```bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/temporal-segment-intelligence-v1
source .venv/bin/activate
python -m pip install -e '.[dev,semantic,images]'

export WS="$HOME/AdventureAI-clean.QJnopt"
export ASSET=2ddb3eb9b9918bc9931c8156dc5318656e09df70403997cdd7e484a6a0d7b837

# Reuse the existing 20-frame plan, not a new one.
caffeinate -i klip --workspace "$WS" adaptive-scenes \
  --plan "$WS/exports/sandbar-ray-pilot-v2.json" \
  --backend ecology-owlv2 --decode-mode batched

# Optional PTS-verified source crops if detector boxes exist.
klip --workspace "$WS" ecology-crops --asset "$ASSET" \
  --max-events 4 --crops-per-event 2 --width 1536 \
  --output-dir "$WS/reports/sandbar-ray-crops-v2"
```

**Do not run `ecology-crops` again if its output directory already exists.**
A no-cache abstention creates no folder; valid crops create a folder.
If first-pass OWLv2 returns no matching boxes, crop output abstains.

Run a bounded segment pilot on the **same reviewed asset**:

```bash
caffeinate -i klip --workspace "$WS" segments \
  --asset "$ASSET" --activity snorkeling \
  --step-seconds 15 --max-frames 64 --refine-frames 16 \
  --output-dir "$WS/reports/snorkel-ray-segments-v1"

open "$WS/reports/snorkel-ray-segments-v1/review.html"
```

This may read the source and decode up to 64 frames; it uses local
SigLIP2 and no external image uploads. Output is a versioned
`report.json`, `config.json`, limited thumbnail gallery and offline
`review.html`. Reusing the exact command and output folder is idempotent,
provided the original report still matches its saved configuration.

## Try other activities without falsely editing the catalog

Use a full SHA-256 of an indexed video you visually recognize as skiing or
mountain biking:

```bash
SKI_ASSET="FULL_INDEXED_SHA256_OF_SKI_VIDEO"
BIKE_ASSET="FULL_INDEXED_SHA256_OF_BIKE_VIDEO"

klip --workspace "$WS" segments \
  --asset "$SKI_ASSET" --activity ski \
  --step-seconds 12 --max-frames 64 --refine-frames 16 \
  --output-dir "$WS/reports/ski-segments-v1"

klip --workspace "$WS" segments \
  --asset "$BIKE_ASSET" --activity bike \
  --step-seconds 12 --max-frames 64 --refine-frames 16 \
  --output-dir "$WS/reports/bike-segments-v1"
```

Since the rest of the current catalog remains `unknown`, these
sport parameters are **explicit review hypotheses**, not catalog changes.

## Validation gates before automation

1. Manually annotate contiguous scene states and important transitions in
   a few snorkeling, ski and bike source videos, including the bright sandbar
   and above-water kayak confounders.
2. Measure segment-boundary tolerance, temporal overlap IoU, unknown/abstention
   coverage, wildlife candidate recall, and false positive rate.
3. Compare full-scan sampling to boundary-refined selection at the **same**
   total decoded frame count and MPS seconds.
4. Detect continuity transitions in a dedicated stage rather than equating
   individual frames with sport actions. Feature tracking and optical flow
   must distinguish camera movement from participant movement.
5. Integrate human-reviewed segments into the editorial ranking and adaptive
   rescanning policy. Until evaluated, these segments are for navigating and
   creating targeted review proposals, not automatically discarding footage.

No image in this private workspace report is uploaded by Klip. Inspect for
sensitive filming locations before sharing any review artifacts.

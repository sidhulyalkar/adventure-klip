# Adaptive POV sampling: first experimental implementation

This is an **opt-in, local-only, read-only source** alternative to fixed-interval
semantic frame extraction. The standard `klip process` and `klip scenes` commands
are unchanged.

## Why this exists

The first 4K DJI pass took more than 45 hours, with multiple full-source SHA-256
verification passes, repeated decoding, and later source-resolution failures
when the external SSD was unavailable. Uniform sampling spends expensive model
inference on chairlifts and repetitive footage while missing short rare events.

This pilot **does not claim** speedups without measuring them. It reduces planned
semantic frame counts per asset and exposes stage timings, but each scheduled
frame still launches an FFmpeg seek. Durable batching/decoding optimizations
remain a separate phase. It does not reduce first-pass motion decoding or source
hashing, and it does not convert weak motion cues into verified sport skills.

## One-time install

From a clean working tree:

```bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/adaptive-pov-sampling-v1
source .venv/bin/activate
python -m pip install -e '.[dev,vision,images]'
```

Set the **exact workspace directory** used for the recent clean run. Do not use
a newly created empty workspace. The video SSD must be mounted and stable
before extraction. Use the same read-only indexed originals, never a copy over
the old source.

```bash
export WS="$HOME/AdventureAI-clean.REPLACE_WITH_ACTUAL_SUFFIX"
test -f "$WS/catalog.sqlite3"
```

## Phase A: Plan without video decoding

Start with a maximum of eight catalog recordings:

```bash
klip --workspace "$WS" adaptive-plan \
  --limit 8 \
  --coverage-seconds 45 \
  --interest-seconds 2 \
  --max-frames 128 \
  --output "$WS/exports/pov-pilot-01.json"
```

Use `--activity snorkeling`, `ski`, or `bike` to restrict to recordings
explicitly labeled with that activity. If your old catalog has `unknown`
activities, the planner must not guess them. Use the Studio's classification
workflow first if you want sport-specific evidence.

The plan is a deterministic, checksum-protected JSON artifact with:
- SHA-256 source identities, media durations and activity labels
- Timed inspection frames and reason codes
- Scheduled periodic coverage and prioritized extra investigation frames
- Actual effective coverage gaps when the frame cap forces coarser sampling
- Cached evidence counts and limitations

The plan is **not a verified event list**, and neither a blank gap nor a weak
object detection is proof that nothing interesting happened.

Use `--limit 0` only to explicitly create a full-library plan. Never run full
heavy semantic inference before comparing against a small benchmark.

## Phase B: Extract only scheduled semantic frames

For a cheap operational smoke test (color histogram, **not** object semantics):

```bash
caffeinate -i klip --workspace "$WS" adaptive-scenes \
  --plan "$WS/exports/pov-pilot-01.json" \
  --backend histogram
```

For **actual** image semantics, install the larger optional dependencies and
use the same plan with separate cache provenance. Perform the first pilot on
your M1 Mac with a small asset count, or run CUDA inference on the 3070 Ti once
you have your workspace accessible from the desktop:

```bash
python -m pip install -e '.[semantic]'
caffeinate -i klip --workspace "$WS" adaptive-scenes \
  --plan "$WS/exports/pov-pilot-01.json" \
  --backend siglip2
```

For unverified open-vocabulary object queries, use `--backend owlv2`.
Existing queries include fish, ray, shark, sea turtle, coral, tree, skis and
bicycles. These are **detection prompts**, not validated species or difficulty
levels. No automatic uploads or cloud model calls occur.

The semantic result contains measured cumulative seconds spent on:
`sourceVerification`, `frameDecoding`, `modelInference`, and
`finalVerification`. The same exact versioned plan/provider can reuse
completed artifacts and per-frame checkpoints. Changing plans intentionally
creates a different config, preserving older completed results.

## Phase C: Human validation and benchmarking

Review a representative set across all three sports. Include:
- top-ranked candidate windows **and** randomized quiet windows;
- skiing lifts/flats and visually interesting low-motion scenery;
- technical descents vs repeated easy runs;
- snorkeling rare fauna, multiple habitat types, and open water;
- MTB technical riding vs helmet shake / camera movements.

Annotate Keep / Skip / Uncertain, with explicit scene labels as needed. Compare
the same set of recordings under the existing uniform sampler and the
adaptive sampler at equal total frame/model budgets. Report:
- wall-clock and GPU/CPU time by stage;
- frames decoded per hour of footage and per reviewed highlight;
- recall on held-out human-reviewed clips, particularly brief wildlife events;
- top-K precision, repeated-shot rate and diversity across trips;
- absent or unverified evidence as **unknown**, not a negative.

No model behavior may be tuned using held-out test labels. The project already
has a `prepare-nim-benchmark` human+optional external judge workflow; using
external judges entails an **explicit** upload opt-in and is not required for
this adaptive pilot.

## Limitations / next work

1. The planner consumes existing motion, event, human review, SigLIP2 and OWLv2
   caches. With no caches, it still provides periodic and deterministic
   exploration; it does **not** infer animals or sport complexity.
2. Sampling is bounded and cannot guarantee capturing brief events. A larger
   frame cap is required for denser coverage of long snorkeling recordings.
3. Low-value ski/bike context (lift/flat/stopped) is an uncalibrated relative
   SigLIP2 cue used only to *deprioritize* extra frames. It never removes the
   scheduled coverage floor or a human-reviewed Keep.
4. Motion/novelty scores prioritize *where to investigate*, **not** whether
   a segment is genuinely highlight-worthy.
5. Real animal taxonomy, counts of distinct species, persistent identity,
   motion-invariant action recognition and GPU-batched inference are future
   benchmark-driven work, not features claimed by this version.
6. Audio and synchronized IMU/GPS fields are not yet integrated into the
   scheduler. They must be source-timestamped and checked for availability.
7. The first costly phase still rehashes sources before and after per-asset
   extraction and uses separate FFmpeg seeks. A separate persistent-decoder
   optimization should follow profiling, with PTS/timestamp parity tests.
8. A disconnected/remounted SSD may cause source resolution errors. Restore
   the correct mount before retrying; never disable source SHA-256 checks.

**Source immutability:** originals are only read. Plans and feature caches are
stored in the workspace, and no clips are exported or destroyed by these
commands.


## Experimental grouped FFmpeg decoder (stacked follow-up)

The original single-seek decoder is still the default. Test the optional
grouped decoder on the **same frozen plan**, in a new cache namespace:

```bash
caffeinate -i klip --workspace "$WS" adaptive-scenes \
  --plan "$WS/exports/pov-pilot-01.json" \
  --backend histogram \
  --decode-mode batched
```

Nearby requested samples (within 3.5 seconds, up to 12 samples or 30 seconds
per batch) share one FFmpeg seek and decoder process. Distant sparse coverage
probes still use the original one-frame seek, avoiding expensive decoding across
uninformative footage. This only optimizes scheduled semantic frame extraction,
not the initial motion scan, full-source hash checks, or model inference.

A batch is accepted only with the expected number of PNGs, sequential FFmpeg
showinfo records, a valid PTS timebase, and decoded source timestamps within
the requested seek window. Otherwise it **falls back to individually timestamped
seeks**. On some sources, including unusual start-time offsets or low-frame-rate
footage, fallback is expected. Mixed batch/fallback output is explicitly counted.

The result contains `decodeDiagnostics`:
- `batchAttempts` and `batchSucceeded`;
- `batchFallbacks`;
- `batchedFrames` and `singleSeekFrames`.

Both decoding modes use the same source identity/immutability checks and
per-frame checkpoint semantics. The decoder version is included in the semantic
configuration ID to prevent silently treating batched output as an old result.

**Performance qualification:** Compare two fresh, uncached runs on a small
representative pilot with the same plan/provider, measuring the decoding stage,
total execution time, fallback frequency and verified highlight recall. Keep
the source SSD mounted for both runs. Do not claim a speedup from frame counts
alone. For VFR, nonzero source start-times, GoPro/DJI footage, and macOS/Ubuntu,
check extracted PTS parity against single seeks before promoting this decoder
to the default. A successful synthetic FFmpeg test is not that qualification.

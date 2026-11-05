# Klip Social Lens: evidence-grounded scene stories v1

## Why add a second, independent judge?

Three-frame Keep/Skip judgments can miss the meaning of underwater animal
encounters, a continuous ski line or a scenic lead-in. Pure numerical motion
and embedding similarities have the opposite problem: they measure change
without explaining **what happens, why it might be interesting, and what they
did not actually observe**.

This opt-in local model writes a **scene-evidence narrative** from seven
ordered images taken from a SHA/receipt-verified private Studio H.264 review
preview, plus bounded nonsemantic cached data:

- 4 fps grayscale brightness, image changes and temporal run summaries
- Existing original-source audio and optical-flow cache statistics where available
- Verified homogeneous SigLIP2 image embedding similarity and sampling coverage
- Optional independently model-produced image-text scene labels and object
  proposals from validated caches, explicitly marked as unverified hypotheses

Raw vector embeddings are not language. The local visual model receives
compact, provenance-labelled numeric summaries and *candidate hypotheses*,
not thousands of meaningless embedding coordinates. It receives neither
Klip scores nor the user's votes, and has **no access to external social
engagement, earlier Keep/Skip data or neighboring clips**.

## Narrative output

Each record includes:

1. **Visible scene:** direct visual setting and subjects only
2. **Chronological observations:** 2–7 distinct grounded observations,
   with indices of the seven sampled frames and distinction between
   visible versus hypothesized meaning
3. **Story arc:** possible setup, development, key moment and outcome,
   with explicit Unknown where playback cannot prove it
4. **Why this could matter:** potential emotional, exploratory, aesthetic,
   behavioral or contextual interest (no fabricated personal significance)
5. **Alternative explanations:** camera pans, reframing, reflections,
   moving backgrounds, repetitive footage or compression artifacts
6. **Evidence gaps:** what's not actually shown in these stills
7. **Questions for human inspection:** which parts of continuous playback
   could confirm or contradict the narrative

For snorkeling: distinguish plausible ray/fish/turtle-like morphology
from species identification, foreground visibility from turbidity and
actual behavior from a series of stills. For skiing and MTB: distinguish
feature approach, terrain view and exit cues from verified action execution.
Do not infer ground speed from visual flow or noisy microphone audio.

The algorithm never reports calibrated popularity probabilities, absolute
speed, rare species or guaranteed completed tricks. The model's prose
remains provisional even when it is detailed.

## Why human-blind scoring comes first

Review pairs in `social-lens-pair-review` **before opening any narrative**.
That page shows only two local source-distinct verified video derivatives.
Choose A/B/Tie/Neither and optionally add reason tags. Download your JSON
ratings to preserve them. This is the human preference ground truth.

The narrative command creates **private JSON only**, not a presentation
page visible during human review. The subsequent `story-counter-audit`
validates your review-export fingerprint and reveals model narratives
only for pairs you already voted on. Unreviewed pairs are excluded.

This preserves the first human decision from narrative anchoring, although
a motivated local user can inspect any private JSON. The inference is
independent operationally; it is not a formally double-blind clinical trial.

## Commands on Mac

From a clean checkout of the new draft feature branch:

```bash
git fetch origin
git switch --track origin/feat/social-scene-evidence-narratives-v1
python -m pip install -e '.[dev]'

export WS="$HOME/AdventureAI-clean.QJnopt"
export FEATURES="$WS/reports/social-rubric-balanced40-v1/feature-template.json"
export TEMPORAL="$WS/reports/temporal-proxy40-v1/temporal-evidence.json"
export PAIRS="$WS/reports/social-pairs12-v1/pairs.json"
```

The following presupposes you have used the same balanced40 feature
file in a successful `social-lens-temporal --run [--prepare-previews]`
analysis, then created the frozen pairs through
`social-lens-pair-review`. If using different output names, point
variables at the existing files. The temporal report's SHA-256 must
match the exact feature template.

### Stage 1: preflight without model calls

```bash
klip --workspace "$WS" social-lens-stories \
  --features "$FEATURES" \
  --temporal "$TEMPORAL" \
  --pairs "$PAIRS" \
  --model gemma3:4b \
  --limit 6 \
  --output-dir "$WS/reports/scene-stories-plan6-v1"
```

Expected: `run: false`, `modelRequests: 0`, plus candidate coverage
and cache eligibility. No image decode, source read or Ollama call.

### Stage 2: independent local Ollama scene reconstruction

```bash
klip --workspace "$WS" social-lens-stories \
  --features "$FEATURES" \
  --temporal "$TEMPORAL" \
  --pairs "$PAIRS" \
  --model gemma3:4b \
  --limit 6 \
  --offset 0 \
  --run \
  --output-dir "$WS/reports/scene-stories6-v1"
```

This explicitly enables **local inference** against the literal
`127.0.0.1:11434` endpoint. It samples seven small JPEGs via FFmpeg
from existing verified **short H.264 previews only**, not original
recordings. Model requests and story caches are independent of the
earlier three-frame judge and never update the human review table.

The local model may be fallible, and processing is bounded to
`--limit 1..24` **per run**. To process the next batch, run the same
command with `--offset 6 --limit 6` and a new output directory (e.g.
`scene-stories-next6-v1`). Continue at offset 12, 18 and so on.
Each clip has a fingerprinted private cache, so re-running the same
batch does not repeatedly query Ollama when inputs are unchanged.
Each inference has a strict schema, temperature zero,
source/preview SHA and narrative prompt fingerprint. Cached results
are reused only if the verified preview and all prompt inputs match.
If the installed model tag points to new weights, cache identity might
not detect that: pin Ollama model digests for scientific comparisons.

After your blind A/B review, export the browser JSON, then reveal and
audit the narratives.

```bash
klip --workspace "$WS" social-lens-story-counter-audit \
  --pairs "$PAIRS" \
  --reviews "$HOME/Downloads/klip-paired-review-ACTUAL-SHA.json" \
  --stories "$WS/reports/scene-stories6-v1/stories.json" \
  --output-dir "$WS/reports/scene-counter-audit-v1"

open "$WS/reports/scene-counter-audit-v1/index.html"
```

For later runs, collect additional `stories.json` files by
advancing `--offset 6`, `--offset 12` and so on, then pass them
to the counter-audit with repeatable
`--additional-stories "$WS/reports/scene-stories-next6-v1/stories.json"`.
Only matching input feature/temporal evidence and Ollama model names
can be consolidated. Duplicate clips or mixed model versions are refused.

Replace the downloaded filename with the actual one. The post-vote
report places A and B narratives side by side with your recorded vote,
Klip's higher-ranked side, timeline evidence, alternative interpretations,
and human verification questions. Each side also offers a playable
previously checksum-verified short H.264 derivative from the local paired-review
folder, making it possible to check the story against the clip. If the
media derivative is missing or altered, playback is omitted instead of
trusting an arbitrary file. It never relabels the source clips
or reranks them automatically.

## Large-scale design, not a premature accuracy claim

The v1 model sees seven stills plus cache summaries. It does **not**
really understand intervening video frames or real original audio.
Large amounts of confident prose are not equivalent to large amounts
of independently supported evidence. The number of distinct,
time-grounded observations and alternative hypotheses are more
valuable than narrative length.

For a future high-standard architecture:

- **Per-recording event graph:** shot boundaries, camera geometry,
  moving subject tracks, foreground/background separation and
  continuous onset/peak/aftermath with verified time spans.
- **Sport-specific scene ontology:** underwater animal appearance,
  subject trajectories, marine environment, ski carving/terrain,
  MTB line choice and actual maneuver completion, with unknowns.
- **Provenance-first retrieval:** link every claim to specific sampled
  frames, flow windows, text-image prompt labels and measurement
  uncertainty. Distinguish multiple model outputs from independent sensors.
- **Counterfactual critique:** a separate model tries to falsify key
  claims by searching for contradictory frames; only independent
  human playback can accept ground truth.
- **Hierarchical archive understanding:** build episode summaries and
  deduplicated outing story maps *only after* sufficient asset coverage,
  never infer a rare moment's absence from sparse sampling.
- **Preference calibration:** compare the model's scene hypotheses to
  human voting disagreements, with held-out trips, camera mounting
  conditions, controls and an explicit false-missed-moment metric.

The present PR delivers a bounded, testable **counter-reading** step,
not an externally trained social-engagement model and not an
unconditional best-clips decision service.

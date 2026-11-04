# Temporal second look v1: ask for more evidence, not just a longer prompt

The current archive has source-indexed, immutable DJI footage and an
opt-in local Gemma 3 image judge. The first real judge trial returned
seven \`Unsure\` judgments and one frame-sampling/response failure.
Adaptive-v2 added sport-specific questions and an uncalibrated ordering
heuristic. **This PR addresses the next limitation: only three sampled
stills per proposal.**

## What it does

A local-only seven-frame second look:
- Requires a **completed source-verified H.264 preview** and a saved
  **adaptive-v2** judgment that was \`unsure\` or explicitly required
  additional temporal evidence.
- Samples seven ordered frames at 7%, 20%, 34%, 50%, 66%, 80%, 93% of
  the short preview. Uses a single \`ffprobe\` duration lookup and
  seven bounded JPEG extractions. **No DJI originals are opened,
  rehashed or decoded.**
- Sends only these seven small JPEGs to Ollama on
  \`127.0.0.1:11434\`, using the named installed model \`gemma3:4b\`.
  \`--run\` is required for *any* second-look model call.
- Returns evidence references to actual frame indexes, provisional
  Keep/Skip/Unsure, remaining uncertainty, event/learning/sharing
  potential, and whether a person needs the full continuous video.
- Preserves the original adaptive-v2 and legacy judgments and the two
  human reviews. It uses its **own fingerprinted cache** (policy,
  model tag, sport hint, prompt, schema, frame fractions, proxy bytes).
  Repeating the same call reuses this result.
- Protects possible short/unseen wildlife or maneuvers by upgrading
  an unsupported AI Skip to Unsure, never automatically deleting any
  archive candidate or claiming a scientifically verified event.
- Keeps candidate and systematic-control intervals eligible. Limits
  each invocation to a maximum of **eight** intervals.
- Supplies a read-only dry run showing how many intervals lack
  verified proxies or first-pass v2 evidence. Neither absence becomes
  a negative clip label.

**Important:** Seven isolated stills provide more sampled temporal
context but do NOT prove continuous action, a completed landing,
wildlife behavior, species identity, or the beginning/end of a scene.
Gemma 3 is still not watching real video or listening to audio.

## Commands after currently running jobs finish

The user has already switched to the recent triage branch. To try the
*new* feature after CI qualification, check uncommitted changes and
switch to the newer stacked PR branch:

~~~bash
cd "$HOME/Documents/Projects/adventure-klip"
git status --short
git fetch origin
git switch --track origin/feat/adaptive-temporal-second-look-v1
source .venv/bin/activate
python -m pip install -e '.[dev,semantic,images]'

export WS="$HOME/AdventureAI-clean.QJnopt"
export REVIEWS="$WS/reports/archive-review-first16-v1/assignments.json"
~~~

If the branch is already checked out locally use
\`git switch feat/adaptive-temporal-second-look-v1\`.

### 1. First get adaptive-v2 suggestions on existing v1 source-provenanced previews

~~~bash
klip --workspace "$WS" archive-local-triage \
  --assignments "$REVIEWS" --model gemma3:4b \
  --judge-policy adaptive-v2 --limit 4

caffeinate -i klip --workspace "$WS" archive-local-triage \
  --assignments "$REVIEWS" --model gemma3:4b \
  --judge-policy adaptive-v2 --limit 4 --run
~~~

The second command only needs local Ollama model time for already verified
preview clips. If a preview is missing/corrupt, the existing v2 path
first re-verifies the source DJI recording to generate a safe replacement
derivative. Do not run simultaneously with an SSD-heavy job.

### 2. Dry-run the evidence request

~~~bash
klip --workspace "$WS" archive-temporal-look \
  --assignments "$REVIEWS" --model gemma3:4b \
  --limit 4
~~~

If the report shows \`missingVerifiedPreviews\` or
\`awaitingAdaptiveV2\`, first prepare or v2-judge those exact
intervals. The second look never silently triggers a source hash.

### 3. Run only four evidence-eligible intervals

~~~bash
caffeinate -i klip --workspace "$WS" archive-temporal-look \
  --assignments "$REVIEWS" --model gemma3:4b \
  --limit 4 --run
~~~

Or target a single frozen assignment by ID:

~~~bash
klip --workspace "$WS" archive-temporal-look \
  --assignments "$REVIEWS" --model gemma3:4b \
  --only-review-id 41f18437aa40041d5200 --run
~~~

This last ID is the real first-ray *candidate hypothesis*, not a
verified animal encounter. If a human review already exists for that
specific assignment, the second look will politely skip it rather
than override your final judgment.

### 4. See seven-frame evidence in Studio

~~~bash
klip --workspace "$WS" studio \
  --benchmark "$REVIEWS" \
  --local-judge-model gemma3:4b \
  --judge-policy adaptive-v2
~~~

Open the appropriate candidate, then **Inspect 7-frame sequence**.
The button is enabled only with existing adaptive-v2 evidence and
a locally verified short preview. It adds an explanatory second-look
summary, referenced frame observations and comparison with the
first-pass recommendation. **Human K/S/U remains the only approval
control**. No catalog labels or human ratings are changed.

## Scoring and scientific quality

- Keep \`observedEvent\`, model \`recommendation\`, human preference
  and \`needsContinuousVideoReview\` as *separate fields*.
- A ray-shaped shadow is a possible event, not a manta/eagle species
  classification. Water clarity and local subject visibility must
  not be conflated.
- Seven frame screenshots might suggest a skier fell and then
  continued; they do not prove uninterrupted falling and recovery.
- Animal and athlete counts, species, athlete technical skill and
  athletic outcomes are never verified by this evidence path.
- The model cannot tune user preferences automatically without a
  validated human-feedback test set.
- Every preview/image stays local. Original indexed camera files and
  signed frozen plans are immutable.
- Source integrity is carried via the previously SHA-verified H.264
  preview receipt, not silently waived.

## Next experiment

Compare human review effort and human Keeps in the first four clips
before versus after a seven-frame second look. In particular, measure
the fraction of three-frame \`Unsure\` results that become
**evidence-supported** but still provisional decisions. Include
systematic controls, short wildlife appearances, chairlifts and
technical riding. Only then test a more expensive video-native model
or track-level inference; more samples alone are not proof of
higher factual accuracy.

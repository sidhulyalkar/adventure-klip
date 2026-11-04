# Adaptive local judge v2: evidence-aware highlighting by sport

## Why this is separate from the currently running inference

The user's first \`archive-local-triage\` process runs the legacy model prompt.
**Let that job finish on its current Git revision.** Adaptive v2 is an
explicit opt-in and uses a different content-addressed model-cache filename.
The original cached suggestions, signed archive plans and human reviews are
never overwritten or silently reinterpreted.

This change does not run inference on the user's Mac or inspect the
private source recordings remotely.

## The guiding rule: "Worth inspecting" is not "confirmed achievement"

The judge now asks for independent evidence along seven axes (0–3,
null when unassessable):

| Axis | Question |
|---|---|
| eventInterest | Is there a potentially significant wildlife encounter, movement or visual moment? |
| subjectVisibility | Can we actually see the animal or focal subject, regardless of ambient murk? |
| ambientClarity | Is the environment itself clear or obscured? |
| narrativePotential | Could approach/action/aftermath form a meaningful story? |
| visualQuality | Is framing, exposure and focus usable? |
| learningPotential | Could reviewing this help technique, observation or filmmaking? |
| sharingPotential | Would an outside viewer plausibly understand and enjoy it? |

This is a **triage rubric**, not measured image-quality metrics, learned
user preferences, calibrated probabilities, or validated sport technique.

A separate human rating may additionally record archive novelty and personal
meaning, which **cannot be inferred from three isolated stills alone**.

### Context-specific attention

- **Snorkeling:** shallow pale sandbars are not a low-value negative.
  Distinctive ray-like silhouettes deserve investigation when water is
  murky but the animal itself is visible. Above-water kayak transport is
  not an underwater wildlife encounter. No species counts, taxonomic
  rarity or behavioral narratives without additional evidence.
- **Ski:** separate lift, flat/traverse, downhill and terrain approaches.
  A dramatic feature is not proof of a successful jump or turn.
  Smooth sustained skiing need not cause large optical flow.
- **Bike:** separate climb/transfer, flowing trail and technical obstacle.
  A rock garden or jump in view is not proof of riding or landing.
  Camera shake must not stand in for athletic difficulty.
- **Unresolved:** the prompt explicitly asks the model to identify the
  visible scene, even when a catalog/activity hypothesis is wrong.

The activity hint is a **weak non-truth prior**. We never supply
candidate/control assignment labels, your saved review labels or other
algorithmic ranking decisions to the model's text prompt.

### Conservative post-model decision gate

v2 saves both the **raw** model recommendation and the **safe** recommendation.
A Skip becomes Unsure when a temporally important event could be outside the
three frames, the event itself is uncertain, or event interest is high.
An unsupported Keep for an invisible/ambiguous event becomes Unsure.

Neither v1 nor v2 can approve a clip, claim a species ID or write a human
review. These gates protect recall, but do **not** guarantee that rare events
are never missed.

### Cache/provenance

A cached v2 judgment is valid only for its local model tag, exact source
preview SHA-256, sport-context hint and fingerprint of the complete
prompt, schema, sampling fractions and version. Legacy v1 suggestions are
preserved under their older separate filenames.

A mutable Ollama model tag is not an immutable model digest. For scientific
comparisons we must additionally record the exact installed model digest,
the video session/day split and the prompting configuration.

## Workflow

### 1. Finish the current legacy local inference

Do not switch Git branches or restart Ollama while its current process
is running. The two existing human reviews remain in Klip's workspace.

### 2. Switch after the run ends

~~~bash
cd "$HOME/Documents/Projects/adventure-klip"
git status --short
git fetch origin
git switch --track origin/feat/adaptive-local-judge-v2
source .venv/bin/activate
python -m pip install -e '.[dev,semantic,images]'
export WS="$HOME/AdventureAI-clean.QJnopt"
export REVIEWS="$WS/reports/archive-review-first16-v1/assignments.json"
~~~

If the branch already exists locally, use \`git switch feat/adaptive-local-judge-v2\`
instead.

### 3. Do a zero-compute dry-run first

~~~bash
klip --workspace "$WS" archive-local-triage \
  --assignments "$REVIEWS" --model gemma3:4b \
  --judge-policy adaptive-v2 --limit 4
~~~

### 4. Evaluate four unreviewed intervals with the new policy

~~~bash
caffeinate -i klip --workspace "$WS" archive-local-triage \
  --assignments "$REVIEWS" --model gemma3:4b \
  --judge-policy adaptive-v2 --limit 4 --run
~~~

This intentionally **re-judges** a small group under the new prompt,
rather than claiming that earlier v1 model opinions qualify as v2.
It reuses source-verified H.264 preview files where available, but still
uses local CPU/GPU time and Ollama requests on 127.0.0.1. No hosted upload.

### 5. Compare saved model outputs against your human decisions

~~~bash
klip --workspace "$WS" archive-judge-audit \
  --assignments "$REVIEWS" --model gemma3:4b \
  --output-dir "$WS/reports/compare-local-judge-policies-v1"

open "$WS/reports/compare-local-judge-policies-v1/index.html"
~~~

The audit uses **existing** model cache files only. It reports where
legacy or adaptive v2 would have deprioritized an interval that you kept,
how much of the sample actually has both policy results, and which
sport/control strata are insufficiently reviewed.

Do not claim one policy is better unless the **same** reviewed intervals
have suggestions from both models, with a sufficiently large, independent
evaluation set. Human Keep/Skip is a personal preference label, not a
verified species or athletic-event ground truth.

### 6. Use the revised prompt interactively

~~~bash
klip --workspace "$WS" studio \
  --benchmark "$REVIEWS" \
  --local-judge-model gemma3:4b \
  --judge-policy adaptive-v2
~~~

The Studio displays v2 explanations, axes, uncertainty and prompt ID,
but the K/S/U human decision remains authoritative. The next milestone
is showing the seven dimensions in a compact explanatory inspector,
not requiring you to enter scores for all of them.

## Research evaluation before scaling

After at least 15–30 paired human-vs-model intervals, include deliberately
hard examples: murky sandbar with a ray-like subject, clear empty beach,
above-water kayak, single-frame shark-shaped shadow, chairlift, smooth
downhill turns, unproven ski jumps and bicycle obstacles without a landing.

Compare v1 and v2 on the **same** held-out intervals: "would skip a human
keep", human-review savings, explicit abstentions, budget per analyzed
clip, and false-negative rates for *independently labeled events*. Use
trip/session/day-disjoint splits when tuning later prompts. Do not
optimize solely against the first two human reviews.

Future v3 should selectively gather **additional temporal evidence**
before a final judgment when \`needsTemporalReview\` is true, rather
than just adding more static adjectives to the model instructions.

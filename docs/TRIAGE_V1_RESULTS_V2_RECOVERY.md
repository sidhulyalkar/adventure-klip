# After first local triage: diagnose, score and compare v2 efficiently

## Observed baseline, from the first real Gemma 3 4B run

The user's exact first `archive-local-triage` run:
- Selected **8** unreviewed assignments (6 candidate hypotheses, 2 systematic controls), with **2 existing human reviews preserved**.
- Produced **7 model suggestions**, all **unsure**. This is a successful conservative description pass but **not an effective final editorial ordering**.
- Failed **one** systematic-control interval with `ValueError`, not further classified in the legacy report.
- Modified **zero human labels** and performed **zero external media uploads**.
- Among unverified summaries were a ray-like form followed by sand, skiing near a lift, a possible fall, beach riding, a possible parked rental motorbike, reef scenery, and kayak transport. **These are model observations, not independently verified events.** The model cannot substantiate a fall-to-recovery sequence, fish species, technical performance, or wildlife rarity from three frames.

**The failure is review ID** `c50944e9b0b44d5a23f0` on `DJI_20241220214252_0015_D.MP4`. The legacy output does not give enough information to identify its cause. Do not assume this is an FFmpeg error: the exception was `ValueError`, and invalid Ollama JSON or model response is also possible.

## What changed (new PR stacked after adaptive v2)

- `investigationScore` is a deterministic **0–100 sorting heuristic**, not a highlight probability. It uses the seven-dimensional v2 rubric, but it deliberately gives **no direct penalty** for ambient water clarity and **no unverified species rarity bonus**. It abstains unless at least four relevant ratings, including event interest and subject visibility, were actually assessed.
- Studio sorts provisional model Keeps and Unsures by investigation score while preserving all failed and unreviewed intervals. **Every fourth unreviewed slot is reserved for a systematic control** when available, even if that interval looks uninteresting to the model.
- The paired local judge audit now displays the 0–100 investigation score alongside legacy/v2/human decisions, keeping unassessed values unknown.
- The local triage CLI now supports `--only-review-id` to retry exactly one failed assignment and emits a sanitized **failure stage, error category and elapsed seconds**, avoiding the old opaque `ValueError` report.
- Previously generated, source-SHA-verified, checksum-validated short H.264 previews can be reused for adaptive v2 inference without rehashing their multi-gigabyte originals, both via batch CLI and within Studio. **If the preview or verification receipt is absent/invalid, Klip re-verifies the original before generating a new derivative.**
- Snorkeling prompts protect the manta/eagle-ray identity boundary, and biking prompts distinguish pedal mountain bikes, motorbikes, parked rentals and beach transport.

## User workflow after the current inference has completed

```bash
cd "$HOME/Documents/Projects/adventure-klip"
git status --short
git fetch origin
git switch --track origin/feat/triage-ranking-diagnostics-v1
source .venv/bin/activate
python -m pip install -e '.[dev,semantic,images]'

export WS="$HOME/AdventureAI-clean.QJnopt"
export REVIEWS="$WS/reports/archive-review-first16-v1/assignments.json"
```

If already tracking the branch, use `git switch feat/triage-ranking-diagnostics-v1`.

### 1. Diagnose only the previously failed video

```bash
klip --workspace "$WS" archive-local-triage \\
  --assignments "$REVIEWS" --model gemma3:4b \\
  --only-review-id c50944e9b0b44d5a23f0 --run
```

This retries only that unreviewed assignment with the **legacy** model prompt, reusing completed proxy/cache work where possible. If it still fails, the new report gives `failureStage`, `errorCode`, `diagnostic`, and `elapsedSeconds`. Do not automatically convert a failed review into a Skip or "no event."

### 2. Compare v2 on four already-available previews

```bash
# Preview which four unreviewed intervals v2 would inspect:
klip --workspace "$WS" archive-local-triage \\
  --assignments "$REVIEWS" --model gemma3:4b \\
  --judge-policy adaptive-v2 --limit 4

# Explicitly run the new sport-aware prompt:
caffeinate -i klip --workspace "$WS" archive-local-triage \\
  --assignments "$REVIEWS" --model gemma3:4b \\
  --judge-policy adaptive-v2 --limit 4 --run
```

Existing v1 AI suggestions and previous human reviews are preserved. The v2 cache uses a separate prompt/source fingerprint. **Valid earlier short previews are reused**, avoiding new full-original SHA passes for those intervals. This still calls the local Ollama vision model, and its 3-frame sampling and response validity must be confirmed on the actual Mac environment.

### 3. Perform cache-only paired audit and review ranked queue

```bash
klip --workspace "$WS" archive-judge-audit \\
  --assignments "$REVIEWS" --model gemma3:4b \\
  --output-dir "$WS/reports/triage-policy-comparison-v1"

open "$WS/reports/triage-policy-comparison-v1/index.html"

klip --workspace "$WS" studio \\
  --benchmark "$REVIEWS" \\
  --local-judge-model gemma3:4b \\
  --judge-policy adaptive-v2
```

Use a **new output directory** if the previous audit report already exists.
The audit creates no new inference or source reads. The Studio queue keeps
a systematic-control exploration floor, and AI-derived rank is shown as a
heuristic that still requires your final human review.

## Evaluation priorities

The first 7/7 Unsure suggestions are best treated as a **baseline failure of
discrimination**, not evidence that the model cannot recognize scenes.
Measure:

1. **Human Keep/Skip disagreement** among the same reviewed clips under v1
   and v2, particularly whether either would de-prioritize human Keeps.
2. Whether the investigation score meaningfully separates more interesting
   observed moments within the model's Unsure group.
3. **Unreviewed controls retained** per batch and the fraction containing
   human-validated interesting moments.
4. Cache-reuse savings in **source bytes read / SHA passes / wall time**.
5. Failure cause for the one `ValueError` from the exact review ID.

Do not claim v2 is more accurate based on these eight nonrandom source intervals
or the two older human reviews. For a scientific improvement claim, build
session-disjoint, held-out reviews and a random interval sample.

Next-stage model quality: when `needsTemporalReview` is true, gather
**targeted additional timestamps** around the proposed event before
determining outcomes. One static three-frame pass cannot verify a fall and
recovery, landed bike feature or detailed animal behavior.

# Recover partial local triage without losing controls or reviewed clips

## Observed November 4 adaptive-v2 pilot

- Four selected: three candidate hypotheses, one systematic control.
- Two provisional `keep` suggestions: ray-like subject on pale sand, and a ski scene.
- Bike candidate `2839d5d62b61b08b3d37`: `RuntimeError` after ~150s in the local judge sampling/response stage. The report cannot prove why Ollama stopped. It is **not** a human Skip.
- Unresolved systematic control `721170b3636eea4b7a83`: a `ValueError` with `judge_frame_sampling_error` after ~24s. It is **not** a negative event.
- Two human reviews pre-existed; zero new human labels or verified events.
- Cache-only audit compared two clips with both policy outputs. No improvement claim is supported.

## What recovery changes

On `feat/triage-failure-recovery-v1`, derived from the seven-frame PR #49:

1. The three-frame and seven-frame proxy samplers probe duration once and first try the existing fast seek. If one frame is missing/corrupt or FFmpeg returns a failure, they retry that **one frame** with a more accurate post-input seek. A timed-out decoder is not automatically retried.
2. An Ollama timeout is distinguishable from an unavailable server, invalid response, or FFmpeg frame-sampling timeout in bounded JSON diagnostics. The runner still proceeds to the next item.
3. Both `archive-local-triage` and `archive-temporal-look` accept repeatable `--exclude-review-id ID`. This changes **only the selection for the current invocation**. It does not label, delete, hide, or permanently blacklist a review interval. Unknown IDs and conflict with `--only-review-id` fail explicitly.
4. Original DJI media remain read-only; human review revisions and all v1/v2/second-look caches stay intact.

## Reproduce and qualify locally

Do not switch branches while Studio is running and you still need the existing executable. Stop Studio with Ctrl+C after you finish the current human-review session. From a clean worktree:

```bash
git status --short
git fetch origin
git switch --track origin/feat/triage-failure-recovery-v1
python -m pip install -e '.[dev]'
python -m pytest -q tests/test_triage_failure_recovery.py \
  tests/test_triage_priority_diagnostics.py \
  tests/test_temporal_second_look.py
```

If tracking the branch already, use `git switch feat/triage-failure-recovery-v1`.

Set `WS` and `REVIEWS` to your existing workspace and frozen assignment JSON.
For example:

```bash
export WS="$HOME/AdventureAI-clean.QJnopt"
export REVIEWS="$WS/reports/archive-review-first16-v1/assignments.json"
```

### Target the two failures separately

```bash
# Read-only environment inspection.
ollama ps
ollama list
ffmpeg -version | head -n 1
ffprobe -version | head -n 1

# Re-try only the bike candidate that previously hit ~150s.
klip --workspace "$WS" archive-local-triage \
  --assignments "$REVIEWS" --model gemma3:4b \
  --judge-policy adaptive-v2 --only-review-id 2839d5d62b61b08b3d37 --run

# Re-try only the unresolved systematic control with frame-sampling failure.
klip --workspace "$WS" archive-local-triage \
  --assignments "$REVIEWS" --model gemma3:4b \
  --judge-policy adaptive-v2 --only-review-id 721170b3636eea4b7a83 --run
```

If a long timeout recurs, preserve the report, review that clip in Studio and inspect local Ollama memory/model load before trying again. Do **not** increase retries or timeout automatically.

### Advance past all four already-inspected assignments

```bash
klip --workspace "$WS" archive-local-triage \
  --assignments "$REVIEWS" --model gemma3:4b \
  --judge-policy adaptive-v2 --limit 4 \
  --exclude-review-id 41f18437aa40041d5200 \
  --exclude-review-id a13280c42e09dc19fb3c \
  --exclude-review-id 2839d5d62b61b08b3d37 \
  --exclude-review-id 721170b3636eea4b7a83
```

Inspect this **dry-run** selection first. Add `--run` for an explicitly approved local Ollama pass. This still reserves controls whenever eligible controls remain, and the four excluded assignments remain in Studio for manual review.

### Seven-frame second look, only when genuinely eligible

```bash
klip --workspace "$WS" archive-temporal-look \
  --assignments "$REVIEWS" --model gemma3:4b --limit 4
```

A seven-frame look **requires a validated cached H.264 proxy and a completed adaptive-v2 result that is unsure or explicitly needs temporal investigation**. Current two provisional Keeps may not meet that gate. An interval with failed v2 inference has no usable first-pass result. The dry-run's `awaitingAdaptiveV2`, `missingVerifiedPreviews`, and `eligibleUnreviewed` fields should guide the next step. Do not force ineligible clips or run the entire archive.

## Scientific release gate

- Unit tests and CI pass on Ubuntu/macOS.
- Local real proxy sampling succeeds or returns an actionable error; no source original is modified.
- A failed inference is not a Skip or a negative observed event.
- Controls remain available despite exclusions.
- A human reviews a stratified sample including model Keeps, Unsures, Skips, sampling failures and systematic controls.
- Before declaring ranking improvement, compare held-out, session-disjoint human Keep yield, missed Keeps in controls, human minutes per accepted moment, and the rate of supported vs unsupported descriptions.

A successful local CLI pass is engineering qualification, not proof of model-based highlight quality.

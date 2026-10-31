# Clip trim policy v1: evidence-aware, review-first cuts

## Objective

Klip should give people a defensible *candidate cut* rather than mistake the largest
optical-flow spike for the best clip. The ideal cut includes enough context to make
the event legible, protects action/encounter continuity, excludes only explicit
human-marked unwanted footage, and records exactly which assumptions were used.

The 315-video DJI processing run can continue independently. This policy reads
previously indexed database rows and cached analysis. It never scans or decodes
original footage and does not mutate an original, re-run an ML model, render an
output, or automatically save a review.

## Source evidence versus editorial decision

1. **Raw source**: a SHA-256-identified recording, source-relative timestamps.
2. **Candidate episode**: an unverified proposed interval from motion and other
   cached observations. A camera jolt is *not* a verified jump, crash or injury.
3. **Trim proposal**: deterministic pre/post context and rule-based duration limits,
   plus explicit human-guard checks. Never confused with human truth.
4. **Preview**: an opt-in player seek using proposed source-relative bounds.
   The annotation fields update for inspection; nothing is saved at this stage.
5. **Manual preview**: change in/out seconds or use [ / ] to set source
   positions, then click **Preview my cut** to see those precise entered bounds
   in the native player. This is still a preview and writes nothing.
6. **Review**: the user independently judges (a) whether the **scene label** is
   correct and (b) whether the **footage** should be kept. They adjust start/end
   and save. Revision history preserves their choices.
7. **Export**: only human-kept clips can be assembled in the existing Sequence
   editor. Unconfirmed action labels export as `unverified_moment`.

## Deterministic policy table

| Candidate | Before / after context | Suggested maximum | Minimum useful reviewed span |
|---|---:|---:|---:|
| Ski/bike multi-frame maneuvers | 2.0 / 2.8 sec (ski), 2.2 / 2.8 sec (bike) | 24 sec | 3.5 sec |
| Snorkeling/wildlife encounter | 3.0 / 4.0 sec | 35 sec | 5 sec |
| Incident/camera disturbance | 5.0 / 8.0 sec | 35 sec | 5 sec |
| Generic motion | 1.1 / 1.5 sec | 16 sec | 3 sec |
| Unknown-activity multi-frame event | 1.5 / 2.0 sec | 18 sec | 3 sec |

All values are **initial heuristics**, not biomechanical, artistic, medical, or
scientifically validated thresholds.

If a candidate action/event is too long for the maximum, *preserve it whole* and
flag `manual_split`. When the core plus padding exceeds the budget, sacrifice
padding first, never any of the action core. For generic motion proposals with no
verified event, propose a centered shorter **alternative** and clearly state
that the actual action could be missed. **Active sequences and generic
snorkeling candidates are exempt from midpoint shortening** because they may
contain continuous action or wildlife footage; these retain their full
candidate interval even when the label is uncertain. Always keep the original
candidate.

## Hard invariants

- A suggested `[startMs,endMs)` must be within `[0,durationMs]`, with
  `startMs < endMs`.
- Explicit manual negatives or latest human Skip intervals are hard guardrails.
  **Never** cross one to obtain context. A guard overlapping the candidate core
  results in `blocked`, not a deceptively clean splice. The UI cannot apply
  a blocked cut with one click.
- Never call `round(t * fps)` to fabricate sample precision. These times are
  **source-relative millisecond suggestions**, not verified exact decoded PTS
  or keyframe-aligned positions. The frame-level cut must be inspected in
  playback; an eventual FFmpeg render must check source duration and identity.
- Never infer that a person fell, had an injury, breathed, or performed a
  specific maneuver from camera motion, audio or zero-shot similarity alone.
- No automatic rewriting of user Keep/Skip, original files, or edit plans.
  Explicit human preferences remain the authority.
- If missing/unusable source duration, offer **no** trim proposal instead of
  quietly guessing; the source can still be watched in the Library.
- All trim responses are versioned `trim-policy-v1` and have `requiresReview=true`.

## Explainable response

```json
{
  "version": "trim-policy-v1",
  "status": "suggested",
  "canApply": true,
  "startMs": 38000,
  "endMs": 58000,
  "coreStartMs": 40000,
  "coreEndMs": 56000,
  "durationMs": 20000,
  "reasons": ["Added source context before/after the candidate within safe bounds."],
  "warnings": ["Review the first and final frames; source-relative milliseconds are not frame-aligned."],
  "requiresReview": true,
  "timestampBasis": "source_relative_ms"
}
```

Other states: `unchanged` (no value in a different cut), `manual_split`
(action/incident too long), `blocked` (explicit human exclusion crosses core),
`manual_review` (no safe footage reaches a useful minimum). Only
`suggested` exposes the one-click preview action.

## What is still missing

This is a robust **rule foundation**, not the last word in aesthetic editing.
The next tier should learn *where to move* a proposed boundary. Only use
source-verified frame PTS and observed evidence; incorporate shot-change
boundaries, deliberate camera pans, action onset/apex/completion hypotheses,
optic-flow troughs, occlusion/blur, and sound-continuity clues. Assess evidence
coverage and abstain if sparse. Avoid cutting through surfacing, landings,
animal interactions or turns. These are future implementations, not claims of
the current trim policy.

Audio and video should both be treated as fallible. For clip selection we
want to retain multiple alternatives around a possible event until they have
been compared by a human, not collapse them into one fictional "best" point.

## Evaluation protocol for ongoing corpus

First gather **independent human gold boundaries** from a stratified sample
of at least 60 candidate intervals spanning multiple recording days and
activities, including hard negatives (chairlift, idle footage, pans, flat
skiing, underwater surfacing, camera bumps). Retain both the proposal and
original event span. Score:

- **Hard invariant failures**: crossings of human exclusions, source overflow,
  source modification or unreviewed automatic export. Required: zero.
- **Event retention**: fraction of human-identified critical action windows
  fully preserved in a proposed clip.
- **Boundary error**: absolute start/end difference from human-selected
  in/out (report median, p90 and distribution by activity).
- **Context adequacy**: reviewer indicates whether approach and outcome are
  visible; assess independently of frame sharpness.
- **Editorial time**: seconds to reach a usable trim versus manually placing
  both boundaries from scratch, randomized by clip with blinded condition.
- **Candidate quality**: useful Keeps per 20 surfaced reviews; separately
  measure missed human-worthy intervals from random source sampling, to avoid
  evaluating recall on model-picked windows alone.
- **Temporal leakage**: split by trip/date and near-duplicate recordings,
  not adjacent windows of the same video.
- **Source/frame correctness**: use real DJI HEVC/VFR records to verify that
  player and renderer refer to the same source-relative interval. An exact
  frame-accurate claim requires measured output frame timestamps, not just
  rounded seconds.

Decision thresholds should be calibrated *after* this evidence; do not claim
a fixed accuracy or utility gain without human trials.

## Local development

Use a **separate Git worktree** while the 315-video indexing/analysis continues;
do not switch the running process's checkout, reinstall its environment or
change its processing arguments. The new API and UI code can be tested against
an isolated synthetic workspace. Merge the stacked PRs only after tests pass
and the active processing job is safely finished.


## Optional cached boundary inspector (v0.8, development branch)

The focused editor offers **Inspect cut boundaries** inside the annotation panel.
This makes one token-protected GET request to `/api/boundary/<asset-sha>?startMs=&endMs=`
for the currently entered source-relative in/out range. The backend uses only
completed, checksum-verified **motion/flow artifacts** already in the workspace.
It creates 48 lightweight bins showing relative *camera/image* motion, with
explicit empty bins for missing data. A low-motion trough near the cut is only
a click-to-seek marker and does **not** automatically adjust or save the cut.

The display abstains from offering trough hints if fewer than 16 observations
or under 65% bin coverage are available. Source-relative timestamps are
*not* guaranteed frame-accurate. The API rejects windows over 180 seconds,
invalid source IDs, or ranges outside the indexed duration. It reads only.

**Preview 5 local frames** is a second, explicit action. It initializes one
muted browser decoder for one source, performs at most five seeks at the
currently entered in/out range, and renders 220×124 JPEG previews locally.
It never uploads, saves or shares the images. Leaving the clip releases the
decoder and clears frame previews. This may cause temporary extra I/O on the
same Mac as an active DJI indexing run, so leave it unused during heavy
processing if the machine is I/O constrained.

Browser seeks are approximate; a five-frame strip is not a guarantee of
decoded frame PTS or continuous action coverage. HEVC and unusual recording
timelines may fail to display in some browsers. Use the original player and
the existing frame-timing evidence to validate any precision-critical cut.
The display does not run inference, download models, transcode source video,
or write to the indexed DJI folder.

Future work: compare reviewer-chosen boundary corrections with cached
motion troughs, shot changes, semantic context, original PTS and measured
rendered-frame PTS across distinct trips, before allowing any automatic
boundary-shift recommendation.

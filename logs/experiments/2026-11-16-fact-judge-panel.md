# Fact judge panel | 2026-11-16

**Evidence level:** implemented, synthetic CI passed with a fake transport. No model has
been run. Nothing here says any judge is accurate.

## Why

The seven-still Gemma 3 4B scout proposed `ski_park` for 45/45 ski clips and blur for
102/108. Asking a model for categories or quality verdicts gave answers that could not be
checked cheaply. The owner cannot spend hours annotating, so model output has to be
verifiable in minutes.

## Design

- Judges answer narrow yes/no/unclear questions about what is visible (take-off, landing,
  animal large in frame). They are never asked whether a clip is a highlight.
- Frames: an even spread plus a burst around each event candidate from
  `timeline_segments`, up to 16, so take-off and outcome are both shown.
- Control questions from another sport catch a judge that agrees with everything; such a
  judge is excluded for that clip.
- At least two model families must answer before anything counts as agreed. Disagreement
  is listed for the owner, not voted away. A failed reply is an abstention, not a "no".
- `calibrate` scores each judge per question against owner yes/no answers, reporting
  false-yes and false-no separately.

## Intended first run (owner machine: M1 Max, 64 GB)

`qwen3-vl:32b` (about 21 GB) and `gemma3:27b`, both present in the Ollama library as of
this date. Loopback only. Ollama lists these as text+image models, so "video" here means
ordered frames.

## Not established

- Whether either model answers these questions correctly on first-person footage.
- Whether 16 frames at 448 px are enough to see a landing.
- Per-question accuracy; needs roughly 30 owner-checked clips before trusting any number.
- The rubric wording is a first draft and has not been checked against real clips.

## Next decision

Run both judges on the reviewed park run and the kept reef scene, where the owner already
knows the answers, before any wider sweep.

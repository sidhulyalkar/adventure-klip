# Variable-length timeline segments | 2026-11-16

**Evidence level:** implemented, synthetic CI passed (767 tests). No real footage or real
motion series has been run through it.

## Why

`full_archive_campaign._proposals` emits at most 16 windows of 20 s per recording whatever
its length (320 s, about 18% of a 30-minute file), and a 20 s window cannot contain the
reviewed 33.7 s park line. The coarse scan also discarded its decoded motion timeline and
kept only a sample count, so any new segmentation idea needed another full source read.

## What changed

- `timeline_segments.py`: tiles the full timeline with variable-length segments and no
  maximum length. With a motion signal, a boundary is a lasting shift in level (medians
  before and after differ by half or more); a short spike is an event candidate inside its
  scene. Review budget is a fraction of duration, and unranked controls are kept so misses
  can be measured.
- `span_recall`: share of owner-kept spans at least 80% covered, plus whether each sits
  inside one proposal. `compare_to_fixed_windows` runs the old generator on the same series
  at the same footage budget.
- The coarse scan now saves `per-source/<sha>.motion.json` beside each receipt. The receipt
  schema and version are unchanged. Existing scans do not have this file.

## Synthetic result (mechanics only)

A constructed 48 s signal shaped like the reviewed run (standing, riding with four spikes,
standing) yields one 5.5-39.5 s segment holding all four event candidates. Fixed windows
cover the span only across several windows. This shows the algorithm does what it was
written to do. It is not evidence about real ski footage, where riding and standing may
not differ this cleanly.

## Not established

- Whether real POV motion separates scenes by level at all; underwater footage may not.
- Threshold values (8 s context, 0.5 shift ratio, 3 MAD) are untuned defaults.
- Embedding-based boundaries are implemented and unit tested, not compared with motion.
- The sidecar write in the coarse scan is not covered by any test; no CI test completes a scan.

## Next decision

Re-scan 8-12 approved recordings to obtain motion timelines (needs owner approval and the
SSD), run `scripts/klip_timeline_segments.py` with the ledger, and read intact-rate against
the fixed-window baseline before changing the campaign's default proposal generator.

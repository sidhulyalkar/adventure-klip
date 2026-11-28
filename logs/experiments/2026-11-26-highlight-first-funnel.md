# 2026-11-26: Owner claim review and the turn to a highlight-first funnel

## Evidence

* Owner claim review of the first grounded stories: 101 claims answered (deck of 162):
  56 true, 39 partly true, 6 wrong. Model-written stories, owner-reviewed.
* Corrections by theme (owner notes): "morning" ×6 (the camera clock is not local time);
  body-contact and wearer inventions ×5 ("hands touching coral", "watch on foot"); not
  specific ×19 (no species, no counts); wrong portion ×12 ("the stingray is at 8:40, clip
  just that"); non-highlight footage described (lift lines, chairlifts, poor snorkels).
* `harvest` over the workspace found no marine owner exports under `reports/` (9 judgments
  total, 0 owner-marked spans). The earlier marine study's exports are outside `reports/`
  (browser downloads); `find-labels` now locates and pairs them with their frozen samples.

## Decision

Stop spending model time and owner time evenly. Search every stored frame for the owner's
targets (animals by species, companions, fast / airborne skiing inside real runs), verify only
those with closed questions, and show the owner the best plus a blind audit. Design and
acceptance criteria: [KLIP_HIGHLIGHT_FUNNEL.md](../../docs/KLIP_HIGHLIGHT_FUNNEL.md).

## Status

| | |
|---|---|
| Implemented | stage A search, stage B verify, stage D review + import + evaluation; story prompt revision 2 |
| Synthetic CI | passed (tests/test_highlight_search.py, tests/test_story_harness.py) |
| Real-media pilot | not yet: needs `encode-queries` and `search` on the owner's Mac |
| Owner-reviewed | no |
| Full-library qualified | no |

## Open questions for the owner

* Any other animals always worth keeping (needlefish, octopus, dolphins are in the list)?
* What makes a ski moment "best": speed, steep or powder, jumps, a full run, friends in frame?
  The default weights (speed 0.5, visual 0.35, skier ahead 0.15) stand until then.

## First footage run (2026-11-27, search run acaf4845-4b22fd2a)

* Inventory: 195 searchable recordings (98 evidence-only without motion, 19 accidental and 18
  other-sport recordings left out). Candidates: 181 animal, 156 ski_action, 98 ski_run,
  1 companion; 122 audit windows.
* Verify (qwen3-vl:8b-instruct, 40 calls): 45 of 85 queued candidates were skipped because
  `--trusted-source-root` pointed at the code checkout. All 113 discovery and 232 catalog
  originals are under `/Volumes/Sid_DJI_SSD/DCIM/DJI_001`. Only windows already covered by
  a discovery proxy were verified, so this first deck is biased toward previously-reviewed
  footage. It is not used for the acceptance criteria.
* Species agreement between search and verifier on the 38 answers naming an animal:
  25 (0.66). Sea turtle 11/11 and shark 9/11, but manta 5/15 (8 called stingray) and
  dolphin 0/2. Search never proposed "stingray" although it scores that query, so rays fall
  to manta / eagle ray. Whether search or verifier is right waits for owner review.
* `find-labels`: the frozen marine sample was found; no owner export paired with it.
* Fixes in `highlights/clips-library-showcase-v1`: pages report the cause of each missing
  clip; library page cuts previews from originals; exports and showcase (stage E in the
  funnel doc).

## First owner review of the highlights library (2026-11-27, library page deck 0c9eb4706a4e)

56 of 56 moments answered across skiing, snorkeling and kayaking: 44 keep, 4 maybe, 8 skip;
12 with best-part marks (one with a start mark only), 33 with notes. Ratings among keeps:
6 five-star, 4 four-star, 13 three-star, 13 two-star, 6 one-star, 2 unrated.

What the notes asked for (summarised; the notes stay in the owner's labels, not here):
* Many keeps were marked as personal memories rather than highlights, so "kept" and "show
  it" are separate: ratings now set a purpose (highlight 4-5 or starred best, memory 1-2).
* Several moments are parts of one action (a terrain-park line, the run before a crash) and
  should play and export together, sometimes starting earlier than the clip shown: linked
  sequences and "needs more before / after" were added.
* The one start-only trim would have been dropped by import; single marks are now kept.
* Snorkel ticks used "other animal" for needlefish, angelfish and fish schools.

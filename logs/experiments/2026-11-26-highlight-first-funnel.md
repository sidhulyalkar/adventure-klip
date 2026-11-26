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

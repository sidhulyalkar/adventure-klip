# E | Within-outing value learning: baseline ladder, taps, preference head

- Date: 2026-11-17
- Status: implemented, CI-passed (synthetic). Not run on owner footage.
- Branch: `value/within-outing-preference-v1`, stacked on `feat/klip-fact-judge-panel` (a8998df).
- Owner objective: rank an outing's footage so the moments the owner would keep come first,
  and learn this from owner choices rather than hand-set weights.
- Hypothesis to test on real data: audio surprise plus within-outing novelty predicts owner
  preference above chance; a small learned head adds to that only if the evidence carries
  more than one useful signal.
- Data cohort: none yet. Needs 8–12 recordings from at least 5 confirmed outings.
- Holdout: leave one outing out. Roster from capture-time gaps, provisional until confirmed;
  recordings with no outing are each their own group. A test instruments the fit and fails
  if a held-out outing's pairs reach its model; another shows scaling and embedding axes
  do not change when held-out evidence changes.
- Inputs: discovery motion (2 fps), new 1 s audio loudness/flux stage, segments, judge
  facts, cached SigLIP2 frames when present. Proxies now keep a 64 kb/s mono audio track
  (`proxyAudio` in the frozen config).
- Execution: see `docs/KLIP_VALUE_LEARNING_RUNBOOK.md`. Only the discovery step reads
  originals; everything else reads derived JSON.
- Quantitative measurements: synthetic only. With value planted in a loud second, the
  audio baseline and the head both separate keeps; the gate correctly reports that the
  head adds nothing. With value planted only in embedding content (not rarer than other
  footage), single measurements stay below 0.7 and the full head exceeds 0.85 held out;
  the gate credits the head. These show the mechanics, not anything about real footage.
- Human judgment: none yet.
- Controls: chance (analytic for pairs, 200 shuffles for recall), each single measurement,
  rank fusion of audio surprise and outing novelty, head without and with embedding/facts.
- Tests: `python -m pytest -q tests/test_value_learning.py tests/test_discovery_experiment.py`
  (33 tests); full suite 813 passed. Tap page driven in headless Chrome with real clips:
  load, sound toggle, keys, undo, draft survives reload, export validates against deck.
- Safety: no new original reads beyond the existing bounded discovery run; evaluations
  refuse to overwrite; ledgers append-only.
- Uncertainty: whether any measurement here tracks real owner preference; whether 960 px
  proxies change choices; whether goals should be modelled separately.
- Decision: keep; run on real data before any further modelling work.
- Next: owner confirms roster, runs discovery on 8–12 recordings across 5+ outings,
  imports existing exports, taps 150 pairs, runs `evaluate`.

## Revision 2 (same day): all owner labels, grid review, recording plan

- Audit: the ledger read 4 of about a dozen owner-review formats, and the learner used only
  keep/skip from those. Studio example reviews, Studio A/B choices, archive benchmark
  reviews, blind pair reviews, curated highlight manifests and all 1–5 ratings were unused.
- `owner_evidence.collect` now reads all of them read-only. It keeps the latest revision
  per item, merges duplicates, sets aside keep/skip contradictions, and lists recognised
  but unread files (moment annotations v2, reel reactions, unimported exports).
- Cross-outing pairs train only when neither outing is held out and never score; a test
  instruments the fit to confirm.
- Ratings order Keeps within one session at half weight, training only.
- New default review: 4-up grid from one outing, uniform random segments; tick keeps, star
  best. Gives keep labels, orderings, and an unbiased per-outing keep rate and recall
  (`sampledKeepRecallAtBudget`). Shuffled repeats measure consistency; contradicted clips
  and stars are dropped.
- `plan` writes a discovery source list that covers labelled recordings, one per outing
  per round; `harvest` reports coverage.
- Tests: 825 passed. Grid page checked in headless Chrome at a 1512×860 viewport: clips
  play, keys, star, sound, back, reload, export, import validation.

## Revision 3 (2026-11-18): first real numbers, trips, review app

- First real evaluation, 85 owner pairs over 4 days (84 from "athletic skill" curation):
  - audio mean 0.87, motion mean 0.77, learned head 0.44, chance 0.5. Pilot, no claim.
  - Read as Stage A ("something is happening"), not as value among active moments.
  - The head below chance is unexplained. Candidates are noise from tiny days and
    sports pooled in one model; a training-data sanity check is still to add.
- plan now picks recordings by the pairs they complete. One per outing completed 1
  of 258.
- Discovery survives unreadable recordings, such as an ffprobe failure on a
  truncated file.
- Review app (`klip_value_experiment.py review`): trips, days, per-recording activity
  ("accidental" excluded everywhere), and grid rounds saved per answer.
- Evaluation holds out whole trips by default; the day-level result is reported as
  optimistic.
- Trip gap of 4 days: the Polynesia trip has a 58-hour gap with no filming.

## Revision 4 (2026-11-18): description pass with pretrained models

- `moment_evidence`: keyframe-only decode about every 2 s, giving SigLIP2 image embeddings
  and CLAP audio embeddings per 2 s window. Stored per recording, resumable.
- Concepts are scored from the stored embeddings against an encoded vocabulary, so
  owner terms need no second pass over the footage.
- Each segment gets each concept's peak score and peak time, and per-recording activity
  proposals.
- The full head takes the concepts as readable columns. Novelty uses description
  embeddings.
- Checked:
  - keyframe timestamps match decoded frames exactly on a generated video;
  - encoder calls run against random-weight SigLIP and CLAP models on Transformers 5.23;
  - in synthetic data, a value visible only as a concept is learned by the full head
    (above 0.9), while energy signals stay below 0.7.
- Not yet run with the real weights. That first happens on the owner's Mac.

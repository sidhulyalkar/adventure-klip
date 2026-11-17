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

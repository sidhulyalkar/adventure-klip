# Clip Value Lab v1: learn what makes moments meaningful

This is the **first human-grounded quality calibration step** after the
archive pilot. The previous batch selected eight videos across activities,
and existing visual sampling/detection can supply hypotheses, but cannot
establish whether a clip demonstrates an achievement, is fun to watch,
contains an unusual animal, or is worth revisiting.

## What is new

- Versioned, frozen `clip-value-lab` packets from signed archive pilot
  plans: existing human examples, cached marine event-context candidates,
  episode hypotheses, and coverage probes where evidence is unavailable.
- **At least one deterministic random control per selected video.** Random
  controls may be spectacular or uninteresting; they are NEVER labeled
  negative by construction. Their presence lets us measure eventual recall
  and discovery blind spots.
- One local Studio page, `/value-lab`, for source-backed video playback and
  revisioned ratings. No clip copy, video upload or large thumbnail export.
- A multi-dimensional rubric with explicit **0–4** anchors (0 none/very
  poor, 1 weak, 2 some, 3 strong, 4 exceptional) and a `Not assessed`
  option. Only a person who sees the source can assign these values.
- Append-only SQLite `moment_value_revisions` with reviewer notes, source
  SHA, exact video-relative bounds, immutable packet ID and prior revision
  ID. The existing `example_revisions` and `pairwise_revisions` continue
  to work unchanged; we do not duplicate those semantics.
- A `value-report` adapter into the existing `moment_ranking` discovery,
  learning and share queues. Unreviewed/missing fields remain null, and
  no taxonomy rarity or athletic skill labels are fabricated.

## Start reviewing the actual first archive batch

**Do not change branches or open a second Klip writer while the current
archive pilot is still running.** The new code is a separate stacked PR.

```bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/clip-value-lab-v1

source .venv/bin/activate
python -m pip install -e '.[dev,semantic,images]'

export WS="$HOME/AdventureAI-clean.QJnopt"
klip --workspace "$WS" value-lab --batch 1
klip --workspace "$WS" studio
```

The local Studio prints a `http://127.0.0.1:PORT/` address. Click
**Value lab** in the header or visit the same local server's
`/value-lab` page. The video server verifies source identity on the
first playback in each server session. The source SSD must be connected.
The review packet itself is generated with **zero video decoding, hashing
or model inference**.

For the next frozen batch:

```bash
klip --workspace "$WS" value-lab --batch 2
# Then on the same local Studio: /value-lab?batch=2
```

Generate an evidence-aware rank report after collecting reviews:

```bash
klip --workspace "$WS" value-report --batch 1 \
  --output "$WS/reports/value-report-first-batch.json"
```

The report distinguishes reviewed vs unreviewed intervals, counts
ratings by dimension and tracks candidate vs random-control selection.
Only rated clips with necessary evidence can enter a review shortlist;
no clip is automatically published. Use a NEW output filename for each
saved report. `value-report` itself reads local JSON/SQLite only, with
**no video decode or inference**.

### Rubric: what makes footage worthwhile?

Keep distinct measures instead of collapsing all appeal into "sharp
looking footage" or assigning a fake calibrated model confidence.

| Criterion | Questions for a human reviewer |
|---|---|
| Event significance | Was a meaningful encounter or sporting action visible? |
| Subject visibility | Can the viewer follow the ray, animal, trail feature or skier? |
| Ambient clarity | How clear is the water, snow, light or environment? |
| Image quality | Is the camera well-framed, stable, exposed and sharp? |
| Narrative completeness | Are the approach, primary event and aftermath visible? |
| Action outcome | Can one tell what happened and how it concluded? |
| Visual novelty | Is this distinct from the user's existing archive? |
| Learning value | Does it suggest actionable improvements or useful insights? |
| Personal meaning | Does the clip matter to the person who recorded it? |
| Audience appeal | Would someone new to the outing enjoy it? |
| Watch-again value | Is it worth the user's time to revisit? |
| Investigation potential | Could another targeted pass uncover a missed highlight? |
| Uncertainty | Is vital evidence missing or genuinely unresolved? |

**Snorkeling counterexample:** A moderately murky reef scene with a
well-framed unusual ray can receive poor ambient clarity but excellent
event value, subject visibility, novelty and audience appeal. A nearly
invisible animal remains a promising investigation, not an automatic
share highlight. A broad `fish` or `marine_mammal` detector proposal is
not a verified species or rarity measure.

**Skiing counterexample:** A smooth technical run may exhibit low
camera motion but have excellent action/outcome, learning and narrative
scores. A shaky, very steep slope may be exciting yet provide little
evidence of a completed maneuver.

**Mountain biking counterexample:** An apparently routine flow segment
could show beautiful line selection, while a high-motion rock garden
might be too blurred or fragmented to explain technique.

### Reliability and privacy

- The packet is signed by its content hash and tied to the original
  frozen archive batch plan. Its review rows refer to that packet ID.
- Source videos are opened read-only via existing local Studio media
  streaming with token protection and server-side integrity checks.
- The review page never uploads video or embeds third-party providers.
- Human ratings may be revised, with all revisions preserved in SQLite.
- Missing scores stay unknown; random controls never mean no event.
- No source media, existing processing checkpoints, event evidence,
  archive plans or old human decisions are overwritten.
- Unknown/unresolved activity recordings remain reviewable, but they
  are not falsely forced into ski/bike/snorkeling cross-sport rankings.
- No statistical performance claims are made from the very small first
  batch; a disjoint evaluation set across trips, days and source
  recordings will be required.

## Next scientific milestones

1. After 20–30 real clip reviews, calculate criterion coverage,
   keep/skip consistency, differences by source selection method,
   and which dimensions explain pairwise preferences. Do NOT tune a
   model using an unrepresentative random control as a negative.
2. Add active clip comparisons that ask "which would you rewatch" versus
   "which would you share" separately, using the existing pairwise
   revision infrastructure.
3. Measure automated subject-local visibility and narrative coverage
   against human ratings. Detect scene/subject quality independently
   from ambient visibility.
4. Evaluate candidate recall using independent random probes and
   trip/session-held-out recordings before increasing OWLv2/VLM
   budgets for all 232 videos.
5. Introduce the canonical interval and model-cost ledger (Issue #41)
   and a scheduler that chooses additional analysis for **maximum
   marginal improvement per compute second**.

The present release improves review collection and ranking provenance.
It does NOT yet learn a calibrated clip-quality model, verify species,
estimate athletic biomechanics, identify all archive events, or
generate an automatically edited best-of reel.

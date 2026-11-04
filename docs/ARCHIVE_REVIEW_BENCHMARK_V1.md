# Archive Review v1: learn what makes a clip worth keeping

## First real evidence from the user's two archive batches

| Batch | Videos | Scheduled frames | Completed/cached jobs | Failed jobs | New frame results |
|---|---:|---:|---:|---:|---:|
| 1 | 8 | 118 | 6 cached | 2 | 0 |
| 2 | 8 | 113 | 7 completed | 1 | 112 |

The second 8-video run reported:
- 547.253 seconds for source SHA verification;
- 178.976 seconds for decoding;
- 0.417 seconds for histogram model inference;
- 9.488 seconds for final verification;
- one failed source, `199e180b1b6b7f820d3d269432f6d8dd84c8e1544b487c18564b62b4bbf84b0b`.

These recorded stage timings sum to 736.134 seconds. The run's elapsed
12m17s is close, but timing metrics are not universal denominators
for all wall-clock operations. **Source I/O and video decoding dominate
this particular cheap stage**. Spending more on OWLv2 or another model is
not the next optimization.

The histogram stage is not a sport-action or species detector. Its frames
are sparse measurements, not automatically accepted clips.

## New built-in CLI: seed a review benchmark from frozen batches

After the current `archive-pilot` command exits, switch branches:

```bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/archive-review-benchmark-v1
source .venv/bin/activate
python -m pip install -e '.[dev,semantic,images]'
export WS="$HOME/AdventureAI-clean.QJnopt"
```

Generate a **small candidate + control review set** without FFmpeg, source
hashing, video decoding, model inference, or changes to the original media:

```bash
klip --workspace "$WS" archive-review \\
  --campaign "$WS/reports/archive-pilot-v1" \\
  --max-assets 16 --window-seconds 14 \\
  --output-dir "$WS/reports/archive-review-first16-v1"

open "$WS/reports/archive-review-first16-v1/index.html"
```

This uses the existing immutable signed plans and per-asset cache statuses.
It selects up to two time intervals per source: an event-like
**candidate hypothesis**, if any, plus a separate **systematic control**
from the coverage floor. Very short files may contribute only one interval
when the windows would overlap.

The review HTML presents navigation assignments and local `file://`
source links. Browser security policies may prohibit opening local
file URIs; in that case, use the printed source basename with Finder.
Video playback is not embedded/generated and depends on the browser.
Do not share the report publicly because it contains local source paths.

The output folder contains:

- `assignments.json`: exact source IDs, timestamps, candidate/control
  role, inherited sport stratum, saved job coverage, sampled reasons.
- `review-template.json`: the same stable review IDs with all ratings
  **null** until a person watches the source.
- `index.html`: simple offline assignment cards and sport-specific
  questions.

The two classes are intentionally **not** treated as confirmed positive
and negative examples. A systematic control can contain a missed ray,
a difficult turn or another important event. That is precisely why we
review controls rather than trusting the candidate selector.

## Review criteria to calibrate on actual footage

Each numeric rating uses a human-anchored 0–1 scale: 0=absent/very poor,
0.5=moderate, 1=exceptional. Use `null` when not observable.

| Criterion | Example |
|---|---|
| eventSignificance | A notable animal encounter or completed athletic action |
| subjectVisibility | Can the viewer follow the animal or athlete action? |
| ambientVisibility | Global underwater clarity, weather or darkness |
| technicalExecution | Observable movement quality, not camera vibration |
| outcomeEvidence | Completed maneuver or full animal departure visible |
| narrativeCompleteness | Approach, central event and aftermath are present |
| visualQuality | Sharpness, exposure, framing, stability |
| archiveNovelty | Unusual compared with previous footage in this archive |
| behavioralNovelty | Distinct observed behavior, not a species rarity claim |
| learningValue | Useful for correcting performance, filming or observation |
| sharingValue | Understandable and compelling for another viewer |
| personalMeaning | Important to the person even if not audience-ready |

**Example edge cases**:
- A very clear underwater sandbar can score high on ambient visibility and
  visual quality, but low on event significance and behavioral novelty.
- A somewhat murky, clearly observable large ray may have high event
  significance, sharing and personal meaning while ambient visibility is
  lower. A ray-like visual is not a verified species or an animal count.
- A very murky scene with an unrecognizable animal-shaped shadow may be
  worth further investigation, but an unsupported organism or rarity label
  must not add bonus value.
- A smooth skiing turn sequence may be meaningful despite low motion
  intensity. Lift/scenic footage may be enjoyable without demonstrating
  athletic technique.
- A challenging mountain bike rock garden requires multiple frames showing
  approach, execution and exit. A static obstacle doesn't prove it was ridden.
- Poorly filmed moments can score high in learningValue even when
  sharingValue is low.

## Next engineering stages

1. **Playable review windows** within Klip Studio: the current HTML is
   only a read-only assignment gallery. Integrate with the existing
   video player and human-feedback revision store, preserving source
   timebase and consent for local-only media.
2. **Evidence-backed feature extraction**: local blur/contrast/visibility
   on relevant crops; camera/subject motion separation; track continuity;
   missing-frame masks; temporal approach/action/outcome. Model-derived
   values need evaluation and provenance, never automatic truth claims.
3. **Adaptive event investigation**: use the observed review labels to
   prioritize animal-like events, novel habitats, sustained ski turns,
   completed bike features and overlooked controls, while keeping a
   systematic exploration floor.
4. **True benchmark**: group train/test by trip or day; annotate both
   candidate and random/control intervals; compute event recall, precision@K,
   temporal IoU, sharing-vs-learning preference agreement and compute
   seconds/valuable clip. Keep no-event, uncertain, and unseen distinct.
5. **Cost-first optimization**: cache source-verification receipts only
   with sound file identity and mutation protections, reduce duplicate
   source reads, batch nearby seeks and keep successful frame checkpoints.
6. **Sport-aware ranking**: learn weights from real reviews; avoid
   calibrating entirely different sports on raw motion or unverified
   animal rarity. Maintain multiple share, learn and investigate queues.

Do not trigger a new whole-library OWLv2 scan or reclassify catalog labels
solely because the histogram campaign completed. The benchmark should
first reveal what meaningful moments the sparse stage misses.

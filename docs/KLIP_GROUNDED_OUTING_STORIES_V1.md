# Grounded outing stories v1

**Evidence level:** implemented; synthetic CI passed (real ffmpeg clips: sharp, black and
blurred; fake model replies for every harness path). **Not yet run on owner footage. No
claim yet about story accuracy or about whether story shortlists rank what the owner keeps.**

## What it is

An LLM writes each outing's story, moments and shortlist. A harness around every model call
keeps the result tied to the footage. Code: `src/adventure_intelligence/story_harness.py`,
`scripts/klip_outing_stories.py`. Output: `REPORTS/outing-stories-v1/` (new files only).

| Stage | Model | Checked by code |
|---|---|---|
| quality | none | Sharpness (Laplacian variance) relative to the same recording, so naturally soft underwater footage is not penalised against snow. Dark, blank/covered lens and blown-out frames. Shake from the motion timeline relative to the recording. The judges' shared facts `lens_obstructed` and `camera_steady`. Verdict: usable, degraded or unusable, with flags. |
| describe | VLM (default `qwen3-vl:32b`) | Schema; every observation at one of the frame times shown and inside the clip. |
| story | LLM (default `gemma3:27b`) | See the validator below. |

The validator rejects:
- a scene id that does not exist;
- beats out of clock order;
- an evidence reference that does not resolve (`fact:` must be a fact both judges answered
  yes; also `obs:`, `subject:`, `signal:`, `quality:`);
- a disputed fact stated as true;
- an animal, person or event (turtle, shark, jump, crash, lift, …) named without appearing
  in the cited scene's evidence;
- morning, afternoon or evening contradicting the capture clock (from the DJI file name);
- "first" or "last" for anything but the first or last reviewed scene;
- an unusable scene in the shortlist;
- a degraded scene shortlisted without citing its flaw;
- duplicates, or a shortlist over budget (30% of scenes, at least 3).

Violations go back to the model once. What still fails is removed and listed under
"Removed by the harness". Unusable scenes the model forgot are excluded by the harness.
Every record keeps the model, options, attempts, violations and the packet's SHA-256.

## Known limits (v1)

- Quality thresholds are uncalibrated. Calibrate them against grid keep/skip answers:
  - how many owner keeps would the gate have called unusable?
  - how many skips does it pass?
- Grounding is word-level. It catches a named animal or event with no evidence. It does not
  catch a wrong claim built from words that are in the evidence.
- "first"/"last" is strict and can remove a valid within-scene phrasing. The repair round
  usually fixes the wording. Removals are listed, never silent.
- The owner-examples hook (`build_packet(examples=…)`) is empty until it is wired to the
  ledger. When it is, a held-out trip may only see examples from training trips.
- Stories cover selected segments only. What the index missed is measured by the grid deck,
  not here.

## Evaluation plan

1. Owner reads each outing's `.md` and marks every moment accept / wrong / rewrite. This
   gives claim accuracy by type. Editing is not wired into the review app yet.
2. The story shortlist enters `evaluate` as another ranker, tested on held-out trips
   against the baseline ladder.
3. Call it automated when it beats the best baseline with an interval above zero and the
   owner's per-claim edit rate is low.

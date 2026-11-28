# Highlight-first funnel (v1)

**Status:** implemented; synthetic CI passed. Not yet run on owner footage, not owner-reviewed.

## Why

The calibration phase (judge panel → describe → stories) spread model time and owner time
evenly over a 25 % sample of every recording. The owner's claim review of the first stories
(101 claims, 2026-11-26: 56 true, 39 partly, 6 wrong) showed the descriptions are mostly
right but aimed at the wrong footage: lift lines, chairlifts and empty water were described
at length, while a stingray at 8:40 sat inside a minute-long segment. The funnel now starts
from what the owner keeps.

| Stage | What | Cost | Module / command |
|---|---|---|---|
| A. search | every stored 2 s frame of every recording, scored for the owner's targets | no model, no video | `highlight_search.py`, `klip_highlight_search.py search` |
| B. verify | one closed-question VLM call per candidate, dense frames at the peak | ~1 min per candidate | `highlight_verify.py`, `verify` |
| C. stories / reels | only on verified highlights | — | next: feed verified windows to `story_harness` and `highlight_compile` |
| D. owner review | verified moments + rejected sample + random audit, blind | the owner's time, on the best | `highlight_review.py`, `review`, `import-answers`, `eval-review` |

## The highlights library: where all of it lands

`highlight_library.py`, `scripts/klip_highlight_library.py`. One derived list of every moment the
owner judged worth keeping, rebuilt from the ledgers each time (never written back):
grid ticks and stars and tap preferences (`owner-ledgers/owner-pairs.jsonl`), gallery trims
(owner labels `boundary`), and highlights-page answers including 1-5 ratings (`highlight`).
Overlapping windows of one recording are one moment; tiers `owner_best` (starred / 5) >
`owner_keep` (ticked, trimmed ok, kept / 4) > `owner_maybe` > `to_review` (verified proposals
only). Skipped-only moments are excluded; kept-and-skipped ones are flagged as conflicts.
`page` lays the owner moments out by sport and outing on the highlights review page, each
playing just its window inside the existing review clip; ratings and trims import back and
the next build ranks by them. Every stage of the funnel is judged by what it adds here:
new owner-kept moments per review minute, outings with at least a few, and the audit miss rate.

## Stage A: search

* **Inputs (read only):** SigLIP2 frame embeddings every 2 s (`reports/evidence/<sha>/frames.npz`),
  the discovery pass's `motion.json` (2 fps pixel change) and `audio.json` (1 s loudness).
* **Own prompts:** `QUERIES` in `highlight_search.py`: several phrasings per concept: manta ray,
  eagle ray, stingray, shark, sea turtle, octopus, dolphin, eel; companion close to the camera;
  vivid reef and fish schools (few per recording); ski fast, powder, jump, trees, steep, skier
  ahead; lift, lift line, indoors; murky, blurry, dark. Their text embeddings are cached in
  `reports/highlight-search-v1/queries.*` (`encode-queries`); the evidence store's vocabulary
  is never rewritten. Changing a prompt changes `queriesSha256` and the search refuses stale
  embeddings.
* **Scores are relative.** A concept's logit is compared with that concept's distribution
  over the *development* frames of the same sport (median / MAD robust z) and must also pass
  a floor on SigLIP's own probability. Held-out recordings never set a baseline or threshold.
* **Animals:** hits closer than 4 s join; overlapping concepts merge (one ray scored as
  stingray and eagle ray is one encounter); window = first hit − 3 s … last hit + 2 s + 3 s;
  a ±3 s core around the peak for trims and dense frames.
* **Ski:** each 2 s step is lift / queue / indoors (SigLIP, z or probability, lift smoothed
  over 5 steps), stopped (motion under 25 % of the sport's median), or riding. Runs are riding
  stretches between lifts and lines, split by a stop longer than 30 s, at least 20 s of
  riding; each run records whether it followed a lift (started at the top) and ended at one.
  Moments inside runs score `0.5 speed + 0.35 visual + 0.15 skier-ahead`, where speed is the
  robust z of motion and wind loudness, visual the best ski-action concept; smoothed over 8 s,
  two per run at least 20 s apart, window −4 s … +6 s. A jump concept over threshold makes
  the moment a `ski_jump`. Whole runs are also candidates (`ski_run`) for top-to-bottom reels.
  The weights are a default until the owner ranks what makes a ski moment "best".
* **Audit:** a reproducible 5 % sample of 10 s windows the search did *not* propose (per
  sport; seed in settings) so misses can be measured.

## Stage B: verify

Closed questions, blind: the prompt is identical whatever the search suspected.
Marine: which animal (closed list, with visual definitions of manta / eagle / stingray), count,
the frames it is in, distance, clarity, whether a snorkeler is in frame. Ski: activity
(downhill / chairlift / standing or line / indoors), airborne + frames, terrain, speed
impression, skier ahead, clarity. Answers are checked (frames listed when an animal is named,
none for `no_animal`, …) and repaired once through `story_harness.call_json`. Verdicts:
verified, uncertain (poor clarity, unclear), rejected (no animal, lift, line). Clips are cut
once into `highlight-search-v1/clips/` from a covering review clip when one exists, else from
the original under `--trusted-source-root` with its hashed size checked. The run takes the
model lock and resumes.

## Stage D: owner review

One page, fixed shuffled order, no model output shown. Per clip: keep / maybe / skip, what it
shows (animal by name, companion, speed, jump, powder…; "lift or line", "bad footage"), best
part in/out at the paused time, a note. Native radio/checkbox inputs inside fieldsets with
legends, polite live regions for counts and marks. Answers import as owner labels (kind
`highlight`).

## Acceptance criteria (each measured on development days, then once on held-out)

1. **Search recall** of owner-confirmed animals in the earlier marine study ≥ 0.9
   (`eval --labels-from … --sweep`; operating point chosen on development only), with the
   candidates per snorkel hour reported. This recall is conditional on the earlier scout's
   selection; the audit sample estimates misses elsewhere.
2. **Audit miss rate**: owner keeps in audit windows → estimated missed keepable windows per
   unproposed hour (`eval-review`); target well under one per hour.
3. **Verifier precision**: owner keep-or-maybe rate of verified moments ≥ 0.7; kept share of
   the rejected sample ≤ 0.1 (otherwise stage B throws good footage away).
4. **Species accuracy** of verified animals against the owner's ticks ≥ 0.8; eagle ray vs
   stingray confusions reported separately.
5. **Ski:** owner keep-or-maybe of `ski_action` / `ski_jump` ≥ 0.6 and no lift or line clip
   kept as action (owner tick "lift or line" on a proposed ski moment counts as a failure).

## Commands

    R="$WS/reports"
    $PY scripts/klip_highlight_search.py encode-queries --reports "$R"
    $PY scripts/klip_highlight_search.py search --reports "$R" --roster "$ROSTER"
    $PY scripts/klip_highlight_search.py find-labels --roots "$WS" ~/Downloads
    $PY scripts/klip_highlight_search.py eval --reports "$R" --labels-from "$WS" ~/Downloads --sweep
    $PY scripts/klip_highlight_search.py verify --reports "$R" --trusted-source-root "$SRC" --dry-run
    caffeinate -i $PY scripts/klip_highlight_search.py verify --reports "$R" --trusted-source-root "$SRC"
    $PY scripts/klip_highlight_search.py review --reports "$R" --trusted-source-root "$SRC"
    $PY scripts/klip_highlight_search.py import-answers --reports "$R" --answers ~/Downloads/highlight-answers-<deck>.json
    $PY scripts/klip_highlight_search.py eval-review --reports "$R"

## From kept moments to finished clips (stage E)

`SRC` must be the footage drive (`/Volumes/Sid_DJI_SSD`), not the code checkout: every
recorded original lives under `/Volumes/Sid_DJI_SSD/DCIM/DJI_001`, so any other root refuses
every clip that needs the original. Pages now say why a clip is missing instead of "run the
verify stage first", and every review page is dark with keyboard controls
(k / m / s, 1-5, i / o for the best part, p to play it, space, arrows, j for next).

1. **Watch everything.** `klip_highlight_library.py page --trusted-source-root "$SRC"` cuts a
   960 px preview with 4 s of context on each side (`--pad-s`) for any moment no existing
   review clip covers, so you can set in and out points just outside the old window.
   `--top N` shows only the best N of each sport.
2. **Trim.** Your best part start and end replace the window (`windowBasis: owner_trim`) on
   the next `build`; a 5-star rating makes the moment `owner_best`. A single mark keeps its
   meaning: a start alone runs to the end of the moment, an end alone from its start.
   "Needs more before / after" widens the next preview on that side (`--more-pad-s`, 20 s).
   * **Purpose from the rating:** starred best or 4-5 stars is a `highlight`; kept and rated
     1-2 is a `memory` (kept and exported, never shown); unrated or 3 is `keep`.
   * **Sequences:** kept moments of one outing within 60 s of each other (on one recording, or
     across consecutive recordings by their file-name clocks) are linked: the page shows
     "part 2 of 3" in time order, `export --sequences` cuts each as one file (one span per
     recording, so the footage between parts is included, then joined), and
     `showcase --sequences` gives a sequence one card that still plays the trimmed highlight,
     with a "Full sequence" button for the parts together.
3. **Export.** `export` cuts each `owner_best` and `owner_keep` moment exactly to its window
   from the original, frame-accurate (re-encoded, not stream-copied):
   * `master.mp4`: original resolution, x264 CRF 18, AAC 192k: the copy to edit from;
   * `web.mp4`: at most 1920 px wide, CRF 23 capped at 8 Mb/s, `+faststart`, with all
     camera metadata removed (GPS, serials, telemetry and timecode streams);
   * `poster.jpg`: the middle frame, at most 1280 px.
   Stored in `highlight-library-v1/exports/<moment id>/` with `export.json` (source SHA,
   window, settings, output sizes and SHA-256). Idempotent; a new trim is a new moment id
   and a new folder, so earlier exports are never overwritten. Moments marked
   "bad footage" and unresolved keep-and-skip conflicts are left out unless asked for.
4. **Showcase.** `showcase` (highlights only by default; `--purpose` to widen) copies the best exported web files and posters into
   `highlight-library-v1/showcase/` (or `--out`): `media/`, `highlights.json`
   (title, sport, place, trip, date, tags, rating, duration, video and poster URLs) and a
   dark gallery `index.html`. Titles come from the roster's outing and trip titles and
   places. The JSON holds no source hashes, file names or private notes
   (`--include-notes` to opt in). `--base-url` sets where the site will serve media from.
   Nothing is uploaded or deployed.

Status: implemented, synthetic CI passed (real ffmpeg on synthetic video). Not yet run on
footage.

Commands:

    SRC=/Volumes/Sid_DJI_SSD; L="$R/owner-ledgers"
    $PY scripts/klip_highlight_library.py build --reports "$R" --ledgers "$L"
    $PY scripts/klip_highlight_library.py page --reports "$R" --trusted-source-root "$SRC" --top 15
    $PY scripts/klip_highlight_library.py import-answers --reports "$R" --answers ~/Downloads/highlight-answers-<deck>.json
    $PY scripts/klip_highlight_library.py build --reports "$R" --ledgers "$L"
    $PY scripts/klip_highlight_library.py export --reports "$R" --trusted-source-root "$SRC" --dry-run
    $PY scripts/klip_highlight_library.py export --reports "$R" --trusted-source-root "$SRC"
    $PY scripts/klip_highlight_library.py showcase --reports "$R" --ledgers "$L" --limit 12 --base-url /highlights/media

## Story harness changes from the same claim review (prompt revision 2)

* No time of day: the camera clock is not local time. Packets carry elapsed time from the
  outing's first scene; any morning / afternoon / evening / night word is a violation.
* No touching or holding claims, and nothing about the camera wearer's hands, feet or gear.
* The describer names animals as specifically as it can, with counts, or says "an
  unidentified animal".

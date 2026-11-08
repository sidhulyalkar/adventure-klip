# Adventure-Klip G1 source verification → sport draft reels

**Status:** local-first workflow. No claim that this specific user's 232
records all passed. This branch adds proxy-only, source-backed draft
reel plans. It does **not** analyze new source video or train a model.
Never present exploratory motion/control proposals as verified "best" moments.

## 0. Your frozen run

```bash
export WS="$HOME/AdventureAI-clean.QJnopt"
export CAMPAIGN_DIR="$WS/reports/full-highlight-ssd-20261106-184704"
export HEALTHY_SOURCE_ROOT="/Volumes/Sid_DJI_SSD/DCIM"
export SSD_IDENTITY_FILE="$WS/reports/ssd-identity-20261106-153749.json"
```

The latest shell text `Pass 27: 0 trusted-local sources remaining`
means **no pending eligible attempts**, not proof of 232 successful recordings.
The catalog total is 232 source SHA identities; the earlier SSD path
enumeration contained 234 MP4s, with two as-yet-unregistered paths.
These two are outside the frozen 232-source audit and must be reconciled
under a separate new campaign, *after* the current audit is closed.

## Incident recovery: completed scan, packet command exits without result

If validation shows 232 verified source receipts and zero failures but the
`PHASE=packet` process returns to a shell prompt without printing
`Open blind review:`, **do not repeat the source scan**. The prior
launcher redirected its actual exception into
`$CAMPAIGN_DIR/packet-errors.log`, so a later
`highlight-campaign-status --packet-dir ...` simply fails because the
packet's `selection-report.json` never materialized. Its redirected
`packet-validated-status.json` will then be an empty file, and parsing
that produces a second, misleading `JSONDecodeError`.

For the already executed attempt, inspect the packet error log **first**:

```bash
echo "PACKET_DIR=$PACKET_DIR"
if [[ -f "$CAMPAIGN_DIR/packet-errors.log" ]]; then
  tail -n 100 "$CAMPAIGN_DIR/packet-errors.log"
fi
find "$CAMPAIGN_DIR" -maxdepth 2 -type f \
  \( -name 'selection-report.json' -o -name 'presentation.json' \) -print
```

The updated launcher records each future packet attempt in
`packet-attempt-<timestamp>-<parent-pid>.json` and
`packet-attempt-<timestamp>-<parent-pid>.stderr.log`, shows the last 60
error lines immediately on failure, and refuses to claim success if the
frozen selection receipt is missing. It preserves a preexisting
incomplete packet folder; use a new unique `PACKET_DIR`, never delete
or overwrite historical receipts to bypass a failure.

A missing preview, a source SHA error, an `ffmpeg` timeout, a
workspace-safety block, or an exception during packet assembly may each
require a different recovery. The user's actual `packet-errors.log`
is needed to choose a safe retry. The 232-source scan checkpoint is
not invalidated by a packet-stage failure.

## 1. Validate actual source successes (workspace reports only)

```bash
klip --workspace "$WS" highlight-campaign-status \
  --audit "$CAMPAIGN_DIR/audit/campaign-audit.json" \
  --scan-dir "$CAMPAIGN_DIR/coarse-scan" \
  > "$CAMPAIGN_DIR/validated-status.json"

python - "$CAMPAIGN_DIR/validated-status.json" <<'PY'
import json,sys
p=json.load(open(sys.argv[1]))
s=p["scan"]
for k in ("completed","failed","blocked","reportIntegrityFailures",
          "interruptedOrUnattempted","completedDurationSeconds",
          "totalCatalogDurationSeconds","scanSourceCoveragePercentOfEntireCatalog",
          "scanDurationCoveragePercentOfEntireCatalog",
          "candidateWindowsProposed","coarseSampleMeasurements",
          "measuredSourcesWithTiming"):
    print(f"{k}: {s.get(k)}")
print("GO:",p["goNoGo"]["coarseScannedEveryCatalogSource"])
print("NEXT:",p["recommendedNextStep"])
PY
```

**Hard gate:** 232 completed, failed=blocked=reportIntegrityFailures=
interruptedOrUnattempted=0, catalog duration coverage=100%. Missing or
tampered source-receipts do not count. A 100% coarse scan is NOT evidence
of 100% event recall or timecode precision.

On the updated branch, independently check the release gate:

```bash
python scripts/klip_packet_readiness.py \
  --workspace "$WS" \
  --audit "$CAMPAIGN_DIR/audit/campaign-audit.json" \
  --scan-dir "$CAMPAIGN_DIR/coarse-scan"
```

If it fails, inspect source scan errors and retry only after source/drive
investigation. Do not turn failed/unknown media into negatives or blindly
restart hashing all sources.

## 2. Create frozen, playable blind-review packet (reads SSD explicitly)

**Run only after full source qualification** and only when the SSD is
securely connected and awake. The packet step may hash source files and
decode selected windows to new local H264 previews, in addition to the
earlier SHA-backed coarse scan. It never deletes originals.

If a packet was generated earlier against a partial scan, preserve it.
Use an unoccupied, new, immutable final packet path:

```bash
export PACKET_DIR="$CAMPAIGN_DIR/review-packet"
if [[ -e "$PACKET_DIR" ]]; then
  export PACKET_DIR="$CAMPAIGN_DIR/review-packet-final-$(date +%Y%m%d-%H%M%S)"
fi

PHASE=packet CAMPAIGN_DIR="$CAMPAIGN_DIR" \
  PACKET_DIR="$PACKET_DIR" \
  HEALTHY_SOURCE_ROOT="$HEALTHY_SOURCE_ROOT" \
  SSD_IDENTITY_FILE="$SSD_IDENTITY_FILE" \
  ENABLE_ORIGINAL_READS=YES ALLOW_MOUNTED_SSD=YES \
  PREPARE_MISSING_PREVIEWS=YES PACKET_LIMIT=120 \
  caffeinate -i bash scripts/klip_full_archive_highlights.sh
```

The packet includes `selection-report.json`,
`curation-manifest.json`, `presentation.json`, `media/*.mp4`
and `index.html`. If the packet command fails or produces missing
previews, check `$CAMPAIGN_DIR/packet-errors.log` and status counters;
do NOT claim the images were reviewed or prepared.

A 120-window packet prioritizes **review diversity**, not proven clip
quality. The current packet selector is independent of the newer
source-balanced coverage proposal (#88); it does not guarantee a fair
activity distribution or event recall.

## 3. Make immediate exploratory sport compilations (SSD NOT read)

From a **separate updated checkout** containing this workflow, after
the packet is ready:

```bash
python scripts/klip_campaign_reels.py \
  --workspace "$WS" \
  --campaign-dir "$CAMPAIGN_DIR" \
  --packet-dir "$PACKET_DIR" \
  --output-dir "$WS/reports/sport-reel-exploration-v1" \
  --mode exploratory --seconds-per-sport 150 \
  --max-clips-per-sport 8 --render
```

The tool fails closed unless the frozen source audit shows 100% verified
completion, the packet fingerprint matches the current scan checkpoint,
and packet MP4s rehash to expected SHA receipts.

Open `$WS/reports/sport-reel-exploration-v1/reel-review.html` for
a local filmstrip: per clip you get source SHA/time range, proposal
role, and an evidence-limited explanation of why it was sampled.

The tool also writes:
- `reel-plan.json`: source identity, timestamp bounds, sport,
  proposal role, human rationale if supplied, review status, selected
  shot order, conservative transition recipe and validation caveats.
- `ski-exploratory-draft.mp4`: silent 540p action cut montage.
- `bike-exploratory-draft.mp4`: silent 540p action cut montage.
- `snorkeling-exploratory-draft.mp4`: silent 540p underwater montage
  with short crossfades.
- `render-results.json`: derivative SHA/size/estimated run duration.

Absent sports remain explicitly empty, not backfilled with guesses.
These are **research previews**, not definitive "best moments" or
published final masters. Source timing in previews uses approximate
fast-seek and is not frame-exact.

## 4. Human-guided better moments (no source reads)

Open the frozen packet's `index.html` with a local browser, play
its verified H264 derivatives, annotate Keep/Skip/Neither/Cannot tell,
the selected personal goal, visible outcome only if supported,
technical and aesthetic quality, event readability, visible peak or
resolution, and a meaningful reason.

Use the review page's **Export JSON reviews**. It saves a file named
`klip-curation-reviews-<manifestsha>.json` in the browser's download
directory. Check its actual path, then:

```bash
export REVIEW_JSON="$HOME/Downloads/REPLACE_WITH_EXACT_EXPORTED_FILENAME.json"
python scripts/klip_campaign_reels.py \
  --workspace "$WS" --campaign-dir "$CAMPAIGN_DIR" \
  --packet-dir "$PACKET_DIR" \
  --output-dir "$WS/reports/sport-reel-reviewed-v1" \
  --mode reviewed --reviews "$REVIEW_JSON" \
  --seconds-per-sport 150 --max-clips-per-sport 8 --render
```

Reviewed mode **includes only explicit human Keep windows** from that
same frozen packet. It uses supplied human technical/aesthetic/event-
readability ratings as an editorial tie-breaker. An unreviewed or Skip
clip is not assumed to lack interesting events. Human written reasons
appear separately from coarse-scan motion evidence in the generated
review gallery and JSON ledger. The tool never fabricates athlete
actions, animal identity, viewer reactions or achievements.

A packet review is *goal-conditioned*, not a single global excitement
score. A wildlife nature encounter may rank above a jump under
`nature_encounter`, while the opposite holds for `athletic_skill`.

## 5. Cinematic release strategy (not implemented in this first preview PR)

Silent low-res previews and generic transitions cannot by themselves
produce an optimal professional edit. For quality final renders we need:

- **Timelines:** correct source decoded PTS, preview PTS and rendered-frame
  PTS alignment with manual trim correction, especially HEVC/VFR.
- **Event completeness:** preserve approach, main action, visible outcome,
  and enough recovery/response; don't cut an uncertain landing away.
- **Sport styles:** ski = strong approach and action cut on movement,
  bike = matched turns/terrain trajectory and rhythm, snorkeling =
  longer atmospheric shots and gentle dissolves where coherent.
- **Audio:** carefully mix original ambience with licensed or
  user-owned music when explicitly authorized; the current previews
  intentionally discard audio. No default copyright-encumbered track.
- **Color:** consistent white balance/exposure between shots, snow
  highlight protection, underwater cast correction without over-
  saturated wildlife and preservation of murky-but-rare encounters.
- **Pacing:** prioritize novelty, action arcs, shot clarity and change
  in visual energy; avoid repetitive head-bobbing despite high motion.
- **Narrative:** hook -> context -> escalation -> payoff -> resolution.
  For multiple trips, human sequencing or validated outing metadata
  should determine chronology, not SHA ordering.
- **Render:** export editable decision list (EDL), use original 4K source
  only in an explicit later media-read stage, normalize audio/color,
  apply frame-accurate trims, then QC each clip against original.
- **Evaluation:** blind comparison of action cuts, fixed context
  padding, change-point trims, and event-phase-aware trims under equal
  duration and selected-event budgets.

A later PR should incorporate PR #88's balanced candidate roster as an
option to the packet creator, enrich candidate intervals using
source-PTS-aligned video bursts, and use temporal representations +
human pairwise preferences to improve actual event recall and ranking.

## Safety

- This workflow writes only new directories under the Klip workspace.
- It never modifies, deletes, moves, uploads or hashes raw source media.
- Synthetic test fixtures include changed preview, bad manifest fingerprint,
  invalid human review, fake original path symlinks and tiny FFmpeg cuts/
  dissolves.
- Original private footage must not be committed or uploaded.
- Human Keep is not licensing, rights or automatic publishing approval.
- Do not bulk update/merge active experimental branches during a running
  media scan. PR #84 is a dependency, not yet on installed `main`.

# Klip: Overnight mixed-sport activity qualification (v1)

Your workspace currently has **232 indexed videos, all marked `unknown`**.
This is why `ecology-crops` returned `Exact indexed snorkeling asset required`.
DO NOT globally mark them as snorkeling and DO NOT treat blue pixels as proof
of underwater footage. Blue sky, snow-shadow, pools, beach, and bright surf are
major false positives. The `snorkeling` activity is a recording-level label:
the best solution for mixed-activity recordings will ultimately be temporal
activity segmentation, not a single label per video.

This opt-in pilot uses SigLIP2 image/text similarity over ~5 widely spaced
frames per video and independent prompt banks for underwater, skiing, biking
and other. Sampled marine-blue pixel fractions are **auxiliary report metadata
only**, never included in an auto-label decision. All output is local.
The HF model weights may be downloaded on explicit request; camera frames
are **not uploaded** or sent to an external API.

## Preflight, install, and launch tonight

Run from a terminal with the original external SSD connected:

```bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/activity-intelligence-pilot-v1
source .venv/bin/activate
python -m pip install -e '.[dev,semantic,images]'

export WS="$HOME/AdventureAI-clean.QJnopt"
test -f "$WS/catalog.sqlite3" || { echo "Catalog not found"; exit 1; }
test -f /Volumes/Sid_DJI_SSD/DCIM/DJI_001/DJI_20241220214252_0015_D.MP4 || \
  { echo "Original SSD is not mounted at the indexed path"; exit 1; }

mkdir -p "$WS/logs"
LOG="$WS/logs/overnight-activity-$(date +%Y%m%d-%H%M%S).log"

# This explicitly opts into at most 2 auto-labeled snorkeling proposals,
# and only after all 5 zero-shot frame votes agree with a strong margin.
# Labels are NOT manually validated. A SQLite catalog backup is created.
KLIP_APPLY_STRONG=1 caffeinate -is \
  bash scripts/overnight_activity_ecology_pilot.sh 2>&1 | tee "$LOG"
```

**Use AC power.** `caffeinate -is` prevents idle sleep for the command
(the `-s` flag only works on AC). Keep the SSD mounted and ensure enough
free disk space for cached model weights and outputs. A first SigLIP2 or
OWLv2 execution can download publicly released model weights and may be
slow on a first run; inference stays local. This pipeline hashes each
original **once for classification** (and selected promoted originals again
for apply and inference) for content identity. Hashing many gigabytes is
a deliberate integrity/IO cost. It will not finish in a guaranteed time.

**A fully read-only classification night:** omit `KLIP_APPLY_STRONG=1`
(or set it to `0`). That scans and writes a review gallery but never
modifies the catalog, and does not run marine object inference.

**Run only classification + label selection**, disabling the costly ecology
follow-up:

```bash
KLIP_APPLY_STRONG=1 KLIP_ECOLOGY_PILOT=0 caffeinate -is \
  bash scripts/overnight_activity_ecology_pilot.sh
```

## Ordered stages and reporting

1. Verify workspace and original mount path, inspect current activity counts.
2. `activity-scan`: for each video, use a completed checksum-verified
   SigLIP2 frame cache when model revision and preprocessing match. Otherwise
   source-verify and decode 5 timestamped frames across ~6–94% of the video.
   The model loads only once. Checkpoint records one by one to
   `$WS/reports/activity-overnight-v1/records.jsonl`, with per-record SHA.
   Resuming the same script skips completed records and retries failures.
   `review.html` shows one thumbnail per newly decoded recording and
   the vote/margin/status. `summary.json` audits failures and counts.
3. `activity-apply`: first preview; when explicitly opted in, promote at
   most TWO `unknown` recordings labeled as snorkeling by all five sampled
   frames and with median cosine-margin >=0.065, after rehashing sources
   and creating a consistent SQLite online backup under `reports/`.
   These thresholds are **not calibrated confidence values**. We cannot
   promise zero false positives, so manually inspect the resulting two
   recordings and correct any mistakes before publishing a species reel.
4. If no sufficiently convincing snorkeling proposals exist, exit safely;
   `unknown` is the scientifically appropriate outcome.
5. If candidates exist and ecology is enabled, create a new adaptive plan
   containing at most two snorkeling assets and at most 96 sampled frames
   per asset. Run the opt-in `ecology-owlv2` model and try PTS-verified
   high-detail crop packets, at most 3 events x 2 crops per asset.
6. The `ecology-crops-overnight-<12-hex-asset-id>/index.html` pages
   are **private visual review galleries**, not verified sightings.

## Review afterward

```bash
export WS="$HOME/AdventureAI-clean.QJnopt"
open "$WS/reports/activity-overnight-v1/review.html"
cat "$WS/reports/activity-overnight-v1/summary.json"
sqlite3 -header -column "$WS/catalog.sqlite3" \
 "SELECT activity,COUNT(*) FROM assets GROUP BY activity;"
find "$WS/reports" -name index.html -path '*ecology-crops-overnight*'
```

The audit and catalog backup are preserved under
`$WS/reports/activity-apply-*.json` and
`$WS/reports/catalog.before-activity-*.sqlite3`.

## Constraints and limitations

- No footage modifications and no automatic activity changes during scanning.
- New semantic sampling uses **actual source PTS** and requires source SHA-256.
- Prompts, gate versions and model revision are recorded. No probabilistic
  calibration or real DJI validation is claimed.
- A strong whole-video consensus can still hide an interesting short segment
  of another sport; future v2 should add temporal context segmentation.
- The present v1 crop path is for *snorkeling* only, by design.
- The existing histogram cache is not a semantic animal or sport classifier.
- Failure to detect strong snorkeling is not proof that snorkeling footage
  does not exist. Inspect report and manually label a known example.
- The classifier evaluates only a bounded sparse set, so it is NOT designed
  to guarantee recall for rare animals or brief activity transitions.
- Some short or zero-duration catalog rows will be skipped and reported
  rather than mistaken for a specific sport.
- Do not apply model proposals to the entire collection without reviewing
  a representative sample and measuring per-sport confusion rates.

## Next quality gates

Benchmark on manually labeled session-level groups (ski, biking, snorkeling,
surface-water confounders, snow/sky confounders, short mixed activities).
Report class-balanced macro F1, precision, recall, ECE after calibration,
abstention/coverage, and bytes-read/video to quantify the value of reuse.
The second pass should only inspect ambiguous segments selected by model
uncertainty; replacing one-label-per-video with segment-level proposals is
the natural next iteration.

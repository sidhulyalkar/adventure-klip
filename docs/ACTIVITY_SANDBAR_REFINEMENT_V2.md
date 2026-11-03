# Adventure Klip: Shallow-sandbar activity refinement and next-stage wildlife discovery

## Why a targeted second pass?

The first real run on 232 source videos produced 220 valid activity checkpoints,
12 frame-decoding failures, and 23 first-pass `unknown` proposals with 0/5
decisive votes. This is **not** evidence that those recordings lack snorkeling.
The uploaded gallery contains rays, bright pale sand and shallow underwater
scenes, alongside at least one clearly above-water kayak view. The original
zero-shot snorkeling prompts focused on coral and fish, so bright sandy seabeds
were comparatively underrepresented.

The first-pass 14 high-consensus snorkeling proposals had margins below the
0.065 **application** cutoff. This explains why an automatic `activity-apply`
attempt could correctly leave all 232 catalog activities as `unknown`.
That zero-shot margin is not a probability and should not be blindly lowered.

This v2 pass is intentionally a **review-only experiment**. It uses expanded
text descriptions for submerged sandbars, bright sand ripples, rays and
underwater reefs, while explicitly contrasting kayaking, above-water boats,
beach wading, ski terrain and biking. It never uses blue-pixel thresholds
to force a sport label.

It does not edit the earlier 220 records, the current catalog, or video bytes.

## Install on Mac

Only change branches when no older Klip run is actively holding the workspace
lock. The new branch stacks on PR #35 and includes the existing classifier,
ecology tracking and crop tools.

```bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/activity-sandbar-refinement-v2
source .venv/bin/activate
python -m pip install -e '.[dev,semantic,images]'
export WS="$HOME/AdventureAI-clean.QJnopt"
```

## Targeted second pass: unresolved clips and prior failures only

```bash
klip --workspace "$WS" activity-refine \
  --input-dir "$WS/reports/activity-overnight-v1" \
  --output-dir "$WS/reports/activity-sandbar-refinement-v2" \
  --mode sampled --frames 5 \
  --limit 40

open "$WS/reports/activity-sandbar-refinement-v2/review.html"
cat "$WS/reports/activity-sandbar-refinement-v2/summary.json"
```

The default selection is **23 first-pass unknown proposals plus 12
first-pass decode errors** in this run, up to 35 total candidates.
If first-pass failure clips remain un-decodable, those assets are reported as
failures, never given a false "other" activity. A partially decodable video
needs at least three successful frames for a review proposal.

A checkpoint file enables safe resumption in the same output directory. Each
new video is SHA-256 verified before sparse frame extraction; model weights are
loaded once, and image inference is batched. We also refuse to rescore with a
different SigLIP2 model revision or image preprocessing pipeline.

For a less expensive, lower-evidence diagnostic on the **same first-pass
unknown** files, use the cached single center thumbnails instead of decoding
source footage:

```bash
klip --workspace "$WS" activity-refine \
  --input-dir "$WS/reports/activity-overnight-v1" \
  --output-dir "$WS/reports/activity-sandbar-thumbnails-v2" \
  --mode thumbnail --exclude-failed

open "$WS/reports/activity-sandbar-thumbnails-v2/review.html"
```

One thumbnail **cannot** establish video-level activity consensus. The report
intentionally marks it as insufficient evidence, while showing the top
relative visual-scene vote to guide human inspection. It does not even
rehash the original videos: the first-pass thumbnail provenance is inherited.

To investigate additional marginal snorkeling candidates, use **a separate
new output directory** because the hypothesis and selection settings change:

```bash
klip --workspace "$WS" activity-refine \
  --input-dir "$WS/reports/activity-overnight-v1" \
  --output-dir "$WS/reports/activity-snorkeling-ambiguous-v2" \
  --mode sampled --frames 5 --limit 25 \
  --include-snorkeling-ambiguous --exclude-failed
```

## Confirm one real recording, then process exactly that one

Do not automatically promote the 23 new reports. Review full source footage,
including surface transitions. For a promising example, the first screenshot
included `DJI_20241227104236_0077_D.MP4`. Confirm it **visually** before
marking it snorkeling, since thumbnails alone are not sufficient.

```bash
VIDEO_NAME="DJI_20241227104236_0077_D.MP4"
VIDEO=$(sqlite3 "$WS/catalog.sqlite3" \
  "SELECT stored_path FROM assets WHERE stored_path LIKE '%/$VIDEO_NAME' LIMIT 1;")
ASSET=$(sqlite3 "$WS/catalog.sqlite3" \
  "SELECT id FROM assets WHERE stored_path LIKE '%/$VIDEO_NAME' LIMIT 1;")
test -n "$VIDEO" && test -n "$ASSET" || { echo "Source not found"; exit 1; }

open "$VIDEO"   # Verify the actual recording before proceeding

# First preview. No changes occur.
klip --workspace "$WS" activity-confirm \
  --asset "$ASSET" --activity snorkeling

# ONLY after you personally verified the source is snorkeling:
klip --workspace "$WS" activity-confirm \
  --asset "$ASSET" --activity snorkeling --reviewed --apply
```

The manual confirmation backs up the SQLite catalog and hashes the full source
before changing exactly one previously `unknown` label; it refuses to replace
another activity. The backup and audit are in `$WS/reports`. Source video
bytes are unchanged.

Use an **explicit asset ID** for the next adaptive plan rather than picking
the earliest snorkeling recording:

```bash
klip --workspace "$WS" adaptive-plan \
  --asset "$ASSET" --activity snorkeling --limit 1 \
  --coverage-seconds 40 --interest-seconds 2 --max-frames 64 \
  --output "$WS/exports/snorkeling-ray-pilot-v2.json"

caffeinate -i klip --workspace "$WS" adaptive-scenes \
  --plan "$WS/exports/snorkeling-ray-pilot-v2.json" \
  --backend ecology-owlv2 --decode-mode batched

klip --workspace "$WS" ecology-crops \
  --asset "$ASSET" --max-events 4 --crops-per-event 2 \
  --width 1536 \
  --output-dir "$WS/reports/ray-evidence-v2"

test -f "$WS/reports/ray-evidence-v2/index.html" && \
  open "$WS/reports/ray-evidence-v2/index.html"
```

The `ecology-crops` command returns an explicit abstention when cached
source-PTS-backed organism boxes are not available. Never assume the reef
or species has been identified from ecological regional priors alone.

## Pipeline architecture from here

1. **Session-aware classification:** cluster adjacent recordings by temporal
   continuity only as a review hint. A neighboring kayak or shore video
   must never inherit a snorkeling label automatically.
2. **Segment states:** treat `underwater`, `surface swimming`,
   `kayak`, `beach`, `ski lift`, `ski descent`, etc. as temporal
   segments. Whole-recording labels are only an organizing index.
3. **Closed-loop sampling:** feed cached `ecology-owlv2` object detections
   back into adaptive-plan, but never count duplicate provider sightings
   twice. Keep a minimum periodic coverage floor for rare animals.
4. **Biological review:** combine marine object crops with local, licensed
   reference image retrieval and taxonomic abstention, distinguishing
   visual appearance, organism identity and individual-animal tracking.
5. **Scientific benchmark:** annotate clips from both selected and random
   intervals. Measure snorkeling recall (especially bright sand),
   kayak/beach false positives, animal encounter recall, event diversity,
   decoded frames, bytes read and GPU seconds.

No full-library rescoring or catalog-wide automatic relabeling is warranted
until the targeted v2 results have been visually qualified.

# Klip V2: Moment, Edit Variant, and Category Reel

Status: first implemented offline, proxy-only vertical slice on top of the
SHA-qualified sport-reel campaign. The system does NOT yet train activity
classifiers, detect camera defects automatically, stabilize video, process native
HEVC masters or prove a human-optimal clip ranking. Source originals are never
opened, edited, deleted, uploaded, or published by this component.

## Design

Moment: source SHA, source-relative bounds, immutable proxy receipt,
provisional sport, multi-label owner categories, optionally an event core.

EditVariant: immersive / highlight / montage. Without owner-confirmed event
start AND end, no automatic tightening is performed. When available, keep
the full event core and short context, and trim only owner-marked noncore
leading/trailing quality defects. Interior blur/shake is never silently
removed. Source-relative proxy timing is approximate, not native PTS.

ReelRecipe: diversified selection with source caps, near-overlap prevention,
hard duration budgets, owner rating tie-breakers, no hidden ML claims.
Category-specific groups are empty unless explicit compatible tags exist.
All-video-best groups allow exploratory candidates but do NOT call them
approved highlights.

Render: optional 960x540 H.264 silent clips and reels from SHA-verified
local packet proxies. No music, network access, color change, or stabilization.
All outputs stay inside private workspace reports.

## Collections

- Ski: ski_park, ski_trees, ski_speed, ski_creative, ski_best
- Snorkel: snorkel_wildlife, snorkel_habitats, snorkel_scenic, snorkel_best, plus owner-labeled snorkel_region_* groups
- Riding: riding_general (one category for existing bike corpus)

A recording can have multiple labels. Unknown activity is not auto-routed.

## Get the code in a separate worktree

    cd "$HOME/Documents/Projects/adventure-klip"
    git fetch origin
    git worktree add ../adventure-klip-v2-edit origin/feat/klip-v2-moment-edit-collections
    cd ../adventure-klip-v2-edit
    source "$HOME/Documents/Projects/adventure-klip/.venv/bin/activate"
    export PYTHONPATH="$PWD/src"

## Run with a verified source-backed packet

Set paths to the actual existing qualified campaign and packet, not guessed ones:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export CAMPAIGN_DIR="$WS/reports/full-highlight-ssd-20261106-184704"
    find "$CAMPAIGN_DIR" -maxdepth 3 -name selection-report.json -print
    export PACKET_DIR="$CAMPAIGN_DIR/review-packet" # replace if packet elsewhere
    export OUT="$WS/reports/klip-v2-collection-pilot-$(date +%Y%m%d-%H%M%S)"
    python scripts/klip_build_collections_v2.py --workspace "$WS" \
      --campaign-dir "$CAMPAIGN_DIR" --packet-dir "$PACKET_DIR" \
      --output-dir "$OUT" --mode exploratory --seconds 150 \
      --max-clips 8 --render
    open "$OUT/review.html"
    open "$OUT/label.html"

This fails closed on incomplete archives, stale packets, mismatched source
identities or corrupted proxies; there is no surprise SSD rescan.

## Add human category, event and filming labels

The first run writes label.html and annotation-template.json. Open label.html in your Mac browser, play each verified preview, choose multi-label categories, mark event start/end, record defects and export JSON. The exported owner annotations can be passed with --annotations after moving them into workspace reports. If you prefer, edit the JSON records that you have
inspected. Each blank record already has the exact packet record ID, source SHA
and frozen manifest SHA. Other records can stay blank. Example values:

    "tags": ["ski_jump", "ski_park"],
    "eventStartMs": 15000,
    "eventEndMs": 22800,
    "qualitySpans": [
      {"kind": "shake", "startMs": 10000, "endMs": 13000}
    ],
    "regionLabel": null

All timestamps are absolute SOURCE-RELATIVE milliseconds, not seconds into
the packet clip. Snorkeling example tags: marine_wildlife, marine_ray,
marine_fish, marine_reef, marine_seagrass, marine_scenic,
marine_underwater_passage. Ski tags: ski_park, ski_jump, ski_box, ski_rail,
ski_trees, ski_speed, ski_creative, ski_moguls. Defects: waterline, shake,
blur, occlusion, water_drop, low_visibility, flat_traverse. Region labels
come only from the owner, not guessed from video.

Then create a new output directory and rerun with:

    export NEXT="$WS/reports/klip-v2-with-tags-$(date +%Y%m%d-%H%M%S)"
    python scripts/klip_build_collections_v2.py --workspace "$WS" \
      --campaign-dir "$CAMPAIGN_DIR" --packet-dir "$PACKET_DIR" \
      --output-dir "$NEXT" --annotations "$OUT/annotation-template.json" \
      --mode exploratory --render

To allow only human Keep clips, also supply --mode reviewed and --reviews
with the existing frozen blind-review export matching the same packet.
A Keep is NOT publishing authorization.

## Verification

    python -m pytest -q tests/test_moment_edit_v2.py
    python -m py_compile scripts/klip_build_collections_v2.py
    python scripts/klip_build_collections_v2.py --help

Private outputs never enter Git. No tests require DJI or private video.

## Next development gates

- Source-backed UI for category/event/defect owner annotation (no JSON editing)
- Automated ski/underwater scene and quality-span observation with human QA
- Native PTS mapping and source-quality render after source re-verification
- Blind A/B on actual clip choice and edit preference, held-out by trip

## Browser annotation workflow

- Open label.html and select ski, snorkeling, or other verified proxy clips.
- Mark event in/out on the local player, check categories, add quality
  defect in/out spans, and Save labels for this clip.
- Export JSON labels before closing the page. No labels are sent to a server
  or stored persistently without exporting.
- Copy that exported JSON file into a private workspace reports directory.
- Rerun the command with --annotations FULL_PRIVATE_PATH. The tool validates
  frozen packet SHA, record IDs, source SHAs, tag vocabulary and time bounds.
- Open review.html again, or inspect the rendered collection preview MP4s.

# Interactive Highlight Reel Studio v1

The Studio is an offline, private, playable sequence of verified local
preview clips with source-timed human reactions and exact cut markers.
It is not a claim that Klip has found the best sporting action across
the entire archive. Any source absent from the frozen curation pilot or
lacking a SHA-verified derivative remains unobserved.

## Three distinct goals

1. Play human-selected clips as continuous interactive reels, with nature,
   cinematic travel, personal memory, story and athletic categories.
   A selected window is a preference, not expert-adjudicated sporting success.
2. Separately scout unreviewed known-sport ski, bike, surf, skateboard,
   snowboard and soccer windows when verified previews already exist.
   These are SPORT SCOUTING, never automatically labeled as best clips.
3. Capture source-relative timestamps for reactions, technical critique,
   camera work, personal meaning, questions and improved trim boundaries.

## Run the first offline reels

From the feature branch with the package installed:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export PILOT="$WS/reports/highlight-curation-pilot60-v1"
    export FEEDBACK="$WS/reports/highlight-review-next-pass-v1/review-discovery-plan.json"

    klip --workspace "$WS" highlight-reel-studio \
      --pilot-dir "$PILOT" --feedback "$FEEDBACK" \
      --workers 2 --scout-limit 12 \
      --output-dir "$WS/reports/interactive-reels-v1"

    open "$WS/reports/interactive-reels-v1/index.html"

Use the actual pilot folder determined by matching the SHA in your
review export. The feedback JSON must refer to that exact frozen pilot.
The destination must be a NEW workspace reports/ or exports/ folder.

Only previously SHA-verified report-local preview derivatives are copied
into the standalone studio. Copies are independent (not hardlinks).
Each bounded local derivative is checked against its original receipt
digest. Independent local checksum/copy tasks use a bounded
ThreadPoolExecutor with --workers between 1 and 4 (default 2).
Result order in the manifest is deterministic. Bad or absent proxies
are explicitly excluded from playback, not replaced by another clip.

NO source DJI/SDXC media is opened, stat'd, resolved, SHA'd or decoded.
No cloud, Internet, model inference or training runs. For future
original source scans, deduplicate source identities and serialize the
verification pass with resumable checkpoints (#67); do not increase
parallelism on the SD reader that previously blocked in kernel read.

## Interface

- Reel navigation: "Your selected moments", "Nature encounters",
  "Cinematic travel", "Personal memories", "Athletic highlights",
  "Sport scouting". Empty collections remain intentionally empty.
- Native video playback with prev/next and auto-advance toggle.
  A single video element avoids loading an entire playlist of players.
- Click quick reaction tags, or type a comment and mark at the playhead.
- Mark [ in and ] out to create reviewer-proposed clip boundaries.
- Click any colored timecode pin to return to the marked moment.
- Keyboard: Space play/pause; N/P next/previous; Left/Right seek 5 s;
  M mark; bracket keys mark the selected interval.
- Prior human review notes remain hidden by default to limit anchoring;
  optionally reveal them.
- Browser-local draft saving is best effort. Export JSON for reliable
  persistence; importing must match the same frozen reel digest.
  No network requests, remote scripts, external uploads or deletes.

A "Skip" reaction is never a source deletion. User reactions are
subjective evidence and do not independently verify an athletic
achievement, wildlife species or landing.

## Import source-time reactions after watching

    klip --workspace "$WS" highlight-reel-feedback \
      --studio-dir "$WS/reports/interactive-reels-v1" \
      --annotations "$HOME/Downloads/klip-reel-reactions-EXACT.json" \
      --output-dir "$WS/reports/reel-feedback-v1"

The importer checks exact frozen reel SHA, unique markers, clip IDs,
reviewer pseudonyms, event source times and SHA-verified unchanged
local preview bytes. It emits:
- timed-feedback.json, containing source-timed human observations.
- retrim-proposals.json, containing only explicit positive-duration
  reviewer cut suggestions, without altering actual original media.

These annotations are research review evidence, not ground-truth
sports labels or preauthorization for ML training. Repeated review
disagreement stays visible.

## Current limits and follow-ups

- Interactive sequential playlists are working preview reels, NOT yet
  final production-grade full-resolution MP4 exports.
- The cached H.264 previews have bounded size and resolution, and
  do not include original audio.
- Automatic source recall, event boundary detection, cinematic regrade,
  species or sport adjudication, multi-view sync and training remain
  future distinct qualification phases.
- The user's pilot Keep decisions do not yet establish a top-athletic
  highlights collection. An empty athletic reel is an honest result;
  sport scouting exists to find and review potential candidates.
- When the DJI card is healthy and authorized, prioritize candidate
  context changes from the timed reactions using serial source hashing
  and PR #67 resume, then revisit the expanded event windows.
- Never treat public YouTube reaction references as licensed training
  footage without appropriate permission grants.
- Public repository tests use synthetic examples, never the user's
  private original videos, personal annotations or outings.

## CI acceptance criteria

- Exact provenance binds frozen pilot, feedback plan, preview SHA,
  playable clip and later annotation export.
- 1..4 bounded local preview copy workers; no original-video reads.
- Missing/tampered/symlinked media rejected and reported.
- Human-approved versus scouting-only clips remain separate.
- Timecoded human annotations never become verified source events.
- Browser JavaScript syntax checked when Node.js is available.


## Optional MP4 preview reel export

The interactive player is intended for iterative curation. When you
want a single shareable MP4 preview of one or more already-verified
collections, render only from the Studio's local copies:

    klip --workspace "$WS" highlight-reel-render \
      --studio-dir "$WS/reports/interactive-reels-v1" \
      --reel all --workers 2 \
      --output-dir "$WS/reports/reel-mp4-previews-v1"

The command exports a separate normalized MP4 for each nonempty
human-approved collection. FFmpeg tasks operate exclusively on
checksum-verified local derivatives; workers are capped at TWO
CPU transcoding jobs, each using two FFmpeg threads. Clips are
normalized to consistent 960x540 H.264 30fps/no-audio before
stream-copy concatenation. Missing or altered input clips cause
that specific reel to fail closed, not quietly omit an athlete
or encounter and pretend the result is complete. Empty
collections produce explicit empty status.

To render just one collection:

    klip --workspace "$WS" highlight-reel-render \
      --studio-dir "$WS/reports/interactive-reels-v1" \
      --reel nature --workers 1 \
      --output-dir "$WS/reports/nature-mp4-preview-v1"

An unreviewed Sport Scout MP4 is NOT included by default.
If specifically needed for manual review, request a separate
--reel sport-scout --allow-scout render. A scout montage is not
an approved athletic achievements reel.

The new MP4 outputs are private, preview quality, muted video
derivatives. Original 4K/60fps footage, original soundtrack,
camera-specific color grading, scene selection and licensed
music/sound are deliberately not included. Do not publish these
as full-resolution exports or infer that their action narratives
have been validated.


## Turn live reactions into the next candidate search

The timed-feedback importer also groups positive interest markers from
nearby moments in the same original source into bounded metadata-only
source search windows. In addition to timed-feedback.json and
retrim-proposals.json, it writes source-interest-proposals.json. Each
proposal includes original asset SHA, source time range, reviewer IDs,
trigger types, and explicit status human_reaction_requires_video_verification.
A "Wow" marker or standout comment is not an automatically accepted
highlight, and none of these proposals authorize reopening the SD card.
These source windows are the bridge to future resumable adaptive
temporal retrieval, not an automatic video classifier.

If a browser restricts local file:// video loading, a loopback-only
static server can provide the same offline files without Internet access:

    cd "$WS/reports/interactive-reels-v1"
    python3 -m http.server --bind 127.0.0.1 8765

Then open http://127.0.0.1:8765/ locally. Stop the server with Ctrl+C.
Do not bind this private clip directory to 0.0.0.0 or a publicly reachable
network interface.

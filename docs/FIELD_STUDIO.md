# Klip Field Studio: private local video review UI

A minimal, professional, browser-based studio for working with videos already indexed. The interface runs **only on 127.0.0.1** and never uploads video, transcripts, labels or thumbnails. Original DJI files are opened **read-only**, streamed by content SHA-256 identity, and remain in their original folders.

## Launch on your Mac

Finish any other Klip processing first, then:

~~~bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/local-review-studio

source .venv/bin/activate
python -m pip install -e '.[dev,vision,images]'

klip --workspace "$HOME/AdventureAI" studio
~~~

The CLI opens your default browser at a private loopback URL. Keep the Terminal running to use the studio; press **Ctrl+C** to stop. The '--no-browser' option prints the local URL. Use '--port 8765' to select a fixed loopback port.

The first time you play a video, Klip verifies its entire source SHA-256 before streaming. For 4K MP4s this can take some time. Subsequent seeks validate cached inode/size/mtime/ctime rather than rehashing. If the original video changes, playback is blocked for that session. Some DJI HEVC footage cannot play in every browser. Safari is a useful first test on macOS. Klip does not silently transcode or duplicate footage.

## Review

- Choose a recording from the left rail. Filter by activity.
- The player displays the original. A separate timeline shows suggested moments and *unverified* context/incident cues.
- Click a candidate, adjust **Start/End** in seconds, choose what happened, then choose **confirmed**, **rejected** or **uncertain**.
- Separately choose **keep**, **skip** or **maybe** for cinematic use. A confirmed fall may be important for a private review reel even if not appropriate for a public reel.
- Add a note and save. The database appends a revision rather than altering footage.
- Click an earlier review to revisit it. Saving a changed decision for exactly the same asset, interval and label appends another revision.

Keyboard shortcuts outside form fields: Space play/pause, left/right arrows jump 3 seconds, [ / ] set in/out, J / K previous/next proposal. Use the Help button for a reminder.

## Compare A/B

- Pick a recording and suggested interval for each side. The videos can belong to different assets, or be distinct ranges of the same video.
- Replay clips, choose A, B, tie or uncertain, add a reason and optional note.
- Save the preference as a versioned entry. Comparisons express human editorial preference, not model knowledge of athletic skill or safety.
- Re-reviewing the identical ordered clip pair retains all earlier judgments in the 'pairwise_revisions' table.

## What the studio does not yet do

- It doesn't run expensive semantic models on demand in the browser. Use the existing 'klip scenes' command to generate cached scene/embedding evidence first. The UI works without those features.
- It doesn't verify crashes, skiing biomechanics, individual wildlife species or every tree type. Camera motion and zero-shot context are provisional.
- Comparisons don't yet automatically fine-tune a trained model.
- Browser playback depends on codec support and PTS behavior; annotations are source-second-relative, not guaranteed frame-perfect.

## Architecture and security

- Python stdlib ThreadingHTTPServer binds exclusively to 127.0.0.1, with a random port by default.
- Cryptographically random per-session token for API and playback, strict Host and Origin checks, no CORS, restrictive CSP and limited JSON POST sizes.
- Indexed video access only by content SHA-256, with first-play full source verification and byte-range streaming for seeking. No arbitrary path input.
- No uploads, CDN dependencies, or arbitrary file-serving routes.
- All review writes occur in the separate workspace SQLite database. Source media is never opened for write.
- This tool is for a single trusted local user. Never expose it over a public proxy, network interface or port forward.
- The HTML/CSS/JS assets are packaged into wheel and source distributions; CI checks wheel contents and JavaScript syntax.

## Next milestones

Waveform displays from cached audio metrics, frame thumbnails cached only in the workspace, low-value scene heatmaps, smarter contrastive A/B review queues, and held-out evaluation of human pairwise rankings. Keep original media untouched.

See docs/REVIEW_DRIVEN_SCENE_ATLAS.md for the model evaluation and learned-exemplar pipeline.

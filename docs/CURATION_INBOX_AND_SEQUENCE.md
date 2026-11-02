# Klip v0.5: Curation Inbox + Sequence Cutting Room

Klip now separates three concerns: candidate discovery, deliberate human editorial decisions, and assembly of a reel. No single motion, audio, or scene score is allowed to delete footage or declare a clip boring with certainty.

## Local launch

```bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/visual-sequence-editor
source .venv/bin/activate
python -m pip install -e '.[dev,vision,images]'
klip --workspace "$HOME/AdventureAI" studio
```

Your last semantic batch (`processed: 1`, `frames_computed: 143`) is already cached. The new UI reads cached metadata. It does **not** re-run semantic inference on startup. If the model only generated embeddings without context predictions, low-action labels remain uncertain.

## 1. Inbox: reduce overwhelm

- The default Inbox shows at most three distinct, nonoverlapping review candidates per recording in six-recording pages. The user can filter skiing, biking, snorkeling and provisional priority.
- `Potential highlight` means a model proposed a contextually relevant scene, not that action recognition is verified. `Needs your eye` means evidence is insufficient, ambiguous, or involves a camera disturbance. `Likely filler` means uncalibrated scene-context signals overlap with transport, stationary or low-action footage.
- No footage is automatically removed, deleted or exported. Explicit confirmed `skip` labels trim future highlight proposals. Rejecting the scene *classification* instead keeps it visible for recategorization.
- Press `Preview` to inspect the original recording and edit in/out times and finer action labels. `Keep` is an explicit editorial approval for the selected source span.
- Press `Reject` to say whether the detector label was wrong or the scene was correctly observed but not worthwhile. A reason and explanatory comment are required. Incorrect labels become `rejected`/`neutral`, while unwanted valid footage is marked `confirmed`/`skip`.

## 2. Sequence: combine only approved moments

- The Clip Shelf contains explicitly human-kept intervals, even when the proposed action label remains uncertain. Non-confirmed labels are exported as `unverified_moment` rather than an asserted maneuver.
- Drag a shelf card into the Sequence Timeline or use `Add`. Move clips using drag-and-drop or accessible ↑/↓ buttons. Remove cards from the draft without modifying either the review bank or the source MP4.
- A preview player uses original read-only streams with per-segment time bounds. The `Play sequence` control is a browser preview, not a rendered video and not frame-perfect.
- Save a named draft. Each change creates an append-only `sequence_draft_revisions` entry and protects against lost updates from another tab.
- Review each adjacent join using `Review this join`, approve or reject it, and save comments on poor pacing, duplicate coverage, continuity, or narrative discontinuity.
- Export an approved-only v1 JSON edit plan into `~/AdventureAI/exports/sequences/`. This is compatible with the existing `klip render` pipeline for a subsequent, explicit offline FFmpeg render. **Exporting a plan does not render or publish anything.**

## 3. Classification feedback vs editorial feedback

A scene can be factually labeled correctly but editorially dull. A camera-rhythm candidate can be wrong about an actual ski turn yet still contain a wonderful landscape shot. The UI stores the two questions separately instead of collapsing them into one bad/boring score.

The rejection note is crucial evidence for the next training cycle. We keep exact original source intervals, human decisions, comments, revisions, and per-transition preferences. There is no automatic training on rejected guesses.

## Limits and safety

- Queue pages are intentionally small. Priorities are uncalibrated and not a proven best-of-all-videos global rank. Review across multiple trips is needed to calibrate them.
- Different semantic providers and clips without real scene-context evidence are never assumed comparable.
- Drag-and-drop is native HTML5, supplemented by click and ↑/↓ reordering. No cloud libraries or dependencies.
- Only explicitly human-kept clips appear in the edit bank, regardless of label-certainty. If you overturn a Keep decision, export will fail until you remove the revoked clip from the draft.
- Drafts are global to this local workspace for now; future work can add multiple named sequences, custom playlists, and saved search views.
- The original DJI video bytes stay in place, unchanged. All edits and user feedback are in `catalog.sqlite3`, while plans are saved to workspace exports.
- Browser playback is limited by DJI codec compatibility; try Safari on macOS. No proxies or thumbnails are generated automatically.

## Evaluation and next milestones

Review the first 20–30 candidate clips across different activity/filming sessions. Measure how often Klip surfaces an actual worthwhile highlight within the first small batch (precision@k), the number of missed good moments, the rate of repeated or ambiguous candidates, review time per useful clip, and user preference for assembled sequences. Only then promote automatic ranking/exclusion strategies.

Additional future improvements: scene filmstrip thumbnails generated in the *workspace* from sparse source PTS timestamps, within-video waveform and optical flow overlays, duplicate visual cluster navigation, per-trip prioritization, transition compatibility suggestions, robust undo/redo UI, and a verified trip-group holdout for preference ranker training.
## 0.6 UI integration and palette update

- Adopt one navy / light-grey / salmon / white theme for the focused screening room, virtual media Library and approved-only Sequence editor.
- Preserve access to the prior detailed analysis and A/B comparison dashboard via the optional local `/classic` route, without cluttering the default UI.
- Verify focused view routes, keyboard-review controls, packaged assets, activity correction metadata and source-file immutability. No media reprocessing required for UI changes.

## 0.6.0 · Focus Studio and virtual Library

- Replace busy multi-card inbox as the default interface with a video-dominant, one-clip-at-a-time screening room.
- Keep, Skip with a note, or mark a wrong detection; hide secondary annotation controls until needed.
- Add filesystem-like virtual organization by filming date, activity, region, original source folder, and collection with search and playback.
- Store user-confirmed activity, region and place metadata outside original source videos. Display provenance when a date is estimated from a DJI filename.
- Preserve human-reviewed examples and existing approved-only reel sequences, with no source video writes.
- Check bundled focused UI scripts, date-provenance behavior, local-only metadata API and immutable source bytes in CI.

## 0.5.0 · Editorial Inbox and Sequence Cutting Room

- Paged, non-overlapping candidate review across recordings, with provisional low-value and promising lanes; no automatic deletion or unreviewed content rejection.
- Keep/reject quick review with required explanation for skipped or incorrectly classified scenes; re-reviewable revisions remain in the local catalog.
- Source-safe drag-and-drop reel board restricted to human-approved clips, keyboard-accessible order controls and timed source preview.
- Per-join approval or rejection with reasons, revisioned draft edits, stale-write protection, and validated render-compatible JSON plan export.
- Tests and CI check for source immutability, review semantics, queue deduplication, HTTP authorization, packaged static assets and both browser scripts.

## 0.4.0 · Klip Field Studio

- Introduce a professional, responsive, local-only review UI with video playback, time-aligned event timelines, selection, annotation, evidence inspection, A/B comparisons, and shortcuts.
- Serve only indexed, source-hash-verified videos over loopback HTTP with tokenized byte-range requests; preserve original footage.
- Append pairwise preference revisions to SQLite, with no automatic publication.
- Package HTML/CSS/JS and test serving, security, source immutability, HTTP ranges, reviews, A/B writes, wheel contents, and JS syntax.

# Adventure Klip changes

## 0.3.3

- Capture original decoded frame PTS/time base separately from requested seek timestamps via FFmpeg showinfo and copyts.
- Add conservative same-label mutual-IoU object track hypotheses with gap and ambiguity gating.
- Expose timestamp evidence, object `trackId`, and unverified track summaries in completed semantic artifacts and timelines.
- Invalidate semantic caches for the timestamp extraction and tracker revision; add real FFmpeg PTS regression tests.

# Changelog

## 0.3.2
- Preserve immutable event snapshots and review decisions when events change or disappear.
- Identical event updates retain approvals; changed revisions require fresh approval.
- Add `klip history ASSET_ID` for local audit inspection.
- Add per-frame semantic checkpoints, checksum validation, and active configuration selection.
- Add opt-in semantic processing to `klip process`; retain unreviewed prediction status.
- Publication excludes unavailable or changed source assets.

## 0.3.1
- Added folder-scoped `klip process` with restartable baseline analysis and timelines.
- Verify source content before cache hits/rendering; recover matching duplicate paths.
- Added checksum-verified extraction artifacts and persistent running/failed states.
- Reuse extraction across detector-only updates and retry damaged cache artifacts.
- Fixed semantic timeline attachment and enforced known source duration bounds.
- See `docs/PROCESSING.md` for migration cost, single-writer semantics and limitations.

## 0.3.0 (October 2026)
- Renamed distribution and primary CLI to `adventure-klip` / `klip` (`adventure` remains available).
- Added read-only `klip index` for stable folders without moving original videos.
- Added normalized, evidence-aware temporal `klip timeline` JSON.
- Added sparse `klip scenes` descriptor families: histogram, opt-in SigLIP2, OWLv2, and optional MediaPipe human pose.
- Added `klip plan --style chronological|crescendo` storyboarding.
- Added tested FFmpeg fade-through-black video/audio rendering.
- Retained verified DJI ingestion, motion evidence, manually validated sequences, and approved WorldLink publication.
- 25 tests passing in local Python 3.13 environment with FFmpeg.

## 0.2.0
- Optical-flow evidence proposals, session candidate grouping and detector versioning.

## 0.1.0
- Local-first catalog, verified import, event annotations, episode rules, reel planning and rendering.

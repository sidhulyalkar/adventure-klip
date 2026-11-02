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

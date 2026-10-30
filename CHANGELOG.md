# Changelog

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

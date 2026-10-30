> Version 0.3 adds the zero-copy index, semantic sampling, normalized timelines, and storyboarding. See [v0.3 notes](KLIP_V03.md).

# Architecture and evolution

## v0.2 pipeline

```
DJI card -> non-destructive hash-verified import -> content-addressed originals
                                          -> ffprobe technical metadata
                                          -> low-res frame differences (+ optional optical flow)
                                          -> candidate visual_activity events
                                          -> event graph / temporal grouping
                                          -> recipe-based draft reel -> FFmpeg MP4
                                          -> reviewed WorldLink bindings -> public manifest
```

**Evidence is not interpretation.** The `flow` detector creates `sustained_camera_motion` and `rhythmic_camera_sweep` proposals. It uses sampled FFmpeg grayscale frames and median dense optical flow with a simple directional coherence check. A camera pan may masquerade as an athlete turn; water, snow and foreground motion can confound optical flow. Repeated sweeps are therefore *camera rhythm candidates*, not verified carving. Flow sampling at 4 fps cannot resolve the precise phases of brief high-speed jumps. Deterministic manual labeling and external semantic plugins remain authoritative for athletic or animal interpretation.

Usage:

```bash
pip install -e '.[dev,vision]'
adventure analyze --detector flow
adventure plan --recipe motion-review --output ~/AdventureAI/exports/motion-review.json
adventure sessions --gap-minutes 15
```

Session candidates use only timezone-aware capture timestamps, validated durations, and matching labeled activities. They are **not** a guarantee of the same camera clock, continuous ski run or real-world route. Cards without reliable capture timestamps are not silently merged.

**Evidence is not interpretation.** The baseline `visual_activity` detector responds to large image changes (camera movement, animals, cuts, waves, and blur). It does not reliably classify jumps, turns, animals or exact places. Semantic activities enter today via manually reviewed `labels` JSON; an independent detector plugin interface is provided for future models. Annotation and actual audio/IMU/GPS analysis are not included in v0.2.

## Core identity decisions

- Asset key: full SHA-256 bytes; original basename is never a unique identifier.
- Re-import skip: previous path, size, mtime_ns and quick first/last hash, after a previous verified full import. This is a practical cache check, not cryptographic equality proof for a modified file.
- Source paths can recur on reused SD cards; a changed signature forces new import.
- Events reference one immutable asset and millisecond timestamps.
- Episodes reference events and can overlap, preserving simultaneous interpretations.
- `recording_summary` is the source recording's whole duration, not evidence of a complete athletic run.
- Manual event replacement revokes that asset's previous world bindings to avoid stale approvals.
- Publishing is separately gated on a manual event, explicit user approval, and independently supplied HTTPS media URLs.

## Detection levels and planned research

| Representation | Implemented | Next research milestones |
|---|---|---|
| Source asset | Hash, probe, preserved metadata | Clock skew, split camera recordings, sidecar-specific GPS/IMU parsing |
| Observation | 2fps grayscale frame change, optional 4fps dense flow | Audio onset/energy, embeddings, IMU, global-motion compensation and horizon tracking |
| Atomic event | Generic motion windows, rhythmic camera sweep proposals, human labels | Ski jump lifecycle, athlete movement validation, tracked animal behavior |
| Episode | Jump chain, carving flow, tree weave, feature chain, sustained activity, shared subject | Learned temporal segmentation and long-range contextual boundaries |
| Full run/session | Single recording summary and tentative timestamp-based cross-file sessions | Cross-asset verified continuity graphs, clock and scene checks |
| Trip | Not implemented | Time/place grouping with location uncertainty |
| Reel | Greedy non-overlapping sequence ranking and render | Diversity constraints, beat alignment, multi-asset continuity and editable EDL |
| WorldLink | Manual approved bindings, privacy-safe manifest | Taxonomy-aware candidate matching, confidence calibration, rich overlays |

## Detector plugin contract

A downstream package can register a detector through:

```toml
[project.entry-points."adventure_intelligence.detectors"]
my-detector = "example_provider:MyDetector"
```

`MyDetector` must expose `name`, `version`, and `detect(asset_id, series, metadata) -> list[dict]`. Each result includes `id`, `kind`, `start_ms`, `end_ms`, `confidence`, `score`, and optional `evidence`. Tests must include false-positive negatives and temporal boundaries. Version changes invalidate the event-analysis cache.

For higher-rate algorithms, future plugins should be supplied richer inputs through a decoupled observation-provider protocol. The current plugin uses the lightweight 2fps motion series and must not be advertised as adequate for biomechanical estimation.

## Performance boundaries

- Copy and verify read the destination twice: once on write, once after. For a new import this is intentional integrity protection. Run sources from fast external media when possible.
- The FFmpeg rawvideo analysis is bounded in RAM but still decodes source video. Reuse of camera-produced `.LRF` proxies is planned, requiring proof of frame/timestamp alignment.
- SQLite is a single-writer catalog; POSIX lock prevents simultaneous CLI writers. Detector-stage cache keys include feature version, detector name and model version. Jobs are resumable at whole-asset boundaries. Per-event transactional job graphs are a future iteration.
- No claims about human movement or species should be published from motion evidence alone.
- Website deployment never needs to read the original camera media directly. Raw footage is never committed to Git.

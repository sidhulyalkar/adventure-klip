# v0.3 implementation notes

## Comparison with general sports highlight makers

Tools such as Klap emphasize upload-to-short automation, saliency/virality scoring, auto-reframing, captions and platform export. Klip is built for large local action-camera archives and *interpretability of event evidence*: multiple hierarchical event levels, timestamped signals, verified original references, scenic continuity, low-cost cached inference, separate wildlife identity review, and world-entity links. No superiority claim is made without a paired evaluation.

## Timeline schema

The measured bin includes visual-motion raw mean, robust normalized value, sample count, and an explicit disclaimer. `importance.heuristic` is local in that video, not a globally comparable probability. `events` are references to detector or human-generated candidates. `objects` and `keypoints` retain model outputs and confidence where available. Embeddings are referenced as a frame index in versioned semantic sidecars.

Downstream temporal modeling should use a typed tensor with observation masks:
`[visual_flow, residual_flow, blur, audio_energy, scene_embedding, object_presence, track_motion, human_pose_visibility, camera_orientation]`, plus timestamps, confidence/missingness, and per-channel version. No zero-filled value should implicitly imply an observation.

## Model training path

1. Create *trip-disjoint* validation and holdout sets. Record camera mount, sport, weather, lighting, slope/habitat, video format, and known equipment.
2. Align visual frame features to source PTS, not only FFmpeg fixed-frame approximations.
3. Train lightweight segment classifiers and sequence models (temporal convolution / transformer / SSM) on observed evidence with separate calibrated confidence heads.
4. Preserve null labels when the action cannot be inferred from POV alone. Do not label head pans as carving or blurred silhouettes as verified species.
5. Evaluate event boundary IoU, per-class precision-recall, macro average precision, long-run retention, species taxonomic accuracy and reel preference with human blind comparisons.
6. Optimize continuity and editorial constraints across whole runs, not just independent event quality. Compare hard cuts, black fades and real cross-dissolves on source material.

## Privacy and reproducibility

Never silently upload footage. Preserve source hashes, source paths, model version, prompt/class vocab, sampling cadence and annotation provenance. Publication requires explicit review, valid browser sources and a user-controlled choice of real-world location disclosure.

# Review history and resumable semantic analysis

## Run on a permanent DJI folder

```sh
python -m pip install -e '.[dev,vision,images]'
klip --workspace ~/AdventureAI process ~/Movies/DJI --activity ski \
  --semantic-backend histogram --every 4
klip --workspace ~/AdventureAI history ASSET_ID
```

The histogram backend is a deterministic color descriptor, not an object detector.
Install `[semantic]` and explicitly choose `siglip2`, `owlv2`, or `stack` to use
the existing optional model backends. They can download model weights and have
not been qualified on real DJI footage. The standalone `klip scenes` command uses
the same checkpoint implementation and returns failure status when inference fails.
Normal indexing, motion analysis and browsing require no semantic model download.

## Review lifecycle

The current `events` and `bindings` tables remain the active view for compatibility.
`event_revisions` stores content-addressed snapshots of event payloads, and
`review_history` records explicit decisions plus invalidation reasons against those
snapshots. These records are append-only through application APIs.

- Identical event replacement preserves its current bindings and approval.
- Changing evidence, timing, confidence, score, label, origin version, or removing
  an event archives its bindings and removes them from the active publication view.
- A later restoration of the old payload does not resurrect its old approvals.
- New detector runs do not overwrite manual labels. Event-ID collisions across
  assets or origins fail before replacement.
- Snapshot creation, binding invalidation and event replacement share one database
  transaction; invalid changes roll back together.
- Existing catalogs migrate additively. Legacy current events/reviews are captured
  on their next edit or explicit review. History already deleted by older versions
  cannot be reconstructed. Manual IDs currently depend on label ordering, so
  reordering labels may require renewed approval.

`klip history` is a local inspection command. Its snapshots and review records are
not included in public exports. Export also requires a source whose content matches
the catalog identity; missing or changed sources are reported without private paths.
HTTPS URL syntax is validated, but remote availability is not probed.

## Semantic lifecycle

Each configuration includes the provider/model version, available resolved model
revision, provider settings, sampling interval, maximum frame count, image width,
sampler version, and installed inference-library versions. Pose configurations
include the local model file SHA-256. Built-in provider code changes must bump its
version; a null resolved model revision is not a claim of pinned model weights.
Model providers currently initialize before cache lookup, so a cached model run may
still incur model-loading time.

Per-frame payloads and checksums are committed in `semantic_frames`. A running or
failed job can reuse valid completed frames; a corrupt checkpoint is recomputed.
After source revalidation, a completed JSON artifact is installed atomically and
its checksum recorded. If that artifact is missing or corrupt, valid frame
checkpoints rebuild it without repeating inference. Source changes detected during
extraction invalidate the affected checkpoints.

`semantic_active` selects the most recently requested configuration per asset and
provider. Timelines attach only its verified, completed artifact. A failed newer
configuration does not silently fall back to an older one. Independent providers
can coexist; their references are retained in `semanticRefs`. Legacy unregistered
JSON files are preserved on disk but ignored by timelines until recomputed.

Semantic JSON includes content identity, model/configuration provenance, embedding
dimension and norm, and sampled timestamps. It omits private source paths. Model
objects/keypoints are always marked unreviewed; scores are not calibrated species
probabilities. Current sample times are **requested seek times**, not decoded frame
presentation timestamps, and are labeled accordingly. The pipeline still needs
PTS-aware frame extraction, temporal tracking, retrieval evaluation and a real-data
qualification before action/species accuracy claims are appropriate.

Work remains single-writer under the CLI workspace lock. Full source verification
adds disk I/O. The frame count defaults to at most 300 per asset; this is bounded
uniform sampling, not adaptive event-driven sampling.

## Verification

Local verification: `python -m pytest -q` — **69 passed in 3.67 seconds**.
Compilation, sdist/wheel builds and an isolated wheel install with both CLI help
commands passed. CI now installs the images extra to exercise the real histogram
integration. Suite runtime is not an inference-throughput benchmark.

Regression coverage includes review preservation/revocation, legacy bindings,
transaction rollback, source mutation, interrupted and failed inference, configuration
invalidation, corrupt checkpoints/artifacts, untrusted prediction status, and active
configuration selection. A generated FFmpeg video runs end-to-end through the real
histogram provider and timeline attachment, then verifies cache reuse.

These tests establish engineering behavior. They do not measure the accuracy of
SigLIP2, OWLv2 or MediaPipe, and no user footage or macOS hardware was tested.

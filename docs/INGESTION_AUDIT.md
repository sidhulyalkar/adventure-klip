# Ingestion safety audit and first correction

Audited foundation: `f078af8`, PR #1, v0.3.0. PR #1 remains open;
its GitHub CI run 40995892236 completed successfully.

## Corrected behavior

An asset previously registered with `klip index` could be counted as an
imported duplicate while its catalog path still pointed outside managed storage.
The repeated-source shortcut could also skip the copy altogether. Removing the
source then made that supposedly imported asset unavailable.

`klip ingest` now installs a verified copy under the workspace's `originals`
directory and updates the catalog reference, including when the same bytes were
indexed under another filename. Repeat-import skips require full SHA-256 checks
of both source and managed destination. This deliberately costs sequential I/O;
edge fingerprints alone cannot establish byte identity. Existing corrupt managed
destinations produce an error and remain untouched. Repeat imports also retry
sidecars, allowing late metadata to be copied.

No schema migration or new runtime dependency is required. Sources remain
untouched. This does not implement a persistent job scheduler or change indexing.

## Verification

Baseline in the implementation environment: 24 passed, 1 skipped (optional
optical-flow dependency unavailable). FFmpeg integration tests ran.
New regressions cover same-path and different-path index-to-import conversion,
source deletion after import, late sidecars, corrupt managed destinations, and
middle-byte mutations with preserved size and mtime.
After correction: 29 passed, 1 skipped in 3.86 seconds. This suite runtime is
not a throughput benchmark for camera footage.

Run: `python -m pytest -q`.

## Remaining priorities

1. Indexing still uses edge signatures for skips and can leave old asset records
   pointing to paths whose bytes have changed. Introduce verified source-location
   records and require identity validation at analysis/render boundaries.
2. Analysis caches currently trust completion rows without verifying artifacts
   or source identity. Add dependency fingerprints, atomic artifacts, persistent
   running/failed/completed states and interruption recovery.
3. Add `klip process` only once source identity and per-stage recovery are sound.
4. Separate heuristic strength from calibrated confidence; enforce exact source
   timestamp bounds and audit reruns of events referenced by publication bindings.
5. Qualify optional inference, card removal, power behavior and performance on
   real DJI recordings and macOS hardware. Synthetic tests do not establish
   action-recognition quality or production readiness.

## Local use

After installing this branch with `python -m pip install -e '.[dev]'`:

```sh
klip --help
klip ingest --help
python -m pytest -q
```

Use the existing `ingest` command for a verified removable-card copy; `index`
retains external source references and is not a replacement for importing a card.

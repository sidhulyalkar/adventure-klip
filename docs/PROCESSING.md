# Incremental local folder processing

Install the package, then point it at a permanent folder without moving footage:

```sh
python -m pip install -e '.[dev]'
klip --workspace ~/AdventureAI process ~/Movies/DJI --activity ski
```

The command recursively indexes that folder, analyzes its successfully indexed
content identities, and writes timelines under `~/AdventureAI/timelines`.
It does not process unrelated catalog assets, create reels, or publish footage.
Semantic inference is opt-in through `--semantic-backend`; see
[review history and semantic checkpoints](REVIEW_AND_SEMANTICS.md). Use `--detector flow` with the optional `[vision]` extra for
the existing optical-flow baseline. External SSD paths under `/Volumes` require
`--allow-removable`; removable camera cards should use `klip ingest` instead.

Rerun the same command after adding videos or after interruption. Exit status 0
means the requested stages completed; 1 means some files/stages failed; 2 means
invalid input or an operational error. JSON reports include per-file failures and
`extracted`, `features_cached`, `analyzed`, and `cached` counts. A corrupt file
does not prevent healthy files from completing.

## Identity and cache guarantees

- SHA-256 identifies content. Filename, size, mtime and edge fingerprints are not
  treated as proof of identity. Indexing detects middle-byte edits even when size
  and mtime are preserved.
- Analysis checks source identity before accepting cached results. If a primary
  path is missing or changed, another cataloged path is usable only after its full
  hash matches. Rendering checks identity before decoding and again before output.
- Extraction artifacts have deterministic names, content checksums and an additive
  SQLite `artifacts` table. Missing or modified artifacts trigger extraction.
- Extraction completes through an atomic file replacement, then a committed cache
  record. Analysis records `running` before work and `completed` only after the
  event transaction commits. Failed detection can reuse previously saved features.
- Interrupted `running` work is retried by the next serialized CLI run. All process
  work uses the existing POSIX workspace lock. This is a single-writer pipeline,
  not a distributed lease/worker queue. Low-level index/analyze calls require the
  caller to hold that lock when other writers may be active.
- Built-in extraction versions are independent of detector and episode versions.
  Detector-only changes reuse features; custom extractors conservatively include
  the custom detector version in their extraction dependency. Editing preferences
  do not invalidate extraction.
- Timeline generation consumes the artifact referenced by a completed analysis,
  rather than picking an arbitrary feature file. Known source duration is preserved;
  out-of-range observations are excluded. Semantic frame attachment no longer
  references an undefined duration variable.

## Compatibility, cost and limits

The first analysis after upgrading recomputes legacy features once because old
files lack trustworthy checksum records. Original media, manual labels and old
feature files are not removed. No new runtime package dependency is required.
Version strings must be bumped whenever extraction or detector semantics change.

Full verification intentionally reads the source bytes even on cache hits. New
extraction also verifies the source after decoding. This prioritizes correctness
over disk throughput; no DJI/M1 Max throughput claim has been established.
No snapshot is taken of externally writable media: concurrent writes are detected
by hashes and filesystem metadata where observable, but this is not protection
against an adversary changing and restoring bytes during decoding.

Timelines are currently regenerated cheaply on each run. Semantic inference now
has per-frame checkpoints and active configuration selection; event revision and
review history are preserved (see REVIEW_AND_SEMANTICS.md). Power-aware scheduling,
general dependency graphs and interactive review still remain. Existing files are
not automatically deleted when a later attempt fails; use the current result/status,
not the presence of an older timeline file, as evidence of successful processing.

## Validation

Implementation verification: **46 tests passed in 5.02 seconds** with optional
OpenCV installed, including the optical-flow integration. Python compilation,
sdist/wheel builds, and isolated wheel installation with `klip process --help`
also passed. Suite runtime is not a real-footage throughput benchmark.

`python -m pytest -q` includes real FFmpeg folder processing, cache reuse and corrupt
media exit-status checks, plus controlled interruption, source mutation, duplicate
fallback, artifact corruption, detector-version invalidation and semantic attachment
regressions. These validate engineering behavior, not athletic action accuracy.
No real user footage, camera hardware or trained semantic model was evaluated here.

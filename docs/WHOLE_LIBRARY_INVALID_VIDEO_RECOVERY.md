# Whole-DJI scan: invalid-media recovery and visibility

This branch addresses the failure pattern observed in the full-drive run on October 31, 2026. The old scan included `._DJI_...MP4` metadata forks and registered some assets with no positive duration. Later stages repeatedly failed with `A positive probed duration is required`, sometimes after wasteful full-source integrity checks.

## Changes

- Ignore `._*` AppleDouble sidecar files and files inside `__MACOSX` during initial video discovery. This affects only candidate enumeration, not source file contents.
- Reject newly indexed videos that cannot be probed or have nonpositive duration. Record a per-file error rather than writing another invalid catalog row.
- Gracefully skip existing zero-duration legacy records before source hashing/model inference in semantic and acoustic passes, with a count of skipped records. The baseline motion pass reports invalid duration before attempting source verification.
- Emit explicit source-verification progress during semantic processing so silent long reads can be distinguished from compute-bound frame extraction.
- Retain full content SHA-256 source integrity checks on valid files. No shortcuts that mistake file size/mtime for cryptographic identity.

## Existing process

These changes do not alter a Python process already running against the old installed code. If the log has remained unchanged for hours, stop that process gracefully with Ctrl+C, or `kill -INT <pid>` if the controlling shell is no longer available. Avoid SIGKILL unless the process is truly unresponsive and you understand recovery risks.

Once the writer has stopped, inspect the catalog with a read-only SQLite query:

```sh
sqlite3 -readonly "$HOME/AdventureAI/catalog.sqlite3" \
  "SELECT COUNT(*) AS assets, COUNT(*) FILTER (WHERE duration IS NULL OR duration <= 0) AS invalid_duration FROM assets;"
```

Old invalid rows are deliberately **not deleted automatically**, because asset IDs and review relationships may refer to them. The new enumerator does not place fake AppleDouble files into the next `process` asset set, and the stage-level guards skip older invalid records when called directly.

## Safe resume (once the old writer is fully stopped)

```sh
cd "$HOME/Documents/Projects/adventure-klip"
git status --short
git fetch origin
git switch --track origin/fix/whole-library-invalid-video-fast-skip
source .venv/bin/activate
python -m pip install -e '.[dev,vision,images]'
klip --workspace "$HOME/AdventureAI" process \
  "/Volumes/Sid_DJI_SSD" --activity unknown --allow-removable \
  --detector baseline --semantic-backend histogram --every 15 --audio-evidence \
  --log-file "$HOME/AdventureAI/logs/dji-library-resume.log"
```

Do not start a second writer on the same workspace or reinstall while the existing scan runs. A resumed scan reuses completed feature/model checkpoints but still cryptographically verifies indexed source files, so resuming may not be instantaneous.

## Out of scope

- True FFmpeg multi-frame persistent decode (the current histogram sampler may launch many short decodes).
- Reusing cryptographic hashes without re-reading sources. We won't weaken immutable source integrity to gain speed.
- Repairing unprobeable source files or inferring frame duration from filenames.
- Automatically removing old catalog records or moving files on the DJI SSD.

Next benchmark: count decoding subprocesses, stage time and time spent on SHA-256 per video, compare frame coverage, and validate quality/recall before changing sampling cadence.
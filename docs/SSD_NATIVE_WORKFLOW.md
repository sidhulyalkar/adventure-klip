# Adventure-Klip: zero-copy processing from an external SSD (macOS)

## Policy

**One canonical raw-video library on the external SSD. No raw media copies are
required on the Mac.** The Mac workspace owns SQLite, SigLIP/other embeddings,
temporary proxies, checkpoints, review packets and reports. A disconnection
pauses original-source work but does not erase completed artifact receipts.
Nothing automatically edits, moves or deletes camera originals.

This workflow is **not** approval to use a failing SDXC card. Always identify
the actual volume. `diskutil info` cannot certify device health, and a
matching volume UUID does not substitute for a full source-file SHA.

## Before unplugging or reconnecting

1. Stop Klip, FFmpeg and other applications actively accessing the SSD.
2. In Finder, **Eject** the intended SSD; do not force eject or remove a
   mounted disk during an active source scan.
3. Reconnect and confirm that it appears with the expected name.
4. Inspect its macOS-reported identity and mount path (examples only):

```bash
diskutil list external
ls -ld /Volumes/*
# Replace with the *actual* volume directory discovered above.
diskutil info "/Volumes/ActionSSD"
```

A healthy mount and successful directory listing **do not prove** a healthy
filesystem or reliable USB connection. If macOS reports I/O errors or the
volume repeatedly freezes, **stop source processing** and investigate the
cable, enclosure, filesystem and backups. Do not keep retrying a suspect drive.

## Recommended locations

- Raw source: `/Volumes/<your-SSD>/<raw-footage-folder>/...` (read only)
- Klip workspace: `$HOME/AdventureAI-clean.QJnopt` (internal Mac storage)
- Outputs: `$WS/reports`, `$WS/features`, `$WS/exports`
- Optional exported finished reels: back to the external SSD when wanted

Unlike `klip ingest`, **`klip index` is zero-copy**: it hashes the original
file, stores its SHA and path in the SQLite catalog, and never installs a second
managed copy. Both the indexer and scanner can be authorized to read an
otherwise healthy `/Volumes` SSD.

## First connect and enrollment

From the Git checkout on branch
`fix/full-archive-source-root-preflight-v1` (until merged):

```bash
export WS="$HOME/AdventureAI-clean.QJnopt"

# Replace the *entire* value with the actual existing root folder on your SSD.
export HEALTHY_SOURCE_ROOT="/Volumes/ActionSSD/Raw"
test -d "$HEALTHY_SOURCE_ROOT" || {
  echo "No footage root is mounted; stop." >&2
  exit 3
}

# Choose a new immutable local identity receipt for THIS SSD.
export SSD_IDENTITY_FILE="$WS/reports/ssd-identity-$(date +%Y%m%d-%H%M%S).json"
python scripts/klip_ssd_mount_guard.py enroll \
  --workspace "$WS" --root "$HEALTHY_SOURCE_ROOT" \
  --identity "$SSD_IDENTITY_FILE"

# Verify the same volume before source reads (path and UUID must agree).
python scripts/klip_ssd_mount_guard.py verify \
  --workspace "$WS" --root "$HEALTHY_SOURCE_ROOT" \
  --identity "$SSD_IDENTITY_FILE"

# This only inventories names and sizes under the authorized mounted directory.
python scripts/klip_source_root_preflight.py --workspace "$WS" \
  --trusted-root "$HEALTHY_SOURCE_ROOT" --allow-mounted-ssd
```

If `VolumeUUID` / `DiskUUID` is unavailable, the guard fails closed.
Investigate the filesystem identity; do not replace it with a volume-name
comparison.

## Shortcut when the original SSD mount path is already catalogued

If `klip index --dry-run` discovered hundreds of recordings on the intended
SSD, **do not immediately rehash them all**. Existing catalog SHA identities
may already refer to those exact source paths, and a new scan will independently
reverify each content hash before accepting its evidence.

Run the new metadata-only reconciliation first. This enumerates directory
entries but does not open video contents:

```bash
export SSD_RECONCILE_REPORT="$WS/reports/ssd-reconcile-$(date +%Y%m%d-%H%M%S).json"

python scripts/klip_source_root_preflight.py --workspace "$WS" \
  --trusted-root "$HEALTHY_SOURCE_ROOT" --allow-mounted-ssd \
  --output "$SSD_RECONCILE_REPORT"

python - "$SSD_RECONCILE_REPORT" <<'PY'
import json, sys
r=json.load(open(sys.argv[1]))
for key in ("catalogAssetCount", "localVideoFilesEnumerated",
            "catalogPathsInsideTrustedRoot",
            "catalogSamePathSameSizeUnverified",
            "catalogSamePathDifferentSize", "catalogPathNotEnumerated",
            "localPathsNotInCatalogAtSamePath"):
    print(key, r[key])
print("Unlisted local paths sample:", *r["localPathsNotInCatalogSample"], sep="\n  ")
PY
```

If there are catalog paths matching the root and file size, a two-file
SHA-verifying scan can start **without a full reindex**. This does not yet
prove all other source files are healthy; each candidate must pass its own
hash/ffprobe/decode later. Paths not in the catalog (at the same absolute path)
are **not necessarily new unique videos**. Investigate them later with
content-addressed indexing, never assume the difference in counts equals new
recordings.

If the existing catalog is entirely outside the selected root or files
have moved, index the stable SSD first, then freeze a **new** audit. Existing
per-source receipts must never be silently relinked to a different path.

## Catalog synchronization (zero-copy)

Existing catalog paths may already point to the same correct SSD mount. If
they still do, a *new campaign audit* followed by an authorized scan may work
without reindexing. If they are stale, renamed or target a different mount path,
verify the volume and explicitly reindex its footage:

```bash
klip --workspace "$WS" index "$HEALTHY_SOURCE_ROOT" --allow-removable --dry-run
klip --workspace "$WS" index "$HEALTHY_SOURCE_ROOT" --allow-removable
```

`--allow-removable` permits **read-only SHA indexing**, not copying. Index
can take time because each file is read to prove identity. Check the returned
`errors`; a file not successfully read remains unverified. The indexer's
current CLI does not itself accept the SSD identity receipt; verify the volume
immediately before running it. If the SSD mount name changed, do **not**
reuse a previous frozen campaign audit.

## Controlled original-source processing

Freeze an audit **after** the catalog matches the mounted SSD:

```bash
export CAMPAIGN_DIR="$WS/reports/full-highlight-ssd-$(date +%Y%m%d-%H%M%S)"
PHASE=audit CAMPAIGN_DIR="$CAMPAIGN_DIR" \
  bash scripts/klip_full_archive_highlights.sh

# Two recordings first, one FFmpeg decode worker.
PHASE=scan CAMPAIGN_DIR="$CAMPAIGN_DIR" \
  HEALTHY_SOURCE_ROOT="$HEALTHY_SOURCE_ROOT" \
  SSD_IDENTITY_FILE="$SSD_IDENTITY_FILE" \
  ENABLE_ORIGINAL_READS=YES ALLOW_MOUNTED_SSD=YES \
  SCAN_ASSETS_PER_PASS=2 SCAN_PASSES=1 SCAN_FPS=1 \
  caffeinate -i bash scripts/klip_full_archive_highlights.sh

cat "$CAMPAIGN_DIR/progress.json"
```

The launcher now verifies the selected volume UUID, selected relative footage
path and exact catalog path before every scan pass. If the disk is absent,
remounted under another path, or replaced by a different disk, processing stops
rather than accepting the wrong source. Each successful source scan records
SHA verification and an immutable per-source report. No separate original
files are copied to the workspace.

After inspecting the two-file pilot:

```bash
PHASE=scan CAMPAIGN_DIR="$CAMPAIGN_DIR" \
  HEALTHY_SOURCE_ROOT="$HEALTHY_SOURCE_ROOT" \
  SSD_IDENTITY_FILE="$SSD_IDENTITY_FILE" \
  ENABLE_ORIGINAL_READS=YES ALLOW_MOUNTED_SSD=YES \
  SCAN_ASSETS_PER_PASS=8 SCAN_PASSES=40 SCAN_FPS=1 \
  caffeinate -i bash scripts/klip_full_archive_highlights.sh

# Build a blind review packet; explicit preview generation reads selected
# originals, but only creates *derived* compressed clips in the workspace.
PHASE=packet CAMPAIGN_DIR="$CAMPAIGN_DIR" \
  HEALTHY_SOURCE_ROOT="$HEALTHY_SOURCE_ROOT" \
  SSD_IDENTITY_FILE="$SSD_IDENTITY_FILE" \
  ENABLE_ORIGINAL_READS=YES ALLOW_MOUNTED_SSD=YES \
  PREPARE_MISSING_PREVIEWS=YES PACKET_LIMIT=120 \
  caffeinate -i bash scripts/klip_full_archive_highlights.sh
```

If the drive briefly disconnects during one source, that source may be marked
failed. Check drive health and UUID/path continuity first; only then run a
bounded explicit recovery pass with `RETRY_FAILED_SOURCES=YES`. Never use this
flag to hammer a freezing disk. Existing successful source reports are reused
by the scan's `--resume` receipts.

## Acceptance gates for SSD-native archive processing

1. The SSD identity guard accepts only the selected UUID and current
   canonical source root; unplugged and swapped-drive cases fail closed.
2. Zero-copy indexing preserves original path and content SHA; no original
   installation under `$WS/originals` is required.
3. Two-file pilot: known source IDs, SHA matched, ffprobe accepted, decodes
   completed, report receipts valid, zero original modifications.
4. A deliberate mid-run disconnect must **never** mark a missing source as
   low-interest footage. Partial results stay incomplete, and failures are
   recoverable only after a new healthy-volume check.
5. The full-archive status denominator remains all 232 *catalogued* sources.
   Only successfully SHA-verified sources and scanned durations count as
   measured coverage. Source changes require reindex plus a new frozen audit.
6. Source-level temporal qualifications and subjective clip review remain
   separate from coarse scene-motion proposals.

## Current limits and engineering follow-ups

- Identity checks occur **between** scan batches and before starting packet
  processing, not on each FFmpeg frame. An unplug during a kernel-blocked read
  may still hang a worker until the OS reports the error.
- The current source hash loop is blocking; add a supervised process with
  per-file timeouts, elapsed-time/bytes heartbeat and safe cancellation before
  claiming robust behavior under pathological drive failures.
- Do not infer SSD health from the word `SolidState` or the mount UUID.
  Actual failure triage requires independent system/device health information.
- Introduce verified cache lifecycle limits, batch throughput telemetry and
  a durable source-volume-to-catalog binding once the direct-SSD pilot works.
- SSD-native does not remove the need for a **second independent backup** of
  irreplaceable originals. A single SSD is not a backup.

## Test

```bash
python -m pytest -q tests/test_source_root_preflight.py \
  tests/test_ssd_mount_guard.py tests/test_full_archive_campaign.py
bash -n scripts/klip_full_archive_highlights.sh
```

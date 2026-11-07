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

After verifying an unlisted file really belongs to this SSD, the index CLI
also accepts that **single existing video** directly. No source video is
copied and adjacent files are not hashed:

```bash
# Replace with an actual file from localPathsNotInCatalogSample.
VIDEO="/Volumes/YourHealthySSD/DCIM/DJI_001/NEW.MP4"
python scripts/klip_ssd_mount_guard.py verify \
  --workspace "$WS" --root "$HEALTHY_SOURCE_ROOT" \
  --identity "$SSD_IDENTITY_FILE"
klip --workspace "$WS" index "$VIDEO" --allow-removable --dry-run
klip --workspace "$WS" index "$VIDEO" --allow-removable
```

This verifies a content SHA for exactly one selected file; check the returned
result/error before assuming it was registered. New catalog registration or
remapping requires a **new frozen campaign audit**, not mutation of an existing
campaign receipt. The selected file can be a duplicate under a new name; the
SHA, not the filename, determines identity.

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

## Accidental interruption and early review packet recovery

If a scan terminal is killed, **do not create a new campaign or reindex**.
Restart `PHASE=scan` with the same immutable `CAMPAIGN_DIR`,
`HEALTHY_SOURCE_ROOT`, `SSD_IDENTITY_FILE`, and `SCAN_FPS`.
The scanner preserves its SHA-backed completed per-source receipts, and
the launcher now continues scan-pass diagnostic file numbering across
separate shell invocations.

If a `PHASE=packet PREPARE_MISSING_PREVIEWS=YES` command was invoked
before the source campaign finished, its exit may be visible only in
`$CAMPAIGN_DIR/packet-errors.log`. Check locally without opening raw
videos:

```bash
tail -n 30 "$CAMPAIGN_DIR/packet-errors.log"
test -f "$CAMPAIGN_DIR/review-packet/selection-report.json" && \
  echo "A frozen packet exists (possibly partial/stale)"
```

**Do not delete an early packet**: it may be an immutable receipt of a
valid smaller set of videos. It is *not* a final-archive review packet
when the source-scan fingerprint later changes.

The standard `PHASE=packet` launcher now performs a **metadata-only
full-coverage readiness gate**. It refuses to build a default packet
unless all sources in the frozen campaign have passed SHA-backed scans,
with no failures, blocks or report-integrity discrepancies:

```bash
python scripts/klip_packet_readiness.py --workspace "$WS" \
  --audit "$CAMPAIGN_DIR/audit/campaign-audit.json" \
  --scan-dir "$CAMPAIGN_DIR/coarse-scan"
```

When the full scan is complete, and an early packet directory already
exists, supply a new, immutable destination without mutating the old:

```bash
PHASE=packet CAMPAIGN_DIR="$CAMPAIGN_DIR" \
  PACKET_DIR="$CAMPAIGN_DIR/review-packet-final-v2" \
  HEALTHY_SOURCE_ROOT="$HEALTHY_SOURCE_ROOT" \
  SSD_IDENTITY_FILE="$SSD_IDENTITY_FILE" \
  ALLOW_MOUNTED_SSD=YES ENABLE_ORIGINAL_READS=YES \
  PREPARE_MISSING_PREVIEWS=YES PACKET_LIMIT=120 \
  caffeinate -i bash scripts/klip_full_archive_highlights.sh
```

This may SHA and decode selected camera intervals to create missing
previews. It must **not** run concurrently with a source scan. If a
source remains permanently failed, obtain an explicit separate exception
or create a partial research packet in a separately qualified workflow,
never assert full-archive success.


## Read-only live source-scan ETA (Nov 7, 2026)

The full-archive source runner now prints an `[klip-eta]` line on a
resume before scanning new assets, and after **each pass**, including the
first pass after enough timings exist. It estimates remaining original
SHA verification and low-resolution video decoding using measured
per-source rates from already completed, receipt-verified runs, plus
catalogued sizes/durations for sources not yet attempted. If fewer
than 3 timed sources exist, it displays `ETA unavailable`, not an
invented completion time.

A separate terminal can poll without opening or statting SSD originals:

```bash
python scripts/klip_campaign_eta.py --campaign-dir "$CAMPAIGN_DIR"
python scripts/klip_campaign_eta.py --campaign-dir "$CAMPAIGN_DIR" --json
```

Example **format only**, not measured real throughput:

```text
[klip-eta] completed 3/5 (60.0%) | attempted 3/5 (60.0%) | pending 2 | failed 0, blocked 0, bad receipts 0 | remaining ~0h 01m (heuristic 0h 01m to 0h 01m) | projected local finish 2026-11-07T12:01+00:00
```

The center ETA uses median measured SHA bytes-per-wall-second and
decoder video-seconds-per-wall-second. A heuristic fast/slow range uses
25th/75th percentiles of measured per-video speeds, **not** a calibrated
confidence interval. It will shift with thermal throttling, source
bitrate/resolution, or SSD throughput and does **not** account for an
ongoing uncheckpointed source or future repair of failed recordings.

This is **time to exhaust the remaining automatic scan attempts**, not
time to achieve 100% verified source coverage. Failed/blocked source
counts and invalid receipts remain visible, separately. The tool
never performs source video I/O, model inference, new hashing or catalog
mutations. An ETA-formatting failure prints a warning but will not
interrupt the scanner.

While your Mac has a scan process active, do not `git pull` or change
the live checkout: the already running shell cannot inherit these new
logging lines safely. Apply the update after the active scan process
exits. Wi-Fi switching does not affect the local scanner as long as
the SSD remains mounted, the laptop stays awake, and all analysis tools
are local. `caffeinate -i` only suppresses idle sleep, not lid-close
sleep, and a matching drive UUID does not establish hardware health.

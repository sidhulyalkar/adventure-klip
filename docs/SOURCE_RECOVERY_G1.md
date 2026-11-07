# G1: Safe source-root recovery for full-archive highlights

This runbook applies when the full-archive audit has catalogued assets but the
scan reports **zero eligible trusted-local sources**. A catalog path is not proof
of present video availability. Do not retry a known stalled SDXC mount.

## Guarantees and boundaries

- `scripts/klip_source_root_preflight.py` without a selected root reads only
  `catalog.sqlite3`, never historical `stored_path` video files.
- With `--trusted-root`, it enumerates **only that explicitly chosen directory**
  using file metadata; skips symlinks and nested mountpoints; does not hash,
  decode, copy or modify any videos.
- Filename plus byte-size matches are only leads. Different names can still
  contain the same video, and matching names/sizes do not prove identity.
- The existing `klip index` verifies every selected healthy source's SHA before
  updating its catalog identity. It does not need to read a disconnected old
  catalog path when remapping SHA-identical local duplicates.
- A subsequent scan verifies original SHA independently before producing
  low-resolution motion proposals. It cannot yet prove sports outcomes.
- These changes do **not** implement fully resumable copy/staging, file-read
  timeouts, duplicate-content quarantine or automatic failed-media recovery.
  Those remain open in issue #77.

## Recovery on macOS

Set the **actual** existing workspace, then inspect the catalog with no
original-media access:

```bash
export WS="$HOME/AdventureAI-clean.QJnopt"
python scripts/klip_source_root_preflight.py --workspace "$WS" \
  --output "$WS/reports/source-inventory-$(date +%Y%m%d-%H%M%S).json"
```

Choose an **already healthy local** folder containing footage you copied or
otherwise recovered, such as a real folder under `~/Movies`. The following is
an example only; replace it with the actual verified local folder:

```bash
export HEALTHY_SOURCE_ROOT="$HOME/Movies/AdventureFootage"
test -d "$HEALTHY_SOURCE_ROOT" || { echo "No real healthy folder selected" >&2; exit 3; }

python scripts/klip_source_root_preflight.py --workspace "$WS" \
  --trusted-root "$HEALTHY_SOURCE_ROOT" \
  --output "$WS/reports/source-root-preflight-$(date +%Y%m%d-%H%M%S).json"

klip --workspace "$WS" index "$HEALTHY_SOURCE_ROOT" --dry-run
klip --workspace "$WS" index "$HEALTHY_SOURCE_ROOT"
```

For a **known healthy** SSD rooted under `/Volumes`, and only after verifying
it is *not* the stalled card, pass `--allow-mounted-ssd` to the preflight and
`ALLOW_MOUNTED_SSD=YES` for campaign scanning. The `klip index` command
may require `--allow-removable` for a selected `/Volumes` source. Never use
these overrides to bypass a failing disk.

Inspect the `index` output: failed files remain unverified and cannot be
counted as covered. An indexed duplicate keeps the original SHA identity.
If no healthy copies exist, stop here, recover/copy them using a proven stable
device first, and **do not** run the source scan.

Freeze a **new audit after index** because previous audits intentionally
preserve the old stored paths:

```bash
export CAMPAIGN_DIR="$WS/reports/full-highlight-verified-$(date +%Y%m%d-%H%M%S)"
CAMPAIGN_DIR="$CAMPAIGN_DIR" PHASE=audit bash scripts/klip_full_archive_highlights.sh

# Controlled pilot; the scanner rehashes and decodes 2 trusted-local files.
CAMPAIGN_DIR="$CAMPAIGN_DIR" PHASE=scan ENABLE_ORIGINAL_READS=YES \
  HEALTHY_SOURCE_ROOT="$HEALTHY_SOURCE_ROOT" SCAN_ASSETS_PER_PASS=2 SCAN_PASSES=1 \
  caffeinate -i bash scripts/klip_full_archive_highlights.sh

python -m json.tool "$CAMPAIGN_DIR/progress.json"
```

Check the pilot's `completed`, `failed`, `blocked`, `originalMediaReads`,
and duration-coverage metrics. Then resume the **same** frozen campaign
rather than discarding validated scan receipts:

```bash
CAMPAIGN_DIR="$CAMPAIGN_DIR" PHASE=scan ENABLE_ORIGINAL_READS=YES \
  HEALTHY_SOURCE_ROOT="$HEALTHY_SOURCE_ROOT" SCAN_ASSETS_PER_PASS=8 SCAN_PASSES=40 \
  caffeinate -i bash scripts/klip_full_archive_highlights.sh

CAMPAIGN_DIR="$CAMPAIGN_DIR" PHASE=packet ENABLE_ORIGINAL_READS=YES \
  HEALTHY_SOURCE_ROOT="$HEALTHY_SOURCE_ROOT" PREPARE_MISSING_PREVIEWS=YES \
  caffeinate -i bash scripts/klip_full_archive_highlights.sh
```

To scan a known healthy mounted SSD, also prefix the **scan/packet** commands
with `ALLOW_MOUNTED_SSD=YES`. A partially indexed or missing subset must stay
**unknown**, not be scored as a boring or negative example.

## Development qualification

```bash
python -m pytest -q tests/test_source_root_preflight.py tests/test_full_archive_campaign.py
bash -n scripts/klip_full_archive_highlights.sh
```

These tests are synthetic, not proof that the user's 232 original videos are
available or that a real full-archive scan completed.

#!/usr/bin/env bash
# Klip full-archive campaign, macOS bash 3.2 compatible.
# PHASE=audit is metadata-only. PHASE=scan requires explicit trusted local media.
set -Eeuo pipefail
umask 077
WS="${WS:-$HOME/AdventureAI-clean.QJnopt}"
PHASE="${PHASE:-audit}"
CAMPAIGN_DIR="${CAMPAIGN_DIR:-$WS/reports/full-highlight-campaign-$(date +%Y%m%d-%H%M%S)-$$}"
STUDY="${STUDY:-$WS/reports/archive-value-study-pilot-v1/study.json}"
SOURCE_ROOT="${HEALTHY_SOURCE_ROOT:-}"
SOURCE_ACK="${ENABLE_ORIGINAL_READS:-NO}"
MOUNTED_ACK="${ALLOW_MOUNTED_SSD:-NO}"
SSD_IDENTITY_FILE="${SSD_IDENTITY_FILE:-}"
RETRY_FAILED_SOURCES="${RETRY_FAILED_SOURCES:-NO}"
BATCH="${SCAN_ASSETS_PER_PASS:-8}"
PASSES="${SCAN_PASSES:-40}"
FPS="${SCAN_FPS:-1}"
PACKET_LIMIT="${PACKET_LIMIT:-120}"
PREPARE_MISSING_PREVIEWS="${PREPARE_MISSING_PREVIEWS:-NO}"
# Fail before creating a new frozen campaign if the selected source is a
# non-existent example path, or unacknowledged mounted media.
if [[ "$PHASE" != audit && "$PHASE" != packet && -n "$SOURCE_ROOT" ]]; then
  if [[ "$SOURCE_ROOT" = /Volumes/* && "$MOUNTED_ACK" != YES ]]; then
    echo "BLOCKED: mounted source root requires ALLOW_MOUNTED_SSD=YES; do not use stalled SDXC cards." >&2
    exit 3
  fi
  if [[ "$SOURCE_ROOT" = /absolute/path/* || ! -d "$SOURCE_ROOT" ]]; then
    echo "BLOCKED: HEALTHY_SOURCE_ROOT must be a real, existing, healthy local footage directory, not an example path." >&2
    echo "Run: python scripts/klip_source_root_preflight.py --workspace \"$WS\"" >&2
    exit 3
  fi
fi
command -v klip >/dev/null
command -v python >/dev/null
# Optional per-run macOS identity pin. This checks the mounted volume UUID and
# exact indexed root, not drive health. Disk IDs can change after reinsertion.
check_ssd_identity() {
  if [[ -z "$SSD_IDENTITY_FILE" ]]; then
    return 0
  fi
  if [[ -z "$SOURCE_ROOT" || "$MOUNTED_ACK" != YES ]]; then
    echo "BLOCKED: SSD_IDENTITY_FILE requires HEALTHY_SOURCE_ROOT and ALLOW_MOUNTED_SSD=YES." >&2
    return 3
  fi
  python scripts/klip_ssd_mount_guard.py verify \
    --workspace "$WS" --root "$SOURCE_ROOT" --identity "$SSD_IDENTITY_FILE"
}
if [[ "$PHASE" != audit ]]; then check_ssd_identity; fi
mkdir -p "$CAMPAIGN_DIR"
echo "Campaign: $CAMPAIGN_DIR (phase $PHASE)"
if [[ ! -f "$CAMPAIGN_DIR/audit/campaign-audit.json" ]]; then
  if [[ -e "$CAMPAIGN_DIR/audit" ]]; then
    echo "Incomplete audit. Preserve it and use a NEW CAMPAIGN_DIR." >&2
    exit 2
  fi
  echo "Building catalog-only audit. No original source I/O."
  if [[ -f "$STUDY" ]]; then
    klip --workspace "$WS" highlight-campaign-audit --study "$STUDY" \
      --output-dir "$CAMPAIGN_DIR/audit" >"$CAMPAIGN_DIR/audit-result.json"
  else
    klip --workspace "$WS" highlight-campaign-audit \
      --output-dir "$CAMPAIGN_DIR/audit" >"$CAMPAIGN_DIR/audit-result.json"
  fi
fi
python - "$CAMPAIGN_DIR/audit/summary.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
print("Catalog assets:",d["catalogAssetCount"])
print("Without prior episodes:",d["sourcesWithoutExistingEpisodes"])
print("Prior study:",d["frozenStudyCoverage"])
print("Path classes:",d["sourcePathClasses"])
print("Catalog quality gaps:",d["catalogQualityGaps"])
print("Unverified source paths remain UNKNOWN.")
PY
if [[ "$PHASE" = audit ]]; then
  klip --workspace "$WS" highlight-campaign-status \
    --audit "$CAMPAIGN_DIR/audit/campaign-audit.json" \
    >"$CAMPAIGN_DIR/progress.json"
  echo "Campaign truth-in-progress: $CAMPAIGN_DIR/progress.json"
  echo "Audit finished. To scan healthy local originals:"
  echo "CAMPAIGN_DIR='$CAMPAIGN_DIR' PHASE=scan ENABLE_ORIGINAL_READS=YES"
  exit 0
fi
if [[ "$PHASE" != scan && "$PHASE" != all && "$PHASE" != packet ]]; then
  echo "Use PHASE=audit, scan, packet or all" >&2; exit 2
fi
if [[ "$PHASE" != packet ]]; then
# The audit contains only catalogued paths. A new trusted folder that has not
# been indexed will not magically make old /Volumes paths eligible for scans.
# Check this lexically, without resolving or statting stale catalog paths.
if ! python - "$CAMPAIGN_DIR/audit/campaign-audit.json" "$WS" "$SOURCE_ROOT" <<'PY'
import json,os,sys
from pathlib import Path
audit=Path(sys.argv[1])
ws=Path(os.path.abspath(os.path.expanduser(sys.argv[2])))
selected=sys.argv[3]
allowed=[ws/"originals"]
if selected:
    allowed.append(Path(os.path.abspath(os.path.expanduser(selected))))
data=json.loads(audit.read_text())
eligible=sum(
    isinstance(item.get("storedPathAsCatalogued"),str)
    and item["storedPathAsCatalogued"].startswith("/")
    and any(
        Path(os.path.abspath(item["storedPathAsCatalogued"])).is_relative_to(root)
        for root in allowed
    )
    for item in data["assets"]
)
if eligible==0:
    print("BLOCKED: 0 catalogued sources are under managed originals or HEALTHY_SOURCE_ROOT.",file=sys.stderr)
    print("Index verified healthy copies first, then create a NEW campaign audit.",file=sys.stderr)
    print("Try: python scripts/klip_source_root_preflight.py --workspace WS --trusted-root HEALTHY_FOLDER",file=sys.stderr)
    raise SystemExit(3)
print("Catalog paths under authorized roots:",eligible,"of",len(data["assets"])," (not SHA-verified yet)")
PY
then
  exit 3
fi
if [[ "$SOURCE_ACK" != YES ]]; then
  echo "BLOCKED: requires ENABLE_ORIGINAL_READS=YES for healthy, local video ONLY." >&2
  exit 3
fi
command -v ffmpeg >/dev/null
command -v ffprobe >/dev/null
if ! [[ "$BATCH" =~ ^[0-9]+$ && "$PASSES" =~ ^[0-9]+$ && "$FPS" =~ ^[12]$ ]]; then
  echo "Invalid budgets" >&2; exit 2
fi
if [[ "$BATCH" -lt 1 || "$BATCH" -gt 256 ||
      "$PASSES" -lt 1 || "$PASSES" -gt 256 ]]; then
  echo "Batch/passes out of range" >&2; exit 2
fi
cmd=(--workspace "$WS" highlight-campaign-scan
     --audit "$CAMPAIGN_DIR/audit/campaign-audit.json"
     --output-dir "$CAMPAIGN_DIR/coarse-scan"
     --fps "$FPS" --workers 1 --max-assets "$BATCH")
if [[ -n "$SOURCE_ROOT" ]]; then cmd+=(--trusted-local-root "$SOURCE_ROOT"); fi
if [[ "$RETRY_FAILED_SOURCES" = YES ]]; then cmd+=(--retry-failed); fi
if [[ "$MOUNTED_ACK" = YES ]]; then cmd+=(--allow-mounted-ssd); fi
echo "Starting source scan. Sources must be on HEALTHY LOCAL storage."
# Preserve prior scan-pass stdout/stderr logs when resuming the same campaign.
# Per-source scan checkpoints are resume-safe, but pass logs once restarted at
# 001 and overwrote the first run's diagnostic evidence.
PASS_START="$(python scripts/klip_scan_pass_index.py --campaign-dir "$CAMPAIGN_DIR")"
for ((i=1; i<=PASSES; i++)); do
  check_ssd_identity
  pass_number=$((PASS_START + i - 1))
  args=("${cmd[@]}")
  if [[ -f "$CAMPAIGN_DIR/coarse-scan/scan-state.json" ]]; then args+=(--resume); fi
  result="$CAMPAIGN_DIR/scan-pass-$(printf '%03d' "$pass_number").json"
  logfile="$CAMPAIGN_DIR/scan-pass-$(printf '%03d' "$pass_number").stderr.log"
  if ! klip "${args[@]}" >"$result" 2>"$logfile"; then
    tail -n 25 "$logfile" >&2 || true
    echo "See failed scan log: $logfile" >&2
    exit 1
  fi
  pending="$(python - "$result" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
print(d["notYetAttemptedEligible"])
PY
)"
  echo "Pass $pass_number: $pending trusted-local sources remaining"
  if [[ "$pending" -eq 0 ]]; then break; fi
done
echo "Source scan reports: $CAMPAIGN_DIR/coarse-scan/per-source/"
echo "Originals outside the explicitly trusted root were not examined."
klip --workspace "$WS" highlight-campaign-status \
  --audit "$CAMPAIGN_DIR/audit/campaign-audit.json" \
  --scan-dir "$CAMPAIGN_DIR/coarse-scan" \
  >"$CAMPAIGN_DIR/progress.json"
fi

if [[ "$PHASE" = packet || "$PHASE" = all ]]; then
  if [[ ! -f "$CAMPAIGN_DIR/coarse-scan/scan-state.json" ]]; then
    echo "BLOCKED: run PHASE=scan on healthy local originals before making a new review packet." >&2
    exit 3
  fi
  if ! [[ "$PACKET_LIMIT" =~ ^[0-9]+$ ]] ||
      [[ "$PACKET_LIMIT" -lt 1 || "$PACKET_LIMIT" -gt 500 ]]; then
    echo "PACKET_LIMIT must be 1..500" >&2
    exit 2
  fi
  packetargs=(--workspace "$WS" highlight-campaign-review-packet
              --audit "$CAMPAIGN_DIR/audit/campaign-audit.json"
              --scan-dir "$CAMPAIGN_DIR/coarse-scan"
              --output-dir "$CAMPAIGN_DIR/review-packet"
              --limit "$PACKET_LIMIT" --per-source 4 --workers 2)
  if [[ -n "$SOURCE_ROOT" ]]; then
    packetargs+=(--trusted-local-root "$SOURCE_ROOT")
  fi
  if [[ "$MOUNTED_ACK" = YES ]]; then
    packetargs+=(--allow-mounted-ssd)
  fi
  if [[ "$PREPARE_MISSING_PREVIEWS" = YES ]]; then
    if [[ "$SOURCE_ACK" != YES ]]; then
      echo "BLOCKED: generating missing previews reads original video. Set ENABLE_ORIGINAL_READS=YES." >&2
      exit 3
    fi
    packetargs+=(--prepare-missing)
  fi
  check_ssd_identity
  if [[ -e "$CAMPAIGN_DIR/review-packet" ]]; then
    echo "Existing review packet preserved: $CAMPAIGN_DIR/review-packet" >&2
    echo "Use a new campaign folder for another immutable review packet." >&2
  else
    klip "${packetargs[@]}" >"$CAMPAIGN_DIR/packet-result.json" \
      2>"$CAMPAIGN_DIR/packet-errors.log"
  fi
  if [[ -f "$CAMPAIGN_DIR/review-packet/selection-report.json" ]]; then
    klip --workspace "$WS" highlight-campaign-status \
      --audit "$CAMPAIGN_DIR/audit/campaign-audit.json" \
      --scan-dir "$CAMPAIGN_DIR/coarse-scan" \
      --packet-dir "$CAMPAIGN_DIR/review-packet" \
      >"$CAMPAIGN_DIR/progress.json"
  fi
  echo "Truth-in-progress status: $CAMPAIGN_DIR/progress.json"
  echo "Open blind review: $CAMPAIGN_DIR/review-packet/index.html"
  echo "Export human annotations, then use highlight-feedback-plan and Reel Studio."
fi

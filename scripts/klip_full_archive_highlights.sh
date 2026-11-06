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
BATCH="${SCAN_ASSETS_PER_PASS:-8}"
PASSES="${SCAN_PASSES:-40}"
FPS="${SCAN_FPS:-1}"
command -v klip >/dev/null
command -v python >/dev/null
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
  echo "Audit finished. To scan healthy local originals:"
  echo "CAMPAIGN_DIR='$CAMPAIGN_DIR' PHASE=scan ENABLE_ORIGINAL_READS=YES"
  exit 0
fi
if [[ "$PHASE" != scan && "$PHASE" != all ]]; then
  echo "Use PHASE=audit, PHASE=scan or PHASE=all" >&2; exit 2
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
if [[ "$MOUNTED_ACK" = YES ]]; then cmd+=(--allow-mounted-ssd); fi
echo "Starting source scan. Sources must be on HEALTHY LOCAL storage."
for ((i=1; i<=PASSES; i++)); do
  args=("${cmd[@]}")
  if [[ -f "$CAMPAIGN_DIR/coarse-scan/scan-state.json" ]]; then args+=(--resume); fi
  result="$CAMPAIGN_DIR/scan-pass-$(printf '%03d' "$i").json"
  logfile="$CAMPAIGN_DIR/scan-pass-$(printf '%03d' "$i").stderr.log"
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
  echo "Pass $i: $pending trusted-local sources remaining"
  if [[ "$pending" -eq 0 ]]; then break; fi
done
echo "Source scan reports: $CAMPAIGN_DIR/coarse-scan/per-source/"
echo "Originals outside the explicitly trusted root were not examined."

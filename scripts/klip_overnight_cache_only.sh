#!/usr/bin/env bash
# Source-safe, checkpointed Klip night run. macOS bash 3.2 compatible.
# NEVER open original DJI/SDXC videos or use --prepare-previews.
# Full-resolution source scans are intentionally excluded until storage is healthy.
set -Eeuo pipefail

WS="${WS:-$HOME/AdventureAI-clean.QJnopt}"
PILOT="${PILOT:-$WS/reports/highlight-curation-pilot60-v1}"
PLAN="${PLAN:-$WS/reports/highlight-feedback-for-reels-v1/review-discovery-plan.json}"
STUDY="${STUDY:-$WS/reports/archive-value-study-pilot-v1/study.json}"
ASSIGNMENTS="${ASSIGNMENTS:-}"
MODEL="${MODEL:-gemma3:4b}"
KLIP_RUNROOT="${KLIP_RUNROOT:-$WS/reports/klip-safe-night-$(date +%Y%m%d-%H%M%S)-$$}"
RUNROOT="$KLIP_RUNROOT"
export WS PILOT PLAN STUDY ASSIGNMENTS MODEL KLIP_RUNROOT
mkdir -p "$RUNROOT"
umask 077

echo "Klip night root: $RUNROOT"
echo "Frozen original pilot: $PILOT"
echo "Existing human feedback: $PLAN"
echo "Operating mode: CACHE-ONLY, originals untouched. Logs: $RUNROOT/*.stderr.log"
test -f "$PILOT/curation-manifest.json" || {
  echo "ERROR: frozen curation manifest does not exist: $PILOT/curation-manifest.json" >&2; exit 2;
}
test -f "$PLAN" || {
  echo "ERROR: review-discovery-plan.json does not exist: $PLAN" >&2; exit 2;
}
command -v klip >/dev/null || { echo "ERROR: klip command missing. Activate .venv and install package." >&2; exit 2; }
command -v python >/dev/null || { echo "ERROR: Python is required." >&2; exit 2; }
command -v ffmpeg >/dev/null || { echo "ERROR: FFmpeg is required for MP4 export." >&2; exit 2; }
python - "$WS" <<'PY'
import os,sys
s=os.statvfs(sys.argv[1])
free=s.f_bavail*s.f_frsize
print(f"Workspace free disk space: {free / 2**30:.1f} GiB")
if free < 20*2**30:
    raise SystemExit("STOP: Less than 20 GiB free. Do not start overnight rendering.")
PY

preserve_incomplete_dir() {
  label="$1"
  path="$2"
  if test -d "$path" && ! test -f "$RUNROOT/.$label.done"; then
    old="$path.incomplete-$(date +%s)-$"
    mv "$path" "$old"
    echo "[PRESERVED partial output] $old"
  fi
}

run_step() {
  name="$1"
  shift
  if test -f "$RUNROOT/.$name.done"; then
    echo "[SKIP done] $name"
    return 0
  fi
  echo "[$(date '+%F %T')] START $name"
  echo "  command: $*" >>"$RUNROOT/commands.log"
  if "$@" >"$RUNROOT/$name.stdout.json" 2>"$RUNROOT/$name.stderr.log"; then
    touch "$RUNROOT/.$name.done"
    echo "[$(date '+%F %T')] DONE $name"
  else
    result=$?
    echo "[$(date '+%F %T')] FAILED $name (exit=$result)" >&2
    echo "See $RUNROOT/$name.stderr.log" >&2
    tail -n 20 "$RUNROOT/$name.stderr.log" >&2 || true
    return "$result"
  fi
}

# Always create a NEW recovered packet, preserving every frozen manifest
# byte, reusing only receipt-verified LOCAL derivatives, never the SD card.
run_step doctor klip --workspace "$WS" highlight-pilot-doctor \
  --pilot-dir "$PILOT" --feedback "$PLAN"
RECOVERED="$RUNROOT/recovered-pilot"
preserve_incomplete_dir recover "$RECOVERED"
run_step recover klip --workspace "$WS" highlight-pilot-recover \
  --pilot-dir "$PILOT" --feedback "$PLAN" --output-dir "$RECOVERED"

CACHE_COUNT="$(python - "$RECOVERED/recovery-report.json" <<'PY'
import json,sys
print(json.load(open(sys.argv[1]))["verifiedReviewProxies"])
PY
)"
echo "Verified, recovered, local playback previews: $CACHE_COUNT"

if test "$CACHE_COUNT" -gt 0; then
  STUDIO="$RUNROOT/interactive-reels"
  preserve_incomplete_dir studio "$STUDIO"
  run_step studio klip --workspace "$WS" highlight-reel-studio \
    --pilot-dir "$RECOVERED" --feedback "$PLAN" \
    --workers 4 --scout-limit 80 --output-dir "$STUDIO"

  preserve_incomplete_dir next-pass "$RUNROOT/adaptive-next-pass"
  run_step next-pass klip --workspace "$WS" highlight-next-pass \
    --pilot-dir "$RECOVERED" --feedback-plan "$PLAN" \
    --studio-dir "$STUDIO" --limit 160 --per-source 8 \
    --output-dir "$RUNROOT/adaptive-next-pass"

  preserve_incomplete_dir reel-render "$RUNROOT/preview-mp4-reels"
  run_step reel-render klip --workspace "$WS" highlight-reel-render \
    --studio-dir "$STUDIO" --reel all --workers 2 \
    --output-dir "$RUNROOT/preview-mp4-reels"

  echo "INTERACTIVE_REEL=$STUDIO/index.html"
  echo "NEXT_PASS=$RUNROOT/adaptive-next-pass/index.html"
  echo "MP4_REPORT=$RUNROOT/preview-mp4-reels/render-report.json"
else
  echo "No verified cached previews recovered. Refusing to invent a reel."
  echo "Temporal processing can still inspect other existing caches without SD-card reads."
fi

# Largest safe analysis batch: source study is READ AS AN EXISTING JSON FILE.
# Do not regenerate archive-value-study here; that stage may stat SD media.
if test -f "$STUDY"; then
  FEATURES_DIR="$RUNROOT/feature-template-300"
  preserve_incomplete_dir features-300 "$FEATURES_DIR"
  run_step features-300 klip --workspace "$WS" social-lens-feature-template \
    --study "$STUDY" --limit 300 --output-dir "$FEATURES_DIR"
  TEMPORAL_DIR="$RUNROOT/temporal-300"
  if test -d "$TEMPORAL_DIR" && ! test -f "$RUNROOT/.temporal-300.done" &&
     ! test -f "$TEMPORAL_DIR/checkpoint.json"; then
    preserve_incomplete_dir temporal-300 "$TEMPORAL_DIR"
  fi
  if test -d "$TEMPORAL_DIR" && ! test -f "$RUNROOT/.temporal-300.done"; then
    run_step temporal-300 klip --workspace "$WS" social-lens-temporal \
      --features "$FEATURES_DIR/feature-template.json" \
      --limit 300 --run --resume --output-dir "$TEMPORAL_DIR"
  else
    run_step temporal-300 klip --workspace "$WS" social-lens-temporal \
      --features "$FEATURES_DIR/feature-template.json" \
      --limit 300 --run --output-dir "$TEMPORAL_DIR"
  fi
  # Notice: NO --prepare-previews, so missing short H.264 previews
  # explicitly stay missing; external originals are never opened.
else
  echo "SKIP temporal-300: frozen study JSON absent: $STUDY"
fi

# Optional extended checkpointed local Ollama job. These only operate
# on the already-verified, already-judged local proxies; NEVER ask triage
# to generate missing H.264 clips from the SD card.
if test -z "$ASSIGNMENTS"; then
  ASSIGNMENTS="$(python - "$WS" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1])/"reports"
found=sorted(root.rglob("assignments.json"))
if len(found)==1:
    print(found[0])
elif found:
    print("Several assignment manifests found. Set ASSIGNMENTS to one of:",file=sys.stderr)
    for f in found[:12]:
        print(f"  {f}",file=sys.stderr)
PY
)"
fi
if test -n "$ASSIGNMENTS" &&
   test -f "$ASSIGNMENTS" &&
   command -v curl >/dev/null &&
   command -v ollama >/dev/null &&
   curl -fsS --max-time 4 http://127.0.0.1:11434/api/tags >/dev/null 2>&1 &&
   ollama list | grep -Fq "$MODEL"; then
  LOOK_DIR="$RUNROOT/cached-ollama-secondlooks"
  if test -d "$LOOK_DIR" && ! test -f "$RUNROOT/.cached-ollama.done" &&
     ! test -f "$LOOK_DIR/secondlook-state.json"; then
    preserve_incomplete_dir cached-ollama "$LOOK_DIR"
  fi
  if test -d "$LOOK_DIR" && ! test -f "$RUNROOT/.cached-ollama.done"; then
    run_step cached-ollama klip --workspace "$WS" archive-cached-secondlook-batches \
      --assignments "$ASSIGNMENTS" --model "$MODEL" \
      --batches 20 --resume --output-dir "$LOOK_DIR"
  else
    run_step cached-ollama klip --workspace "$WS" archive-cached-secondlook-batches \
      --assignments "$ASSIGNMENTS" --model "$MODEL" \
      --batches 20 --output-dir "$LOOK_DIR"
  fi
  echo "SECOND_LOOK_REPORT=$LOOK_DIR/secondlook-state.json"
else
  echo "SKIP optional local Ollama batches: no unique ASSIGNMENTS, installed model or responding localhost Ollama."
  echo "Set ASSIGNMENTS=/path/inside/WS/reports/assignments.json MODEL=gemma3:4b to enable later."
fi

echo
echo "Klip safe overnight work finished. Inspect logs and reports under:"
echo "$RUNROOT"
echo "To resume interrupted steps, rerun with KLIP_RUNROOT='$RUNROOT'"

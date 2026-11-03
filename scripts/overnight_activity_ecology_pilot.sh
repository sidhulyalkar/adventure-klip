#!/usr/bin/env bash
# Offline footage classification with explicit optional activity promotion.
# Source files are read only; report outputs stay inside the Klip workspace.
set -Eeuo pipefail

WS="${WS:-$HOME/AdventureAI-clean.QJnopt}"
RUN_DIR="${KLIP_ACTIVITY_REPORT_DIR:-$WS/reports/activity-overnight-v1}"
PLAN="${KLIP_ECOLOGY_PLAN:-$WS/exports/ecology-overnight-v1.json}"
AUTO_APPLY="${KLIP_APPLY_STRONG:-0}"
FRAMES="${KLIP_ACTIVITY_FRAMES:-5}"
ECOLOGY_PILOT="${KLIP_ECOLOGY_PILOT:-1}"
DB="$WS/catalog.sqlite3"

stamp() { date '+%Y-%m-%d %H:%M:%S'; }
note() { printf '\n[%s] %s\n' "$(stamp)" "$*" >&2; }
fail() { note "ERROR: $*"; exit 1; }

command -v sqlite3 >/dev/null || fail "sqlite3 command missing"
command -v ffmpeg >/dev/null || fail "ffmpeg command missing"
command -v klip >/dev/null || fail "klip CLI missing; activate your Python venv"
[[ -f "$DB" ]] || fail "No catalog at $DB"
[[ "$AUTO_APPLY" == 0 || "$AUTO_APPLY" == 1 ]] || fail "KLIP_APPLY_STRONG must be 0 or 1"
[[ "$ECOLOGY_PILOT" == 0 || "$ECOLOGY_PILOT" == 1 ]] || fail "KLIP_ECOLOGY_PILOT must be 0 or 1"

source_path="$(sqlite3 "$DB" "SELECT stored_path FROM assets WHERE duration > 0 ORDER BY created_at LIMIT 1;")"
[[ -n "$source_path" && -f "$source_path" ]] || fail \
  "Source video unavailable; mount the original SSD at the indexed mount path"

note "Workspace: $WS"
note "Active videos:"
sqlite3 -header -column "$DB" \
  "SELECT activity,COUNT(*) AS videos FROM assets GROUP BY activity ORDER BY activity;" >&2

note "Stage 1: source-verified sparse activity proposals (resumable; existing reports reused)"
klip --workspace "$WS" activity-scan \
  --output-dir "$RUN_DIR" --frames "$FRAMES" --allow-model-download

note "Activity review: $RUN_DIR/review.html"
note "Activity summary: $RUN_DIR/summary.json"

note "Stage 2: conservative preview of eligible snorkeling candidates"
klip --workspace "$WS" activity-apply \
  --report-dir "$RUN_DIR" --activity snorkeling \
  --max-assets 2 --minimum-margin 0.065 --minimum-votes 5

if [[ "$AUTO_APPLY" != 1 ]]; then
  note "No labels changed. To run complete night pilot, explicitly set KLIP_APPLY_STRONG=1."
  note "Review the proposed activities in $RUN_DIR/review.html"
  exit 0
fi

note "Stage 3: explicitly requested promotion, maximum TWO strong snorkeling proposals"
klip --workspace "$WS" activity-apply \
  --report-dir "$RUN_DIR" --activity snorkeling \
  --max-assets 2 --minimum-margin 0.065 --minimum-votes 5 --apply

snorkel_count="$(sqlite3 "$DB" "SELECT COUNT(*) FROM assets WHERE activity='snorkeling' AND duration>0;")"
note "Catalog now has $snorkel_count positive-duration snorkeling assets"
if [[ "$snorkel_count" -eq 0 ]]; then
  note "No sufficiently strong snorkeling evidence. No forced labels, no ecology scan."
  note "Inspect $RUN_DIR/review.html and manually verify one video if appropriate."
  exit 0
fi

if [[ "$ECOLOGY_PILOT" != 1 ]]; then
  note "Ecology pilot disabled. Classification stage finished."
  exit 0
fi

note "Stage 4: at most TWO snorkeling assets, 96 sparse detector frames per video"
if [[ -f "$PLAN" ]]; then
  note "Reusing existing plan: $PLAN"
else
  klip --workspace "$WS" adaptive-plan \
    --activity snorkeling --limit 2 \
    --coverage-seconds 40 --interest-seconds 2 \
    --max-frames 96 --output "$PLAN"
fi

note "Stage 5: explicit local OWLv2 marine inference on the small adaptive plan"
klip --workspace "$WS" adaptive-scenes \
  --plan "$PLAN" --backend ecology-owlv2 --decode-mode batched

note "Stage 6: produce private, source-PTS-verified organism crop packets"
while IFS= read -r asset; do
  [[ -n "$asset" ]] || continue
  out="$WS/reports/ecology-crops-overnight-${asset:0:12}"
  if [[ -f "$out/manifest.json" ]]; then
    note "Already generated: $out/index.html"
    continue
  fi
  if [[ -e "$out" ]]; then
    note "Existing incomplete output at $out; not overwriting. Inspect manually."
    continue
  fi
  klip --workspace "$WS" ecology-crops \
    --asset "$asset" --max-events 3 --crops-per-event 2 \
    --width 1536 --output-dir "$out"
  if [[ -f "$out/index.html" ]]; then
    note "Marine evidence gallery: $out/index.html"
  else
    note "No PTS-backed organism crops for $asset; nothing fabricated."
  fi
done < <(sqlite3 "$DB" \
  "SELECT id FROM assets WHERE activity='snorkeling' AND duration>0
   ORDER BY created_at,id LIMIT 2;")

note "Overnight pilot finished. First inspect $RUN_DIR/review.html and the ecology crop galleries."

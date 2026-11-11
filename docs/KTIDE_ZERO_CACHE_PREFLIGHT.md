# K-TIDE zero-SigLIP2 cache recovery

A source inventory reporting 0 SigLIP2-ready out of 232 source IDs
does not mean footage or existing cached analyses are missing.

Observed owner-side investigation (2026-11-11):
- 232 sources in catalog, 25 recorded semantic jobs.
- 19 completed histogram semantic jobs, 4 failed, 1 partial.
- 1 partial ecology-OWLv2 job; no SigLIP2 job.
- 718 semantic frame checkpoints, 21 semantic JSON files.
- 16 valid active histogram sources with a single frozen model key
  (cdd573e7a357ac567854).
- All 16 catalog activity labels are "unknown", not verified sport labels.

## Safety-first preflight

    export WS="$HOME/AdventureAI-clean.QJnopt"
    python scripts/klip_ktide_preflight.py \
      --workspace "$WS" --provider histogram \
      > "$WS/reports/ktide-histogram-preflight.json"

The command opens catalog.sqlite3 read-only, validates completed semantic
artifact receipts and reports capture-date *buckets for inspection*.
It does NOT open source videos, create/activate cache jobs, classify sport,
or claim independent trips. Do not commit the output: it contains private
catalog SHA IDs and recording timestamps.

## What next?

1. Use the histogram-ready 16 sources for **local pipeline smoke only**.
   A histogram is a low-information color distribution, not a useful
   athlete/wildlife representation. A successful smoke does not validate
   temporal semantics or scene classification.
2. Assign group IDs only after reviewing outings. Multiple source files
   from the same actual trip/day must have the same group; different dates
   can still be one trip, so do not auto-split by capture date.
3. Use manual owner-verified sport/goal/subsport assignments; all 16
   activities are currently "unknown".
4. With at least three independent outing groups, prepare a small histogram
   training dataset using:

       python scripts/klip_plan_ktide.py --workspace "$WS" \
         --provider histogram \
         --assignments "$WS/reports/ktide-assignments-histogram.json" \
         > "$WS/reports/ktide-selection-histogram.json"

       python scripts/klip_prepare_ktide.py --workspace "$WS" \
         --provider histogram \
         --selection "$WS/reports/ktide-selection-histogram.json" \
         --name ktide-histogram-smoke-001

   Then run train_local with a new name, following KTIDE_TRAINING_V1.md.
5. Before any scientific heldout study, generate compatible SigLIP2 caches
   for a larger owner-approved selection of independent trips. Semantic
   extraction does explicitly hash/read the selected original videos and
   may fetch pretrained model weights if not already installed. Do not
   run it implicitly across the full 232-source archive.
6. A small histogram smoke cannot support the published 12-query/5-candidate
   cross-trip study; it needs at least two held-out trip groups with enough
   eligible candidate windows and independent verified human judgments.

The preflight is a **diagnostic tool**, not a remediation or benchmark score.

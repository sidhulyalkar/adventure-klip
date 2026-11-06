# Klip: source-safe overnight media analysis and curation repair

## Failure diagnosis

The first studio error arose while reading a required local JSON report.
Klip requires (1) original curation-manifest.json, (2) presentation.json
in that same frozen pilot folder, and (3) the review-discovery-plan.json
that was generated from that exact frozen manifest. Testing only files
1 and 3 can miss the second. The vague historical error also covered
empty, oversized and symlinked files. The second adaptive-queue failure
was a cascade from the missing reel-manifest.json.

Run:

    klip --workspace "$WS" highlight-pilot-doctor \
      --pilot-dir "$PILOT" --feedback "$PLAN"

This reports the exact missing or malformed file and fingerprint mismatch.
The doctor does not read or verify source camera media.

## Non-destructive recovery

Run:

    klip --workspace "$WS" highlight-pilot-recover \
      --pilot-dir "$PILOT" --feedback "$PLAN" \
      --output-dir "$WS/reports/curation-recovered-NEW"

Recovery writes a separate new packet, preserves exact frozen original
manifest bytes and SHA-256, reuses only already locally receipt-verified
H.264 preview derivatives, reconstructs presentation.json and a review
index.html. If a preview receipt or media cannot be verified, the
record stays unknown/unavailable, not guessed. It cannot touch a
DJI original or delete the previous packet. The original human review
export remains linked by its immutable manifest fingerprint.

## One launch for the whole cache-only night run

On the corresponding feature branch, after installing the package:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export PILOT="$WS/reports/highlight-curation-pilot60-v1"
    export PLAN="$WS/reports/highlight-feedback-for-reels-v1/review-discovery-plan.json"

    caffeinate -i bash scripts/klip_overnight_cache_only.sh

The script is written for macOS bash 3.2. It checks 20 GiB free space
and local FFmpeg, logs every command into a new private report directory,
and runs these safe stages sequentially:

1. Pilot doctor and immutable local recovered packet.
2. Bounded local video copy/receipt verification (up to 4 workers),
   multi-collection interactive Reel Studio and up to 80 known-sport
   scouting candidates, explicitly separated from human highlights.
3. Source-linked adaptive queue, up to 160 review proposals; per-source
   cap 8; only existing local verified previews linked.
4. Up to two concurrent local FFmpeg encoding jobs for per-collection
   960x540 preview-quality H264 MP4 files, with no source audio.
5. A fresh common-rubric feature template for up to 300 candidates
   from an EXISTING frozen archive-value-study JSON; source media is
   not probed or rescanned to regenerate the study.
6. Checkpointed temporal image measurements on available verified
   cached short H.264 previews, up to 300 windows, without allowing
   missing previews to invoke original-source reads.
7. If ASSIGNMENTS is supplied (or exactly one local assignments.json
   is discoverable) and a matching local Ollama vision model is running,
   up to 20 sequential micro-batches of eight existing adaptive-v2
   eligible cached preview second looks. The model is not used to infer
   definitive sport outcome, event rarity or training ground truth.

Optional local model parameters:

    export ASSIGNMENTS="$WS/reports/YOUR-FROZEN-REVIEW/assignments.json"
    export MODEL=gemma3:4b
    caffeinate -i bash scripts/klip_overnight_cache_only.sh

Only an actual existing private assignments file inside workspace
reports/ or exports/ can be used. If this optional stage lacks the
prerequisites, it is safely skipped. One verified local preview is
not equivalent to full archive coverage.

## Resume without redoing completed stages

The night script prints an output directory of the form:

    "$WS/reports/klip-safe-night-YYYYMMDD-HHMMSS-PID"

You can reuse it by setting that exact path, e.g.:

    export KLIP_RUNROOT="$WS/reports/klip-safe-night-ACTUAL-FOLDER"
    caffeinate -i bash scripts/klip_overnight_cache_only.sh

Completed stages are skipped. The temporal stage has a true journal
and uses --resume on interrupted runs. The cached local Ollama batch
runner stores atomic per-batch JSON and excluded review IDs, with
strict model and assignment SHA checks on resume.

Inspect output and progress:

    find "$KLIP_RUNROOT" -maxdepth 1 -name '*.stderr.log' -print
    tail -f "$KLIP_RUNROOT/temporal-300.stderr.log"
    cat "$KLIP_RUNROOT/recover.stdout.json"
    open "$KLIP_RUNROOT/interactive-reels/index.html"
    open "$KLIP_RUNROOT/adaptive-next-pass/index.html"

## What is deliberately NOT in the overnight run

No archive-pilot --run, no archive-local-triage --run, no original
source hashing, no source-identity bypass, no content deletion, no
automatic LLM-derived truth. The current archive-value-study scans
original-path existence, so it is not regenerated while the SDXC
reader remains untrusted. Critically social-lens-temporal is invoked
WITHOUT --prepare-previews. Do not increase original-video read
parallelism against the previously blocked SDXC source.

If few verified previews exist, much of this pipeline will report
unavailable windows or finish sooner than expected. That is not a
failure to use the compute budget. It means source-media recovery to
healthy local storage, followed by serial original verification and
resumable source ingest, must precede a full archive rerun.

Running caffeinate -i prevents idle sleep while the terminal remains
active, but closing the MacBook lid can still suspend processing.

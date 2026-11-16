# Klip Ski: source-linked Run Context V1

## Bug: the architecture clips a run before identifying its events

The old archive \`full_archive_campaign._proposals\` chooses 20-second windows
around single motion peaks and uniform timeline controls. Its companion
\`highlight_curation_pilot\` also creates 20-second controls. That is the
**intentional discovery-preview budget**, not a physical ski-run boundary.
The blind review example displays source-relative **16–36 seconds** but the
owner reports additional park jumps later in the same source recording.
Neither this screenshot nor one sampled frame proves exact jump locations.

Auto-Scout's 120 clips came from 108 distinct sources among successful
analyses, so neighboring source windows generally are not available for
joining. The seven-frame VLM cannot infer what happens beyond a window.
The error is attempting to use a \`Moment\` as the \`Run\` unit.

## Better data model

    SourceRecording (immutable identity, duration, timebase)
       └── RunContext (long bounded single-source candidate, NOT verified run)
             └── HumanReviewedRun (actual continuous start→end)
                  ├── Feature 1: jump / box / rail / ...
                  ├── Feature 2: jump / speed flow / ...
                  └── Feature 3: jump / ride-away / ...
                         └── EventMoment (short edit candidate)
                              └── EditVariant (highlight, immersive, montage)

A full park run can eventually score for sequencing, transitions, originality,
multiple-feature continuity and completion. One isolated jump can be good but
should not force the entire line into an arbitrary 20-second cut. Skiing
footage containing lift rides, flat transits and stops needs *boundary
evidence* and explicit review, not automatic max-duration concatenation.

## Implemented V1 repair

An **opt-in, targeted** \`klip_ski_run_context.py\` workflow:

1. Identify one existing 20s anchor via source SHA prefix and exact bounds.
2. Resolve a matching frozen audit and original already-SHA-verified scan
   receipt, without opening original camera media.
3. Plan a 2–4 minute source-relative context centered around the anchor,
   prioritizing time after the window so subsequent jumps remain visible.
   The context is a **hypothesis**, not a detected continuous run.
4. With explicit \`--render-context --allow-mounted-ssd\`, reverify the SHA of
   **one** explicitly authorized original source, validate its duration and
   create a 960px H264 private preview with audio if available. Do not process
   the other 231+ videos. Source time alignment remains approximate due to
   FFmpeg seek/re-encode until decoded frame PTS is mapped.
5. Use the offline run-review page to mark a full run's start/end, multiple
   feature timestamps, run type and a Keep/Skip preference. Export owner
   annotations as local JSON for later model calibration; no automatic
   owner-label promotion or final reel editing.

### One source metadata-only inspection (safe without SSD)

From the feature checkout:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export PILOT="$WS/reports/klip-v2-pilot-20261114-153934-88998"

    python3 scripts/klip_ski_run_context.py \
      --workspace "$WS" --pilot "$PILOT" \
      --source-prefix 2efff9077808 \
      --anchor-start-ms 16000 --anchor-end-ms 36000 \
      --before-s 40 --after-s 180 --max-s 240

This checks frozen metadata and scanned-source receipts. If multiple copies
of the same source have different audits, pass \`--audit\` with the exact
matching \`campaign-audit.json\` path, under private workspace reports.

### Explicitly render ONLY the matching source (requires healthy mounted SSD)

    export RUN="$WS/reports/klip-ski-run-context-$(date +%Y%m%d-%H%M%S)-$$"

    python3 scripts/klip_ski_run_context.py \
      --workspace "$WS" --pilot "$PILOT" \
      --source-prefix 2efff9077808 \
      --anchor-start-ms 16000 --anchor-end-ms 36000 \
      --before-s 40 --after-s 180 --max-s 240 \
      --trusted-source-root "/Volumes/Sid_DJI_SSD/DCIM" \
      --allow-mounted-ssd --render-context --output "$RUN"

    open "$RUN/run-review.html"

The first new source SHA verification is potentially expensive on large
videos; it is limited to **one targeted original recording**. Original videos
are never modified or uploaded. Missing source access or SHA mismatch fails
closed, leaving the old 120-clip evidence intact.

### Next experiment: actual run discovery

Use several owner-reviewed long contexts as ground truth. Evaluate three
candidate boundary strategies against human run starts/ends:

1. Coarse cached stop/lift/flat-snow state transitions plus motion
   (cannot alone determine park completeness).
2. Dense temporal retrieval using frozen pretrained video encoder
   embeddings at overlapping 2–5s steps, with human run exemplars.
3. Event graph: approach → feature → landing/ride-away → transition → next
   feature, terminating at explicit stop/lift/cut or outing boundary.

Measure **run recall@K**, feature recall, missed third jumps,
boundary undercoverage in seconds, false merging of unrelated runs,
and viewer preference for full-line vs single-moment edits. Retain
20-second clips as lightweight discovery *thumbnails*, not as fixed
run boundaries. Do not rewrite the original scan's frozen algorithm or
claims without a versioned experiment and held-out improvement.

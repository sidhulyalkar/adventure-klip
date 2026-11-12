# K-MOMENT capture-time neighborhoods V1

**Status:** Implemented read-only, source-level candidate chronology; no
trip, filming location, landscape identity or event match is established.

## Why this matters

A skiing video may be preceded by a chairlift recording and followed by
another segment of the same run. Similarly, separate snorkeling clips
may form a meaningful dive sequence. **This is not established from
capture metadata alone.** We can, however, determine which nearby source
recordings should be compared with an evidence-aware video model later.

This small, safe V1 stage reads the existing SQLite assets catalog in
read-only mode. It uses only each source's SHA-256 ID, timezone-aware
capture start timestamp and recorded duration. No source video, audio,
frame embedding, GPS trace or photo is read. Camera clocks may be
incorrect, camera files may be split, and a long outing may span days;
links are explicitly provisional and **not** independent trip groups.

## Run against the private 232-source catalog

    export WS="$HOME/AdventureAI-clean.QJnopt"
    python scripts/klip_moment_context.py \
      --workspace "$WS" \
      --max-gap-hours 12 \
      --max-neighbors 4 \
      > "$WS/reports/moment-capture-neighborhoods-v1.json"

Summarize proposed relationships:

    python - <<'PY'
    import json, os
    from pathlib import Path
    p=Path(os.environ["WS"])/"reports/moment-capture-neighborhoods-v1.json"
    d=json.loads(p.read_text())
    for k in ("catalogSourceCount",
              "sourcesWithParseableTimezoneAwareCapture",
              "sourcesWithUnknownOrAmbiguousCaptureTime",
              "sourcesWithUnusableDuration","candidateRelationships",
              "verifiedOutingGroups","geolocationInferred"):
        print(k, d[k])
    for edge in d["links"][:12]:
        print(edge["sourceSha256Pair"][0][:12],
              edge["sourceSha256Pair"][1][:12],
              "end-to-start gap(s)=",
              edge["earlierRecordingEndToLaterStartGapSeconds"])
    PY

This writes an output report **only because the shell redirects stdout**;
the Python implementation itself does not create or change any files.
Do not commit private source IDs or capture-time records.

## Interpretation contract

An edge records two catalog source identities and observable *capture
clock proximity* (start gap and, where duration is valid, approximate
end-to-start gap). The link explicitly declares:

- sameOuting = unknown
- sameSkiRunOrDiveSite = unknown
- sameLocation = unknown
- sharedSubjects = unknown
- humanConfirmed = false
- relativeVisualAppearanceCompared = false
- capturedClockAccuracyVerified = false

The reported max-neighbors bound limits candidate *selection per
recording*. Because the graph contains reciprocal edge references,
an individual source may appear in more than max-neighbors total
incident edges if several other sources selected it.

This graph may help prioritize later visual comparisons; it must
not assign training/validation/test trip groups, infer a resort run,
establish animal reidentification, automatically rename video files
or publish private location traces.

## Next source context enrichment

Only after temporal and semantic video evidence becomes available:

1. Combine source chronology with scene embeddings and distinctive
   stable terrain/reef landmarks under a frozen versioned model.
2. Use GPS/IMU or clearly legible trail signs, if available, to
   corroborate specific location hypotheses. Record map-match accuracy,
   camera clocks and local sensor limitations.
3. Link intervals within a recording to nearby intervals in other
   recordings; allow many-to-many episodes, not only one activity
   for an entire source.
4. Re-run scene descriptions using an explicit evidence neighborhood
   and prefer local, event-specific supported language over vague
   source-wide narratives.
5. Ask the owner to confirm only ambiguous, high-information
   outing or rare-encounter relationships, not every video.
6. Track verified outing identity *separately* from soft
   source-to-source relationships and archive similarity.

See the K-MOMENT scene dossier specification for how these links can
later support long-form skiing and marine-ecology scene interpretations.

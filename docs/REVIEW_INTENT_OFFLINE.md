# Klip: private review intent -> editorial routing on main

This is a **standalone, offline bridge** for the frozen human-curation-review
export. It ports the independently testable routing rules from the stacked
experimental reel work into main without importing the experimental SSD and
video-processing dependencies.

The previous review batch contained 117 exported judgments: 46 keep,
70 skip, 1 cannot tell. Most entries selected the old UI default goal
athletic_skill, so their natural-language comments must not silently be
treated as accurate action labels. Numeric quality ratings are sparse.
No private records or review text are checked into Git.

## Run on your own Mac

Use the original exact exported JSON file and, ideally, its corresponding
frozen review manifest (whose **file bytes** hash to export.manifestSha256):

    python scripts/klip_review_intent.py \
      --reviews "/absolute/path/to/klip-curation-reviews-...json" \
      --manifest "/absolute/path/to/curation-manifest.json" \
      > "$HOME/AdventureAI-clean.QJnopt/reports/review-intent-v1.json"

This reads local JSON only, not media or previews. It does not create a
directory, import the reviews into the media database, modify frozen receipts,
retrain a model, or automatically render/publish anything. Ensure the output
directory already exists and store the report privately.

The optional --manifest validates the exact SHA-256 digest, every reviewed
record ID, and source-window marker bounds. Without it, the report explicitly
reports manifestDigestVerified=false; do not interpret its routes as
verified source-linked observations. Even WITH the manifest, the tool never
checks playback SHA receipts and cannot establish actual action/species
truth from the user note.

## What it returns

- Explicit Keep/Skip/abstention totals and requested goal distribution.
- Goal-conditioned editorial routes: action, nature, scenery, memory,
  technique analysis, unresolved/review required.
- Non-executable editing followups: trim proposals, look at nearby source
  footage, underwater-only spans, compare alternate encounters, optional
  telemetry, nondestructive postprocessing and manual-only discard holds.
- Per-record route and source-relative time-marker metadata for local
  inspection. No raw rationale copied to the result ledger.
- Provenance flags stating that no media, source-video or previews were read.

A Skip for a selected derivative is **not** a negative classification for
the whole recording, and a human text explanation is not video ground truth.

## How it connects to KSRE V1

KSRE's weights express **how to interpret observed evidence** for a goal
and sub-sport. This tool expresses **the reviewer's editorial intent**.
They are independent:

1. Route user preference into a destination and propose follow-up inspections.
2. Verify actual scene features with source-backed temporal observations and
   quality/rubric annotation. Never invent 35 feature values from a comment.
3. Evaluate the selected goal x sub-sport profile with the source-aligned
   evidence ledger, preserving unknown spans.
4. Compare a proposed cut with independently judged alternatives.
5. Update weight presets only after enough independently adjudicated,
   source-disjoint pairwise preferences exist.

The experiment should compare this human-guided routing against the frozen
existing baseline, including cases where a rare nature encounter is badly
filmed but still worth recovering, and where crashes belong in technique
analysis rather than action montages.

No source-media deletion or publishing path exists in this module.

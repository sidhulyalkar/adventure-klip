# K-MOMENT Archive Profile & Sport-Aware Pilot V1

**Status:** Implemented, metadata-only, read-only baseline and pilot
candidate selection. Nothing in this report has inspected the actual
private footage, certified sport or outing identity, trained a new model,
ranked moments or improved a video. It is a decision support tool for
the K-TIDE State V2 experiment.

## Why profile the archive before model selection?

The source catalog currently contains **232 videos** according to the
user's latest *local* metadata audit. The experiment so far uses 16
histogram-ready sources, 46 windows, 73.32% connected frame adjacency,
40/46 windows with three-frame order evidence, and 560 provisional
capture-time neighborhood links. No independent trips have been
verified. A successful engineering training run over histogram features
does not establish skiing action quality or underwater ecology expertise.

**Do not assume** 232 independent training examples, 560 verified
outings, or equal representation of ski/bike/snorkeling. We need
actual source durations, capture periods, label coverage, resolution,
codec, frame rate and prior human feedback before budget allocation.

## Run locally, no SSD source reads

From a refreshed Klip worktree, with your private workspace mounted:

    git fetch origin
    git switch --detach origin/main
    export WS="$HOME/AdventureAI-clean.QJnopt"

    python scripts/klip_moment_archive_profile.py \
      --workspace "$WS" \
      --judge-report "$WS/reports/ktide-judge-pilot-retry-001/proposals.json" \
      --pilot-sources 24 \
      --coarse-seconds 10 \
      --dense-seconds-per-source 30 \
      --dense-fps 8 \
      > "$WS/reports/kmoment-archive-profile-v1.json"

The optional judge report may be the newer structured retry report if
one was created. Omit --judge-report if it is missing. The program
only reads the SQLite catalog in read-only/query-only mode and, if
supplied, an existing private judge JSON. It writes to stdout;
the shell redirection alone creates the JSON report.

Avoid copying the full JSON to public issues or pull requests:
it includes private source SHA identifiers and capture month metadata.
No source paths, GPS values, probe errors, arbitrary metadata strings
or user-authored descriptions are exported.

### Short report

    python - <<'PY'
    import json,os
    from pathlib import Path
    p=Path(os.environ["WS"])/"reports/kmoment-archive-profile-v1.json"
    d=json.loads(p.read_text())
    print("Sources:",d["archiveSourceCount"])
    print("Known video hours:",d["duration"]["totalHoursKnown"])
    print("Median duration(s):",d["duration"]["medianSeconds"])
    print("P90 duration(s):",d["duration"]["p90Seconds"])
    print("Known decimal GB:",d["sourceStorage"]["totalDecimalGBKnown"])
    print("Catalog activity (NOT verified):",d["catalogActivityUnverifiedCounts"])
    print("Sport hints (NOT verified):",d["candidateSportRoutingUnverifiedCounts"])
    print("Capture years:",d["captureMetadata"]["sourcesByCaptureYearUtc"])
    print("Probe gaps:",d["metadataGaps"])
    print("Coverage:",d["existingEvidenceCoverage"])
    print("Pilot mix:",d["pilotPlan"]["pilotCandidateCountsBySport"])
    print("Coarse frame budget:",
          d["illustrativeComputeBudgets"]["frameReadsIfAllValidSourcesUniformlySampled"])
    print("Maximum pilot dense frames:",
          d["illustrativeComputeBudgets"]["maximumPlannedDensePilotFrames"])
    PY

## Interpretation and decisions

- **Video hours/GB:** measured only from currently cataloged source
  metadata; not a measurement of what is physically on the SSD today.
  Invalid or missing durations/sizes are excluded with separate counts.
- **Capture years/months:** a description of source metadata, not a
  recording rate estimate, local timezone, validated outing count or
  projection of the next five years.
- **Camera:** currently stored metadata includes width, height,
  codec, average frame rate and presence of audio. A camera device
  model, mounting position, filmer intention, GPS, and frame-level
  video quality are *not determined* by these fields. Image orientation
  does not establish whether video is helmet POV or handheld.
- **Catalog activity:** import-time category, *not independently
  verified*. Recognized ski/bike/snorkeling values can help a
  candidate pilot sampling strategy but cannot become ground-truth
  labels without review.
- **Judge hints:** optional, previous verified-source model proposals
  only. Never override a known catalog category, and never convert
  them into owner-confirmed sport, trip or training truth.
- **Existing semantic providers:** counts of active provider metadata
  rows, *not* checksum re-validation of feature receipts. Use
  ktide_preflight for the latter.
- **Human review counts:** latest example and pairwise comparisons
  already present in SQLite, not a complete measure of verified events.
- **Frame-read budgets:** planned number of sample observations at
  chosen coarse interval and dense pilot fps. They are NOT estimates
  of GPU runtime, disk read GB, model FLOPs or generated output quality.

## Pilot selection mechanics

The planner proposes up to --pilot-sources distinct source identities.
It uses deterministic seed-controlled round-robin selection across
catalog activity or prior judge's sport *hints* (ski, bike, snorkeling,
unknown); it promotes diversity of source capture month and duration
class within each category.

Every candidate retains:
- source content SHA, capture month, metadata duration;
- candidateSport and its provenance (catalog metadata, model
  hypothesis, or unknown);
- an explicit unverified outing flag, ownerConfirmedActivity=false;
- captureStyle=unknown_not_observed;
- selection reason = metadata diversity, NOT an excitement score.

A source from an unknown activity remains useful, because discovery
must include examples the previous judge missed and quiet controls.
All groups need independent confirmation before held-out claims.

The audit is repeatable with the same catalog and seed. It never
accesses raw footage or mutates existing annotations.

## Decision matrix for the *next* experiments

| Measured input | Engineering decision |
| --- | --- |
| Few confirmed sport labels | Prioritize reviewer-confirmed coarse labels, then expand routing |
| Most sources with ambiguous camera type | Evaluate POV/handheld classifier on a labeled sample |
| Short clips dominate | Preserve onset/outcome with dense short-segment windows |
| Long clips dominate | Use sparse global passes plus adaptive local densification |
| Mostly 4K/60 or high-fps recordings | Decode low-resolution proxies in early stages; return to originals only for selected events |
| Many same-day recordings | Propose context neighbors; NEVER treat calendar days as independently verified outings |
| More verified ski examples than marine | Share backbone; lightly tune specialists with class-aware sampling |
| Poor temporal connectedness | Increase sampling density before fitting a larger state model |
| Few pairwise human choices | Gather 2-way editorial judgments, don't train a large personalized ranker |

### Proposed experimental hierarchy

**Shared, frozen backbone first:** compare frozen image-encoder
features and a video encoder on identical verified clips. Use old
K-TIDE as a temporal baseline and a simple GRU as the first
state-space control. Track performance and compute per decoded minute.

**Sport-specific feature experts second:** skiing focuses on terrain
shape, equipment and apparent turn/run continuity; biking on
obstacles, approach, takeoff, contact and exit; snorkeling on
ecological context, animal-like tracklets, subject readability and
color/visibility. Do not infer hidden joints, exact speed, species
or filmmaker intention from a camera-only view.

**Human preference model last:** use actual saved pairwise decisions to
fit small hierarchical sport-conditioned ranking heads. Separate
technical quality, action relevance, scene novelty, personal meaning
and recoverable edit quality.

**Evaluation must include controls:** quiet footage, mis-aimed clips,
duplicate/similar recordings, visually beautiful ordinary scenes and
rare poor-quality encounters. Score source-factual accuracy, event
boundary completeness, unsupported claims per description, retrieval,
false-skip rate and blinded editorial preferences.

## Suggested promotion gates

**A. Coverage:** Measure all cataloged source hours and codec/fps mix,
identify metadata gaps, record an owner-verified sport and capture-style
subset across distinct, owner-confirmed outings.

**B. Temporal baseline:** Evaluate frozen SigLIP2, temporal deltas,
current K-TIDE and simple GRU on the same source and timestamp
splits. No historical checkpoint changes.

**C. Specialty pilots:** Compare ski/bike/marine experts separately
on representative clips, requiring a gain in grounded event accuracy
or scene usefulness over the shared baseline at matched inference cost.

**D. Preference loop:** Use independent outing-held-out comparisons,
abstentions, missing-value awareness and a random low-scoring control
audit before ranking/auto-skipping any source.

**E. Archive scale:** Add incremental cache invalidation and new-source
scheduling only after profile coverage and budget metrics are known.
No automatic deletion, original replacement, auto-publishing, or
private location disclosure.

This stage addresses inventory and experimental design, not dense
scene inference. The K-MOMENT scene dossier spec covers later
event graphs, grounded narratives and reversible editing.

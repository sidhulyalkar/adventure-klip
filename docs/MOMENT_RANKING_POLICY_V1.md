# Klip ranking v1: discover, learn, and share different moments

## Motivation and observed failure mode

The user's first real ray-like snorkeling encounter produced overlapping
`marine_mammal` and `fish` evidence cards covering approximately
9–45 seconds. This is good **discovery** but not reliable organism identity:
rays are fish, not mammals, and the frame alone cannot distinguish a
particular eagle/manta species. OWLv2 prompts and object labels are
uncalibrated proposals, not biodiversity observations.

The key ranking counterfactual:

- A crystal-clear view of familiar sand may be pretty but unremarkable.
- A slightly murky **human-verified unusual ray encounter** can be much
  more memorable, even if its average image quality is poorer.
- A nearly invisible potential animal may be worth **investigating**, but
  should not be auto-proposed as an audience-ready public highlight.
- A false detector label must not receive a rare-species boost, even if
  geographic occurrence records suggest the species is plausible.

Thus, clip "value" is intrinsically multi-objective. The three outputs are:

1. **Discover/investigate**: inspect under-sampled, potentially novel
   episodes, unknown objects and unclear outcomes. Uncertainty increases
   *investigative* priority, not biological certainty.
2. **Learn/revisit**: evidence-backed technical/observation value.
   Ski turns, bike approaches and snorkeling camera framing require
   temporal review; strong motion alone is not athletic achievement.
3. **Share shortlist**: observable significance, novelty, visual subject
   visibility, narrative completeness and overall image quality.
   Human approval is still required before publishing.

## Ranking policy, v1

`moment_ranking.py` implements a bounded, deterministic review policy.
Scores are **heuristic** and must not be presented as calibrated
probabilities, species certainty or technique measurements.

Signals range from 0 to 1, or **null** when not assessed:

| Signal | Meaning |
|---|---|
| `eventSignificance` | Qualifying evidence of a memorable action/encounter |
| `visualNovelty` | New visual event versus the reviewed personal archive |
| `subjectVisibility` | How easily a human viewer sees the subject |
| `imageQuality` | Clarity, exposure, framing and compression |
| `narrativeCompleteness` | Enough approach, action and aftermath |
| `learningValue` | Evidence-backed improvement opportunity |
| `investigationPotential` | Expected value of additional review/decode |
| `uncertainty` | Explicit unresolved contextual evidence |
| `verifiedTaxonRarity` | Rarity ONLY AFTER expert/user verified species ID |

The ranker never generates numeric quality measurements from detector labels
or uncalibrated embedding similarities. A species rarity field is ignored
unless `taxonomyStatus=verified_species`. Verification here is a **provided
annotation**, not something the ranker independently proves. A detector
proposal like `marine_mammal` is not acceptable rarity provenance.

Unknown signals remain `null`. Sharing review requires assessed
significance, visibility, image quality and narrative completeness, and
visibility >=0.35. That threshold is a preliminary review gate, not an
empirically calibrated cutoff; review can override *editing choice* but
should not claim more evidence than exists. Nothing is automatically
approved, exported or published.

### Competing sports

The same numeric event-significance scale is NOT currently calibrated
across skiing, biking and snorkeling. The shortlist alternates sports
with available reviewed signals (round-robin); within each sport it uses
the appropriate separate score. When enough reviewed comparisons exist,
train sport-conditioned utility models and measure cross-sport agreement,
instead of rewarding raw camera movement.

Temporal overlap >60% of the union within the same asset suppresses
duplicate placement in one shortlist, but never deletes any input
candidate. Group-specific wildlife labels are NOT treated as distinct
animals by this rule.

### Overlapping inconsistent marine labels

`event_context._context_windows` now merges conflicting group hypotheses
ONLY when they have strong bounding-box overlap at the **same sampled
frame timestamps** (one IoU>=0.87, or at least two overlaps>=0.58 with a
max IoU>=0.65). Mere overlapping 9–45 s review windows are insufficient
because there might be several animals in the scene.

Consolidation produces `group=conflicting_labels_unverified` and retains
all original labels and provider provenance. It does NOT infer that one
animal was present. Co-visible but spatially distinct objects remain
separate cards. Re-run `event-context` into a NEW output folder to use
this improved visual grouping; existing local reports are immutable.

## Offline CLI

Rank an existing event manifest without decoding the source again:

```bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/moment-ranking-policy-v1
source .venv/bin/activate
python -m pip install -e '.[dev,semantic,images]'

export WS="$HOME/AdventureAI-clean.QJnopt"
EVENT_MANIFEST="$WS/reports/ray-event-context-v1/manifest.json"

test -f "$EVENT_MANIFEST" || { echo "Point EVENT_MANIFEST to your actual generated manifest"; exit 1; }

klip --workspace "$WS" rank-moments \
  --input "$EVENT_MANIFEST" \
  --output-dir "$WS/reports/moment-ranking-unscored-v1"

open "$WS/reports/moment-ranking-unscored-v1/review.html"
```

**Expected first result:** existing event packets contain embeddings,
detector labels and phase images, but do NOT contain established subject
visibility, image quality, event significance or verified animal rarity.
The system therefore returns **unscored review cards**, not fake
confidence scores.

To add your **actual** reviewed measurements, create a separate JSON list
with objects like:

```json
[
  {
    "momentId": "FULL_ASSET_SHA256:event-context-01",
    "humanReview": "keep",
    "taxonomyStatus": "unverified",
    "evidenceNotes": ["Ray-like subject visible across source window; exact taxon unconfirmed"],
    "signals": {
      "eventSignificance": 0.90,
      "visualNovelty": 0.85,
      "subjectVisibility": 0.65,
      "imageQuality": 0.43,
      "narrativeCompleteness": 0.77,
      "learningValue": 0.70,
      "investigationPotential": 0.80,
      "uncertainty": 0.40
    }
  }
]
```

**These numbers are illustrative only, not assessments of your uploaded
video.** Inspect source footage, correct the momentId to one actually in the
manifest, and record reviewed features before using this example.

```bash
klip --workspace "$WS" rank-moments \
  --input "$EVENT_MANIFEST" \
  --reviews "$WS/reports/your-reviewed-moments.json" \
  --output-dir "$WS/reports/moment-ranking-reviewed-v1"
```

The command never redecodes video, calls model inference, edits source
media, or changes catalog labels. It writes a bounded private JSON report
and local HTML triage summary. Re-run with a NEW output directory.

## Large archive plan

**Stage 0: Safe indexing/session context.** Keep originals immutable;
record source SHA, timebase, duration, camera metadata, session association
and asset provenance. Full-hash when necessary; avoid gratuitous repeated
full-file I/O between downstream stages once integrity is assured.

**Stage 1: Global cheap coverage.** Use existing histograms, sparse
sport classification, low-resolution embeddings and cheap audio/motion
proxies across all 232 assets. Segment activities within each video
instead of deriving habitat from the color blue. Build an index of
candidate intervals and gaps. No rare animal requires blue water.

**Stage 2: Sport-specialized discovery.** Maintain explicit diverse
exploration probes even when cheap scene models classify regions as
uninteresting. For snorkeling: sandy bottom, coral, kelp, animal-like
regions, visibility/lighting and approach/aftermath. For ski: lift,
traverse, sustained descent, observed turn sequences, apparent jump/drop.
For bike: transfers, terrain, riding sequences, berms/rocks/obstacles.
No one-frame action or athlete skill claims.

**Stage 3: Targeted visual verification.** Use local OWLv2 in small,
checkpointed batches on promising or uncertain windows. Preserve partial
results. Cluster co-located contradictory detector hypotheses into one
review object, without claiming biological identity. Revisit ±seconds
to identify temporal continuity; avoid decoding the entire collection.

**Stage 4: Event representation.** For each candidate record phase-specific
vectors, metadata/provenance, temporal segment context, subject box/track,
visibility and quality features, and an uncertainty ledger. A meaningful
moment is an **event plus surrounding context**, not a one-frame caption.

**Stage 5: Triage and human reviews.** Rank for discovery, learning
and sharing separately. Include near misses, unknowns and random intervals
in the benchmark so recall can be estimated. Build a held-out
sport/session-balanced evaluation set; measure event recall, precision@K,
NDCG, human keep-rate, budget per discovered event, clip boundary error
and calibration of any learned scores.

**Stage 6: Personalized learning and export.** Only after enough
recorded pairwise preferences, learn sport-conditioned utility functions.
Create narrative sequences with distinct setup/main event/recovery phases.
Never auto-publish sensitive GPS, faces or unverified animal/technique claims.

### Edge-case regression examples

- Murky, distinctive verified ray outweighs pristine ordinary sand for
  *investigation* and potentially for *sharing* when sufficiently visible.
- Rare but invisible animal remains investigable, not auto share-eligible.
- Unverified fish/mammal conflict receives zero species-rarity boost.
- Broad camera shake does not by itself become technical ski/bike skill.
- Brief animal between sparse samples is counted as a *recall miss* on
  the held-out benchmark; sampled absence is not biological absence.
- Multiple different animals simultaneously are not forcibly merged
  merely because review windows overlap.
- Repeated visuals across sessions should trigger a soft diversity
  penalty later, not remove a unique human-confirmed encounter.
- Camera waterline, sand, snow, sky and kayaking negatives are explicit
  controls for snorkeling recognition.
- A missing or corrupt final frame must preserve earlier checkpoints and
  report the unresolved gap instead of invalidating all completed work.
- Personal revisiting/learning can prefer clips that are imperfect for
  publishing, without conflating those two outputs.

This release implements **policy, provenance-aware input, opt-in ranking
and same-frame detector-conflict consolidation**. It does NOT claim the
full 232-video library has been fully processed, that automatic visual
quality labels already exist, or that the thresholds are empirically
optimized. Those are separately measurable next milestones.

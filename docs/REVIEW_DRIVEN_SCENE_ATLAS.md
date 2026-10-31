# Review-driven Klip learning: acoustic triage → semantic scene atlas → verified examples

## Purpose

Klip must not confuse camera movement, loud microphone audio, and model similarity with real maneuvers or memorable moments. Build a repeatable, measurable loop for the existing six real DJI skiing recordings and later snorkeling/biking. **No original file is moved, renamed, deleted, compressed or edited.** These commands operate on immutable references and write metadata into \`~/AdventureAI\` only.

The three complementary signals:

1. **Acoustics, cheap:** 8-kHz mono decoded audio split into 1-second windows. Version 2 records RMS, zero-crossing rate, peak, crest factor, clipping fraction, and (when NumPy is installed) spectral centroid, spectral flatness, spectral flux, and low-frequency band fraction. Useful for discovering recording transitions, microphone defects and unusual sound segments. It does NOT reliably identify skiing, marine creatures, breaths, impacts, excitement, or boring footage.
2. **Video, selective/optional:** pretrained SigLIP2 embeddings represent scene appearance. Compatible model artifacts are mean-pooled over each candidate interval. Object-label proposals from a separately run OWLv2 model and text-image scene hypotheses from the \`context\` backend enrich the same record. Distinct model versions and preprocessing are never compared. Semantic similarity is NOT recognition correctness or semantic proof.
3. **Human review, decisive:** an immutable reference (source content SHA-256 + source-relative start/end) receives a classification decision (\`confirmed\`, \`rejected\`, \`uncertain\`), an independent editorial preference (\`keep\`, \`skip\`, \`neutral\`), and a note. Every revision is appended to SQLite; the newest revision becomes active for ranking and an audit detects contradictory overlapping reviews. No media bytes are copied into the example bank.

## Install development branch

Do this once current Klip analysis has stopped. The branch is stacked on the previous open PRs.

\`\`\`bash
cd "$HOME/Documents/Projects/adventure-klip"
git fetch origin
git switch --track origin/feat/verified-exemplar-scene-atlas
source .venv/bin/activate
python -m pip install -e '.[dev,vision,images]'
\`\`\`

## Phase A: low-cost analysis without reprocessing motion

\`\`\`bash
# Only cached audio features will be regenerated if the version is new.
klip --workspace "$HOME/AdventureAI" acoustics

# Candidate review windows; if embeddings are unavailable, the output says so.
klip --workspace "$HOME/AdventureAI" scout \
  --recipe ski-highlights --provider siglip2 --top 20
\`\`\`

Before running a heavyweight video model, examine the scout report. An acoustic spike should lead to a request for visual inspection, not an automatic cut. Lack of sound is unknown, not boredom.

## Phase B: optional semantic scene and object models

\`\`\`bash
python -m pip install -e '.[semantic]'

# Begin on a single indexed asset; these can download pretrained model weights.
klip --workspace "$HOME/AdventureAI" scenes --backend siglip2 --every 3 --limit 1
klip --workspace "$HOME/AdventureAI" scenes --backend context --every 3 --limit 1
klip --workspace "$HOME/AdventureAI" scenes --backend owlv2 --every 3 --limit 1

klip --workspace "$HOME/AdventureAI" scout \
  --recipe ski-highlights --provider siglip2 --top 20
\`\`\`

**Warning:** \`--limit 1\` selects the first catalog asset, not necessarily the desired named video. A separate workspace for a single test file is safest for deterministic smoke tests. Scene/OWL models are currently broad, uncalibrated detection hypotheses, not precise tree/fish species classification.

## Phase C: review and re-review true examples

Get full source SHA-256 values via \`klip status\`. Example commands:

\`\`\`bash
klip --workspace "$HOME/AdventureAI" examples add FULL_ASSET_SHA256 \
  --start 46.0 --end 61.0 --label ski_lift \
  --decision confirmed --highlight skip \
  --note "Lift travel, not active skiing"

klip --workspace "$HOME/AdventureAI" examples add FULL_ASSET_SHA256 \
  --start 102.0 --end 117.0 --label carving_flow \
  --decision confirmed --highlight keep \
  --note "Sustained clear downhill turns"

# If further inspection contradicts a review, repeat the EXACT same label
# and timestamps with the corrected decision/note. The previous revision
# is retained and the example audit reports the new revision count.
klip --workspace "$HOME/AdventureAI" examples audit
klip --workspace "$HOME/AdventureAI" examples evaluate
\`\`\`

A \`rejected\` classification means "the reviewed interval is NOT an instance of this label." It is distinct from \`--highlight skip\`, which means "don't select it for the highlight reel." A confirmed crash might still be a worthy private review clip while being excluded from cinematic highlights. Use the separate \`klip mark\` path when you also want a time-aligned event or an explicit incident-review episode. The example bank is a versioned preference/labeling dataset and never rewrites source frames or modifies existing events.

### Strict evidence contract

- \`confirmed\` records one human's review, not independent scientific verification.
- One reviewed example may explicitly include/exclude its own interval. Similarity to other examples is only a small ranking nudge and requires at least three relevant cross-asset matches drawn from two separate source assets.
- A visually similar clip might show an entirely different maneuver or species. Similarity clusters are for browsing and diversity, not categorical identity.
- A missing semantic embedding never turns into a negative example or invented novelty score.
- The evaluation command reports held-out **recording-grouped** precision, recall, abstentions and coverage where human-reviewed samples exist; it does not claim accuracy when no data is available. Split by trip/filming day and eliminate near-duplicate leakage before any serious benchmark claim.

## Phase D: select visually diverse highlights

\`\`\`bash
klip --workspace "$HOME/AdventureAI" \
  plan --ranking context --context-policy suggest \
  --diversity visual --provider siglip2 \
  --recipe ski-highlights --style chronological \
  --seconds 90 \
  --output "$HOME/AdventureAI/exports/ski-diverse.json"
\`\`\`

This uses the existing context-aware candidate score, then a redundant-scene penalty, exact source non-overlap, source-count limits, and a duration budget. It attaches audio triage, object proposals when available, scene context when available, nearest reviewed examples, a provisional cluster ID, and fully traceable source boundaries.

Even if the model thinks a chairlift and a downhill descent look similar, reviewed \`skip\` intervals remain excluded from proposed cinematic windows. A good complete run must be kept intact through human-labelled episode boundaries, rather than splitting merely to maximize clip diversity.

## Next genuine research milestones

- A local dual-pane review player with frame-accurate annotation, pairwise A/B clip comparison, reviewer provenance and per-label confusion analysis.
- Manually label chairlift, flat traverse, jump approach/flight/landing, clean turns, tree navigation, water-surface breaks, wildlife sightings and negative controls; include multi-camera and audio-missing clips.
- Evaluate cross-trip retrieval and calibrated labels. Different camera stabilization, lighting, music and underwater housing produce domain shift.
- For object-centric wildlife and plant classification: masks/tracklets first, multi-frame taxonomy second, explicit abstention and reviewer corrections third. Scene embeddings must not be treated as a species ID.
- Pairwise preference learning once multiple independent human comparisons exist. Avoid training a large model from a few superficially similar frames.

Nothing in this increment automatically deletes, renames or uploads original videos.

# Discovery coverage audit for previously missed action-sports moments

The frozen Archive Value Study provides scored hypotheses, not full
original-video event recall. The user's initial study recorded 232 catalog
recordings, 214 judged valid and accessible at that time, 42 sources
with candidate episodes, 355 ranked windows and only 40 selected
pilot windows (36 unresolved/other, 4 snorkeling). The 172 of 214
sources unrepresented in that ranking may contain great moments:
missing proposals do NOT prove events are absent.

## Read-only exploratory audit

From the checked-out branch:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    klip --workspace "$WS" archive-discovery-audit \
      --study "$WS/reports/archive-value-study-pilot-v1/study.json" \
      --features "$WS/reports/social-rubric-balanced40-v1/feature-template.json" \
      --source-budget 32 \
      --output-dir "$WS/reports/archive-coverage-32-v1"

Outputs in a NEW workspace-local directory:
- coverage.json: per-source recorded duration validity, activity,
  cached episode counts, completed job *statuses*, selected/ranked
  membership, metadata issues and exploration tier.
- source-plan.json: a deterministic activity-balanced, 32-source
  **proposal-only** exploration roster, not a source-read authorization.

This command opens only the local workspace catalog and the frozen
study/feature JSON. It does not stat, resolve, checksum or decode
original camera files, which matters while the removable SDXC reader
is unhealthy. Completed semantic job statuses are not reverified model
evidence, and recorded source paths are not proof of accessibility.

The 18 frozen metadata-invalid sources should be investigated separately:
missing duration, malformed identity and other metadata errors require
different repairs. A metadata-invalid source is NOT a bad video or
grounds for deleting it.

## Next discovery work

1. Repair catalog duration/identity gaps from trusted metadata where
   possible; do not synthesize duration.
2. Explore source-level coverage across resolved and unresolved sports
   using previous verified embeddings, audio/motion, temporal landmarks
   and randomized source-time controls; avoid current score-only ranking.
3. Verify source identity before any new derivative; resume per clip
   via PR #67 checkpoints rather than reopening a blocked recording.
4. Blindly compare new and old proposals, with source/outing-disjoint
   heldout videos and independent random samples for missed-hit audits.
5. Train new aspect-based highlight models only on source/creator-licensed
   media and independently verified human reasons and preferences.

The research PR for action-sports reaction videos proposes distinct
expert, spectator and editor interpretations but does not grant
training rights to public YouTube content.

Success requires increased held-out human precision and discovery recall,
not more fluent captions or unverified clip scores.

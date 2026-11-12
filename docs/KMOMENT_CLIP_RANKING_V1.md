# K-MOMENT V1: Explainable clip ranking and unknown-coverage review

**Status:** Implemented source-safe, cached-evidence review-priority policy,
not trained personal ranking, verified event classification, objective quality,
or a finished video editor.

## Why next

The local 232-video archive contains 9.689 hours and ~452.678 decimal GB of
HEVC sources. Of these, 231 catalog activities are unknown. The validated
source-level Ollama judge gave structured *proposals* for just 16 sources.
The catalog audit found no saved pairwise judgments in its SQLite database.
Consequently, pretending to know the best clips by sport, or training a large
personalized preference network now, would be an unsupported claim.

**The task is two-stage:** (1) prioritize *existing candidate intervals* for
review with explicit, explainable heuristics; (2) actively explore *unlabeled
recordings*, making no claim that a sample is the highest-value part.

## Data flow

    catalog.episodes + existing derived feature receipts
                    |
      editorial_candidates(policy="suggest")
                    |
       bounded source-linked candidate windows
                    |
       K-MOMENT review ranker, fixed policy v1
                    |
      +------------------+-----------------+
      | evidence-ranked  | unscored        |
      | clip hypotheses  | archive probes  |
      +------------------+-----------------+
                    |
            human A/B/Keep/Skip
                    |
          *future* personalized ranker

This first stage uses cached derived features and episode metadata only. No
original video is opened, no new frames are decoded, no LLM is called, no
trainable ranker is fitted, no database is written, and no media is changed.
It does not automatically discard, delete, publish or trim any source.
Proposed boundaries need playback review before editing.

## What a score means

Existing editorial_candidates already has a composite score mixing detector
strength, camera motion and scene-context hypotheses. The new module avoids
taking that entire composite and adding motion/scene features again, which
would double-count cues. It instead uses originalEpisodeScore and separate,
explicitly named evidence proxies.

The initial heuristic vector consists of:

| Cue | Source | Important limit |
| --- | --- | --- |
| detector | Existing bounded episode's original detector score | Not verified action or success |
| event_kind_fit | Small explicit goal-specific hypothesis table | Event kind itself may be wrong |
| scene_context | Only multi-frame, unverified positive/negative context evidence | Image/text similarity is not calibrated probability |
| camera_motion_proxy | Cached flow/motion descriptor | Camera movement is not athlete movement or quality |
| underwater_continuity | Previously derived contiguous submersion support, if present | Not a complete wildlife encounter |
| local_review_keep | Existing overlapping local Keep record, if present | Local review record, not independently audited preference |

Goal-dependent initial weights are all listed as constants in the module.
These are **hypotheses** for review ordering. Never call them fitted,
calibrated or optimal preferences. The automatic goal router suggests
sport_action for ski/bike, nature_encounter for snorkeling and exploration
for unknown labels, with user choice able to override it.

For known cue values v_i in [0,1] and policy weights w_i that sum to 1:

    review_lower = 100 * sum_{observed i}(w_i * v_i)
    review_upper = review_lower + 100 * sum_{missing i}(w_i)
    cue_coverage = sum_{observed i}(w_i)

This range is a **heuristic sensitivity envelope**, NOT a confidence or
credible interval. Absence of a measurement is represented as unknown,
not as a reliable negative value. A model *hypothesis* can support priority
for investigation, but cannot establish an action, skill level, rarity,
personal meaning or quality score.

The following always remain unknown in V1: event phase completeness,
technical image quality, archive distinctiveness and personal meaning.
Even an apparent camera disturbance is flagged for review, not treated
as a verified crash or a low-quality athlete.

Heuristic priorities are sorted **within sport and editorial goal**, then
interleaved across sports. This gives broad coverage without claiming
cross-sport scores are calibrated or directly comparable. The planner
suppresses highly overlapping candidates and caps clips per source.

## Exploration is mandatory

At least an explicit fraction of the review packet is reserved for
metadata-stratified, *unscored* archive coverage probes. No event type,
moment score or completeness is invented for an unknown recording.
The exploration lane uses source duration and an approximate central
20-second playback window, marked as a non-event coverage sample.

If no eligible episodes exist, the output contains **zero ranked clips**
and only unscored exploration probes. This is expected and safer than
calling the 232 unexamined videos a ranked set. The next task would then
be a coarse, cached-event discovery pass and an improved scene review UI.

The local **Focused Studio** now has an explicit `Review source` selector:
standard queue (existing behavior) or `Ranked + exploration` (opt-in).
It loads only the fixed private file
`$WS/reports/kmoment-clip-ranking-v1.json` through the existing
token-protected local HTTP server. There is no implicit scoring run or video
decoding when you switch modes. Ranked hypotheses and unscored exploration
clips use the existing source-backed playback, Keep/Later/Skip and optional
manual annotation controls.

If the ranking file does not exist, the Studio leaves its standard queue
available and displays a helpful error. If the catalog changes, missing
sources or invalidated intervals are skipped. Clips explicitly kept or
skipped in the append-only local review records no longer reappear in the
ranked mode; Later remains eligible.

This integration does **not** add automatic montage creation, calibrated
cross-sport ordering, A/B preference learning or sport-verification.
Use the classic Full Studio's existing A/B comparison tool for pairwise
preferences; it has not been coupled to this ranking feed yet.

## Run the local review ranking

From the refreshed main worktree:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    python scripts/klip_rank_clips.py \
      --workspace "$WS" \
      --judge-report "$WS/reports/ktide-judge-structured-retry-001/proposals.json" \
      --goal auto \
      --limit 24 \
      --explore-fraction .35 \
      --max-per-source 2 \
      > "$WS/reports/kmoment-clip-ranking-v1.json"

To inspect coverage without publishing any source identifiers:

    python - <<'PY'
    import os,json
    from pathlib import Path
    p=Path(os.environ["WS"])/"reports/kmoment-clip-ranking-v1.json"
    d=json.loads(p.read_text())
    for k in ("sourceCatalogCount","episodeRowsInCatalog",
              "eligibleEpisodeRows","expandedCandidateWindows",
              "rankedCount","explorationCount","recommendedNextAction"):
        print(k,":",d[k])
    for item in d["rankedClips"][:12]:
        print("rank",item["rankForReview"],
              "sport",item["sportHint"],
              "kind",item["eventKindProposal"],
              "interval(s)",item["startMs"]/1000,item["endMs"]/1000,
              "heuristic lower/upper",item["priorityLower"],item["priorityUpper"],
              "cue coverage",item["observableCueCoverage"])
    PY

The private JSON includes source SHAs and some capture metadata. Do NOT
commit it. Source paths and original video bytes are never included.
CLI output is stdout only; shell redirection creates the private report.

## Next: a *learnable* ranking system

1. Connect ranked and exploration packets to the existing local Studio,
   with source-ID-safe playback, A/B comparisons and explicit feedback.
2. Collect review choices and separate factual correctness, episode
   completeness, technical quality and personal editorial preference.
3. Validate independent outing IDs. Any train/test split built from
   capture days or proposed same-day links is NOT scientifically held-out.
4. Train only a small, regularized pairwise ranker first: shared features
   plus sport-specific adjustment; compare against this fixed heuristic
   and random/novelty baselines on held-out verified outings.
5. Assess recall@K for judged high-value clips, nDCG@K or pairwise
   preference, duplicate coverage, adverse false-skips and how often
   unscored probes uncover overlooked good moments.
6. Keep scores versioned; do not overwrite any human judgment, clip
   source, edited derivative or previous report. Improve event/object
   state modeling separately from taste/preferences.

See [K-MOMENT scene dossier research](MOMENTWORTHINESS_SCENE_DOSSIERS_V1.md)
and [archive profiler](KMOMENT_ARCHIVE_PROFILE_V1.md).

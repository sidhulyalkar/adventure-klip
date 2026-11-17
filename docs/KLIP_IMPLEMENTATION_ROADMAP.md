# Klip implementation roadmap | from label guessing to meaningful video stories

**Date:** 2026-11-16. This is an ordered implementation plan, not a claim that all stages already run. Keep old study branches and frozen reports for reproducibility.

## P0 | Correct human review and preserve old evidence (in progress in this PR)

**Why:** a snorkeling scene can matter without an animal. The prior UI asked for wildlife categories and lost the human *why keep?* explanation, even when the scene had been interesting in earlier curation.

Deliver:
- Scene-first form: description, whyKeep/whySkip, sceneType, storyRole, filmingIntent, personalImportance, sceneInterest, Keep/Skip/Unsure. Animal evidence and filming ratings remain optional and independent.
- Legacy marine-review v1 exports remain readable. Frozen sample SHA/sampleId unchanged; no regeneration of the 57 proxy windows.
- Sidecar HTML upgrade for an existing study folder, without erasing old HTML or unsaved browser state (the browser's unsaved state still requires exporting first).
- Unit tests: positive scenic no-animal Keep, malicious/oversized notes, invalid enums/rating, v1 import compatibility, source ID mismatch, missing description allowed, real local review upgrade.
- Documentation, append-only logs and Claude Code handoff.

**Gate:** new scene description survives export/import and appears attached to the correct source record in a *validated report*. No model-generated guesses exposed in the blind form.

## P1 | Stop losing existing human feedback

**Why:** repeated labeling wastes expertise and may contaminate blind evaluations. Prior Keep labels and sport/nature descriptions must be discoverable in a provenance-aware ledger.

Deliver:
- Read-only adapters for selected known review exports: V2 owner moment annotations, blind calibration, highlight curation records, ski-run owner annotation and marine v1/v2.
- Merge by source SHA + source-relative overlap; record human goal, reviewer, export digest, schema version and sampling context. Do NOT auto-apply a label from a different interval, editorial goal or outing.
- Clear disagreement and unresolved-evidence queues, with explicit owner decisions to reconcile.
- Reuse reviewed examples as *candidate retrieval positives* only when label semantics match.

**Gate:** zero altered historical exports, deterministic duplicate handling, no cross-source mistaken merge, audit of inherited vs newly labeled scenes.

## P2 | Complete-scene candidate discovery over source recordings

**Why:** fixed 20s motion windows truncated a verified four-jump terrain park sequence; seven stills cannot recover what was not sampled.

Deliver:
- Source-wide coverage index across all catalog recordings and cached embeddings, explicit missing spans and invalid caches.
- Bounded sample of 8–12 representative complete recordings for temporal pilot. Include quiet scenery, multiple jumps, linking turns, long wildlife encounters, negative controls.
- Adaptive overlapping broad scene proposals, not forced 20s edits. Retain low-score control windows so missed rare events are measurable.
- Full original-duration context and source-family/outing continuity as separate hypotheses.
- Source-safe bounded extraction, SHA receipts, checkpoint/resume, deterministic split by outing, cost trace.

**Gate:** recover owner-marked first-three-jump sequence and the fourth extra without splitting or confusing runs; recover complete high-value nonanimal reef scenes; measure recall and timebase uncertainty vs owner marks. Do not rescan whole library before pilot passes.

## P3 | Verified temporal semantics and explanation

Deliver:
- Freeze a low-cost SigLIP2/scene histogram retrieval baseline and test text-image nearest neighbors from owner descriptions.
- Compare to a pretrained video-native encoder on independently labeled action and encounter windows. Use temporal context, don't let future frames leak into predictive tasks.
- Specialized event hypotheses: approach/action/landing, reef reveal, animal tracking/visibility/behavior/departure, underwater passage, scenic pan.
- Per-claim evidence: frame/time spans, model version, uncertainty, false-positive/false-negative controls.
- Story interpretation from observations as **model-proposed** text and editable narrative roles. Never fill in why the owner filmed it.

**Gate:** statistically and practically meaningful improvement in held-out recall and false animal claims compared to frozen baseline. Human review measures interpretation relevance and missing events.

## P4 | Personalized ranking and story composition

Deliver:
- Context-dependent Keep/Skip and optional pairwise A/B preferences; optional subjective scalar ratings without treating them as universal truth.
- Separate relevance score, watchability/technical quality, narrative utility and novelty, with abstention when components are missing.
- Evaluate source/outing-disjoint NDCG@K, Recall@K, pairwise agreement, calibration and error breakdown by activity and scene type.
- One complete line may yield several editable moments, but the selection engine must keep a linked sequence as an option.
- Composition optimization with duration budgets, scene diversity and meaningful transitions.

**Gate:** owner prefers ranked Top-K and composed reels over current heuristic and random/diverse controls in blinded A/B, across multiple outings. No simple category-count ranker can declare this gate passed.

## P5 | Source-quality editing and production

Deliver:
- Decode original camera PTS/audio for user-approved scene ranges, including rigorous source-to-proxy alignment and cross-file joins only with verified chronology.
- Preview variants: immersive complete scene, action-focused cut, connecting montage. If event ending is unknown, show missing context rather than cutting a landing.
- Frame-accurate local final exports, verification of no broken playback/no unintended original media writes, explicit human publication approval.

**Gate:** verified source identity, native duration/PTS alignment, no edit cuts through owner-marked action, correct audio, acceptable frame timing and final preview signoff.

## Parallel engineering practices

- Compare models on exactly the same frozen sample and source/outing split.
- Keep all experiments versioned: input receipt hashes, model key, feature schema, wall clock, data coverage, failure modes, outputs, and next decision.
- Source media on external SSD is immutable. No silent deep rescans, live upload, unreviewed deletion, auto-publishing or arbitrary previous label promotion.
- Keep production release candidate draft until tests, CI, real pilot, cost and privacy gates pass.

The first Claude Code task should be **review architecture and P0/P1 integration**, not “train a smarter model” without a verified benchmark.

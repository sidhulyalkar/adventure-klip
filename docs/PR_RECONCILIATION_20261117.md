# Adventure-Klip pull request reconciliation: November 17, 2026

**This is an inventory and merge-decision audit, not a claim that every code diff has been independently verified.**
Baseline inspected: `main@490eceadfd55ac530d8ece07dfb73ede1a2b99bc`, latest completed main CI **successful**. Snapshot: **65 open pull requests (34 draft / 31 non-draft)**. Re-fetch before acting: branch heads and CI are moving.

## Why nothing from the old stack is a safe one-click mainline merge today

- Most open PRs point at **another unmerged branch**, sometimes with alternative sibling UI designs. Their `clean` mergeability is **relative to their old feature parent**, not current `main`.
- [#28](https://github.com/sidhulyalkar/adventure-klip/pull/28) is a non-draft direct-`main` adaptive sampling PR but its live GitHub status reports `dirty` (merge conflict). Do not force that diff onto the current K-TIDE/K-MOMENT mainline.
- [#110](https://github.com/sidhulyalkar/adventure-klip/pull/110) targets `main`, but it is a draft and `dirty`; do not merge its old Studio rewrites over newer functionality.
- The [#116–#124](https://github.com/sidhulyalkar/adventure-klip/pulls) V2 action, Auto-Scout and wildlife ranking updates are stacked drafts requiring an owner-verified local preview/annotation pilot and fresh integration tests. A `clean` status against their own stacked base is not enough.
- [#82](https://github.com/sidhulyalkar/adventure-klip/pull/82) remains a draft on a large older archive stack. Main already contains different K-TIDE/K-MOMENT experiments. Its additional value must be isolated and benchmarked, not merged as a dependency bundle.
- [#130](https://github.com/sidhulyalkar/adventure-klip/pull/130) is an independent, narrow encoder correction targeting `main`, with dedicated Torch and ordinary CI gates. This is the current merge candidate **after both gates finish**.

**Do not mechanically close the remaining PRs as “already merged.”** Some contain unique source protection, object-tracking, labeling, UI or editing behavior not yet validated on `main`. Closing an ancestor can also obscure the origin of dependent work. Close only after a file-by-file comparison and recorded supersession mapping, preserving branches and diffs as evidence.

## All currently open PRs and disposition

| PR | Purpose | Base ref | State | Decision |
|---|---|---|---|---|
| [#4](https://github.com/sidhulyalkar/adventure-klip/pull/4) | Preserve review history and resume semantic analysis per frame | `feat/resumable-folder-processing` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#5](https://github.com/sidhulyalkar/adventure-klip/pull/5) | Record decoded frame PTS and conservatively track temporal object hypotheses | `feat/review-history-semantic-resume` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#6](https://github.com/sidhulyalkar/adventure-klip/pull/6) | Protect indexed DJI source folders from generated output writes | `feat/decoded-pts-object-track-hypotheses` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#7](https://github.com/sidhulyalkar/adventure-klip/pull/7) | Show per-video processing progress, decoded-frame counters, and stage ETAs | `fix/protect-original-footage-output-paths` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#8](https://github.com/sidhulyalkar/adventure-klip/pull/8) | Context-aware ski and snorkeling highlights with visual submersion continuity and acoustic evidence | `feat/progress-stage-eta` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#9](https://github.com/sidhulyalkar/adventure-klip/pull/9) | Diagnose low-value scenes and suspected incidents with evidence-linked private review overlays | `feat/context-aware-adventure-highlights` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#10](https://github.com/sidhulyalkar/adventure-klip/pull/10) | Add revisioned training examples, acoustic triage and visually diverse scene atlas | `feat/activity-quality-incident-review` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#11](https://github.com/sidhulyalkar/adventure-klip/pull/11) | Build private Klip Field Studio for interactive clip reviews and A/B comparisons | `feat/verified-exemplar-scene-atlas` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#13](https://github.com/sidhulyalkar/adventure-klip/pull/13) | Klip v0.6: One-clip screening and hierarchical virtual media Library | `feat/visual-sequence-editor` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#16](https://github.com/sidhulyalkar/adventure-klip/pull/16) | Klip 0.6.1: Cinematic, minimal Focus Studio | `feat/focus-review-hierarchical-library` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#22](https://github.com/sidhulyalkar/adventure-klip/pull/22) | Klip v0.7: provenance-aware object edge metadata and smart selective scene transitions | `feat/cinematic-minimal-focus` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#23](https://github.com/sidhulyalkar/adventure-klip/pull/23) | Prevent stalled whole-SSD scans from invalid AppleDouble and zero-duration videos | `feat/semantic-edge-transition-evidence` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#28](https://github.com/sidhulyalkar/adventure-klip/pull/28) | Adaptive POV sampling v1: budgeted source-safe semantic inspection for skiing, biking and snorkeling | `main` | Ready-marked | Reconcile: main-base conflicts with current head |
| [#29](https://github.com/sidhulyalkar/adventure-klip/pull/29) | Adaptive POV v1.1: bounded FFmpeg batch decoding with PTS-safe fallback | `feat/adaptive-pov-sampling-v1` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#31](https://github.com/sidhulyalkar/adventure-klip/pull/31) | Ecology context v1: offline marine occurrence priors and cached species retrieval | `feat/adaptive-batched-decoder-v1` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#33](https://github.com/sidhulyalkar/adventure-klip/pull/33) | Ecology v2: reviewed taxonomy crosswalk and cached marine encounter proposals | `feat/ecology-context-v1` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#34](https://github.com/sidhulyalkar/adventure-klip/pull/34) | Ecology v3: source-verified organism crop packets from cached encounter boxes | `feat/ecology-encounters-taxonomy-v2` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#35](https://github.com/sidhulyalkar/adventure-klip/pull/35) | Activity intelligence v1: safe mixed-sport classification and resumable overnight pilot | `feat/ecology-crop-evidence-v3` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#36](https://github.com/sidhulyalkar/adventure-klip/pull/36) | Activity v2: sandy seabed refinement, explicit source review, and targeted marine processing | `feat/activity-intelligence-pilot-v1` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#37](https://github.com/sidhulyalkar/adventure-klip/pull/37) | Segment Intelligence v1: sport-aware temporal states, selective boundary investigation, and wildlife overlays | `feat/activity-sandbar-refinement-v2` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#38](https://github.com/sidhulyalkar/adventure-klip/pull/38) | Event evidence v1: recover 19/20 semantic frames and embed before/during/after context | `feat/temporal-segment-intelligence-v1` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#39](https://github.com/sidhulyalkar/adventure-klip/pull/39) | Moment ranking v1: exceptional wildlife, evidence-gated rarity and balanced multi-sport review | `feat/event-evidence-recovery-v1` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#40](https://github.com/sidhulyalkar/adventure-klip/pull/40) | Archive pilot v1: one-command inventory, frozen batches and resumable cheap processing | `feat/moment-ranking-policy-v1` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#42](https://github.com/sidhulyalkar/adventure-klip/pull/42) | Archive status v1: explain 2 failed DJI jobs, recover histogram checkpoints, preserve frozen plans | `feat/archive-pilot-orchestrator-v1` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#43](https://github.com/sidhulyalkar/adventure-klip/pull/43) | Clip Value Lab v1: human-grounded value criteria and playable balanced pilot reviews | `feat/archive-status-failure-triage-v1` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#44](https://github.com/sidhulyalkar/adventure-klip/pull/44) | Archive review v1: sport-balanced candidate and control benchmark from frozen plans | `feat/archive-status-failure-triage-v1` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#45](https://github.com/sidhulyalkar/adventure-klip/pull/45) | Archive feedback v1: playable benchmark Studio, append-only human reviews and control-vs-candidate evaluation | `feat/archive-review-benchmark-v1` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#46](https://github.com/sidhulyalkar/adventure-klip/pull/46) | Fast Studio review: fix stalled DJI playback with cached H264 previews and opt-in private local AI triage | `feat/archive-review-feedback-v1` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#47](https://github.com/sidhulyalkar/adventure-klip/pull/47) | Adaptive visual judge v2: sport-context prompts, evidence rubric, conservative abstentions and paired human audit | `feat/studio-review-fast-local-judge-v1` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#48](https://github.com/sidhulyalkar/adventure-klip/pull/48) | Local triage follow-up: rank Unsure clips, isolate failed review and reuse verified previews | `feat/adaptive-local-judge-v2` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#49](https://github.com/sidhulyalkar/adventure-klip/pull/49) | Temporal second look: inspect 7 cached frames for uncertain adventure moments, with evidence and human review gates | `feat/triage-ranking-diagnostics-v1` | Ready-marked | Reconcile: historical stacked PR; compare against current main |
| [#50](https://github.com/sidhulyalkar/adventure-klip/pull/50) | Recover partial local triage: per-run exclusions, bounded frame seek fallback and clear timeouts | `feat/adaptive-temporal-second-look-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#51](https://github.com/sidhulyalkar/adventure-klip/pull/51) | Editorial quality + action evidence: manual framing defects, longer ski windows and deletion-review queue | `feat/triage-failure-recovery-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#56](https://github.com/sidhulyalkar/adventure-klip/pull/56) | Archive-wide Clip Value Study: longer action candidates, diverse shortlists and blind human controls | `feat/archive-editorial-quality-and-action-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#57](https://github.com/sidhulyalkar/adventure-klip/pull/57) | Social Action Lens v1: rights-gated social engagement preference baseline versus Klip | `feat/archive-value-study-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#61](https://github.com/sidhulyalkar/adventure-klip/pull/61) | Social Lens: repair missing-corpus UX, add rights preflight and balanced review bootstrap | `feat/social-action-lens-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#62](https://github.com/sidhulyalkar/adventure-klip/pull/62) | Social Lens temporal evidence v1: verified 4 fps proxy metrics, cache fusion and opt-in source-safe analysis | `feat/social-lens-corpus-onboarding-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#64](https://github.com/sidhulyalkar/adventure-klip/pull/64) | Blind A/B clip preferences: verify where Klip's ranking actually disagrees with human choices | `feat/social-lens-temporal-evidence-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#65](https://github.com/sidhulyalkar/adventure-klip/pull/65) | Social Lens: grounded local scene narratives plus strict post-vote A/B counter-audit | `feat/social-blind-pair-preference-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#67](https://github.com/sidhulyalkar/adventure-klip/pull/67) | P0: resumable temporal evidence and observable removable-media verification | `feat/social-scene-evidence-narratives-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#68](https://github.com/sidhulyalkar/adventure-klip/pull/68) | Research: rights-gated YouTube reaction references and multi-perspective highlight learning plan | `fix/social-temporal-interrupt-resume-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#69](https://github.com/sidhulyalkar/adventure-klip/pull/69) | Discovery: source-safe archive coverage audit before ranking or external reaction training | `fix/social-temporal-interrupt-resume-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#70](https://github.com/sidhulyalkar/adventure-klip/pull/70) | Pretraining specification: action, cinematography and observer-grounded highlight dataset | `research/reaction-highlight-evidence-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#71](https://github.com/sidhulyalkar/adventure-klip/pull/71) | Implement source-safe highlight dataset pilot with blind human annotation and multi-rater import | `spec/highlight-dataset-curation-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#72](https://github.com/sidhulyalkar/adventure-klip/pull/72) | Turn human clip reviews into safer highlight and source-context discovery plans | `feat/highlight-curation-pilot-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#73](https://github.com/sidhulyalkar/adventure-klip/pull/73) | Interactive Reel Studio: parallel local-cache assembly, cross-sport playlists and timestamped human feedback | `feat/highlight-review-to-discovery-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#74](https://github.com/sidhulyalkar/adventure-klip/pull/74) | Close the Reel Studio feedback loop with source-safe adaptive inspection queues | `feat/interactive-highlight-reel-studio-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#75](https://github.com/sidhulyalkar/adventure-klip/pull/75) | Repair partial curation pilots safely and orchestrate resumable cache-only overnight highlight jobs | `feat/reel-feedback-adaptive-queue-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#76](https://github.com/sidhulyalkar/adventure-klip/pull/76) | Reorient Klip toward full-archive highlight discovery: source-safe audit, resumable coarse scan and blind review packets | `fix/overnight-curation-pilot-recovery-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#82](https://github.com/sidhulyalkar/adventure-klip/pull/82) | Temporal Action Intelligence T0: cache-only visual change graph, source-safe CLI and qualification plan | `feat/full-archive-highlight-campaign-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#84](https://github.com/sidhulyalkar/adventure-klip/pull/84) | G1 SSD-native: zero-copy catalog recovery, volume UUID guard and resumable source scans | `feat/full-archive-highlight-campaign-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#87](https://github.com/sidhulyalkar/adventure-klip/pull/87) | Research plan: parallel archive work, temporal understanding and personal episodic learning | `fix/full-archive-source-root-preflight-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#88](https://github.com/sidhulyalkar/adventure-klip/pull/88) | G1.5: SHA-backed candidate coverage audit and source-balanced human review plan | `fix/full-archive-source-root-preflight-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#91](https://github.com/sidhulyalkar/adventure-klip/pull/91) | G2 preview workflow: qualified sport reels, evidence explanations and reviewed clip selection | `fix/full-archive-source-root-preflight-v1` | Draft | Hold: research prototype; dependent, not release-qualified |
| [#110](https://github.com/sidhulyalkar/adventure-klip/pull/110) | K-MOMENT: ranked + explore mode in local Focus Studio with safe review feedback | `main` | Draft | Hold: draft, current main conflict/qualification review |
| [#116](https://github.com/sidhulyalkar/adventure-klip/pull/116) | Klip V2: source-linked moments, smart edit variants and sport collections | `feat/verified-sport-reel-workflow-v1` | Draft | Hold: recent V2 pilot; dependent and not field-qualified |
| [#117](https://github.com/sidhulyalkar/adventure-klip/pull/117) | Klip V2: checkpointed local auto-scout for 120 uncategorized previews | `feat/klip-v2-moment-edit-collections` | Draft | Hold: recent V2 pilot; dependent and not field-qualified |
| [#118](https://github.com/sidhulyalkar/adventure-klip/pull/118) | Clarify Klip architecture and isolate a read-only SigLIP2 exemplar baseline | `feat/klip-v2-autoscout-experiments` | Draft | Hold: recent V2 pilot; dependent and not field-qualified |
| [#119](https://github.com/sidhulyalkar/adventure-klip/pull/119) | Diagnose failed Auto-Scout category contradictions without mutating completed run | `refactor/klip-clear-pipeline-and-exemplar-baseline` | Draft | Hold: recent V2 pilot; dependent and not field-qualified |
| [#120](https://github.com/sidhulyalkar/adventure-klip/pull/120) | Klip V2: blind ski/snorkel calibration with explicit human Keep/Skip | `experiment/klip-autoscout-schema-disagreement` | Draft | Hold: recent V2 pilot; dependent and not field-qualified |
| [#121](https://github.com/sidhulyalkar/adventure-klip/pull/121) | Fix 20-second truncation: source-linked ski run context and multi-feature review | `experiment/klip-v2-blind-calibration` | Draft | Hold: recent V2 pilot; dependent and not field-qualified |
| [#122](https://github.com/sidhulyalkar/adventure-klip/pull/122) | Ski event scaling: preserve three-jump lines and isolate bonus features | `experiment/klip-ski-run-context` | Draft | Hold: recent V2 pilot; dependent and not field-qualified |
| [#123](https://github.com/sidhulyalkar/adventure-klip/pull/123) | Rerank playable Top Clips with source-backed complete ski edits | `experiment/klip-ski-multiscale-event-lines` | Draft | Hold: recent V2 pilot; dependent and not field-qualified |
| [#124](https://github.com/sidhulyalkar/adventure-klip/pull/124) | Clean Top Clips: quarantine unknown/short snippets and calibrate marine wildlife ranking | `experiment/klip-full-preview-top-ranking` | Draft | Hold: recent V2 pilot; dependent and not field-qualified |
| [#130](https://github.com/sidhulyalkar/adventure-klip/pull/130) | K-TIDE correctness: exact checkpoint dataset lineage and honest contrastive coverage | `main` | Draft | Candidate: exact-lineage fix; merge only after both CI gates |

## Mainline learning architecture: what should land next

1. **Encoder trust, first.** Freeze teacher/model/dataset lineage; expose inactive contrastive objectives rather than implying they learned; enforce leak-safe trip splits and observable temporal support.
2. **Source-backed representations.** Connect K-TIDE and K-MOMENT through existing cached embeddings, with timestamps and unknown gaps. Preserve phase-level evidence rather than treating a single pooled embedding as a complete action.
3. **Low-cost human exemplar baseline.** Isolate the pure read-only matching component of [#118](https://github.com/sidhulyalkar/adventure-klip/pull/118) into a minimal independently tested `main` PR; **do not** pull its dependent Auto-Scout UI/CLI wholesale.
4. **Actual learned value.** Use trip-held-out human action/phase annotations and blind retrieval comparisons, including no-match and rare-event conditions, before adjusting ranking or training a preference head.
5. **Editor and Reel Studio.** Reconcile #110 and #116–#124 in one reviewed visual workflow; avoid replacing newer Studio assets with incompatible older branch defaults. Require a real local preview packet, then only publish explicit user-approved clip cuts.

## Merge gates (apply to each candidate)

- Fresh base: `main` head, non-draft PR, clean mergeability, reviewed changes and no unresolved review blockers.
- Passing required **macOS + Linux** CI; encoder paths additionally require K-TIDE CPU Torch smoke. Base integration build after merge must also pass.
- All video work source-immutable: no source-video writes, silent model downloads, external uploads, automatic publication, or destructive cleanup.
- No unverified action/species claims; uncertainty and unknown coverage stay distinct from negatives.
- Exact source hash, teacher revision, feature-manifest SHA, checkpoint identity, timestamp basis, and source/outing split provenance.
- Check real pilot footage **locally**, without relabeling model hypotheses as human decisions. Synthetic tests establish software correctness only.
- Preserve a rollback commit SHA and existing review revisions.

## Suggested cleanup batches

| Batch | PR families | Condition to leave hold |
|---|---|---|
| **A: scientific integrity** | #130, current K-TIDE/K-MOMENT source/provenance code | Independent CI + regression tests |
| **B: baseline retrieval** | Extract pure baseline from #118; consider #82 only as a separate unverified visual-change arm | No new dependencies on the Auto-Scout stack; cross-source/no-match tests |
| **C: adaptive evidence** | #28–#49 | Rebase/re-implement on current main, demonstrate no duplicate code and FFmpeg parity on actual HEVC/VFR |
| **D: safe review and editorial** | #4–#23 and #50–#91 | Resolve historical ancestors/alternative UI; preserve source and append-only human review data |
| **E: interactive Klip V2** | #110, #116–#124 | One coherent pilot-tested UI, human-reviewed clip intervals, no false animal/athlete classification |

This is **not** an instruction to merge five batches automatically. Each batch contains choices that require resolving contradictory and potentially superseded components. An old PR being non-draft does not override a new field-qualification requirement.

## Current limits

GitHub PR metadata, changed paths, and selected high-impact diffs were reviewed. This document does not claim to have executed private user footage, checked runtime on a DJI SSD, verified model accuracy, or reviewed every single modified line across 64+ PRs. The next maintainer should review the exact delta for every candidate before moving it from `Hold` to `Merge`.

# Adventure-Klip documentation

The top-level [README](../README.md) explains the research purpose, math and everyday workflow. This index separates **default-branch functionality** from **unmerged experiments**.

> A document may describe a feature on a specific stacked branch even when that feature is not installed by `pip install -e .` from `main`. Read the branch in its source PR and check `klip --help` for the checkout you actually installed.

## Start here

| Purpose | Guide | Who should read it |
|---|---|---|
| **The scientific question** | [Problem formulation](PROBLEM_FORMULATION.md) | Researchers, ML engineers, reviewers |
| **System components and provenance** | [Architecture](ARCHITECTURE.md) | Core contributors |
| **Safe local processing** | [Processing](PROCESSING.md) | All operators with personal media |
| **Human-review and boundaries** | [Clip trim policy](CLIP_TRIM_POLICY.md) | Reviewers and video editors |
| **Unmerged PR ordering** | [PR integration plan](PR_INTEGRATION_PLAN.md) | Maintainers |
| **Contributor expectations** | [Contributing](../CONTRIBUTING.md) | First-time contributors |

## Core video workflow and data contracts

- [Indexing and ingestion audit](INGESTION_AUDIT.md): verified import and source identity lessons.
- [Processing](PROCESSING.md): incremental stage recovery, SHA verification and FFmpeg.
- [Source safety](SOURCE_SAFETY.md): output-path protections and source immutability.
- [Semantic extraction and revision history](REVIEW_AND_SEMANTICS.md): model caches and human revisions.
- [Frame timing / tracks](ARCHITECTURE.md): event provenance and its limitations.
- [Review-driven scene atlas](REVIEW_DRIVEN_SCENE_ATLAS.md): human examples, audio triage and visual similarity.
- [Context-aware highlights](CONTEXT_HIGHLIGHTS.md): underwater continuity and ski background contexts.
- [Activity and incident review](ACTIVITY_AND_INCIDENT_REVIEW.md): safety of low-value/incident hypotheses.
- [Clip trim policy](CLIP_TRIM_POLICY.md): event-core and manual exclusion protection.

## Interface, sequence and publishing

- [Field Studio](FIELD_STUDIO.md): local review and A/B inspection.
- [Focus Review / Library](FOCUS_REVIEW_LIBRARY.md): large video-first review interface.
- [Curation inbox / Sequence editor](CURATION_INBOX_AND_SEQUENCE.md): human approvals and ordering.
- [Semantic edge transitions](SEMANTIC_EDGE_TRANSITIONS.md): edge comparisons for neighboring clips.
- [Sid's iLand integration](ILAND_INTEGRATION.md): approved discovery exports.
- [Run log and performance evaluation](PROCESSING.md#run-log-audit-and-performance-evaluation): audit without opening original media.
- [NVIDIA judge experiment](NIM_CLIP_BENCHMARK.md): explicit external upload opt-in, not human ground truth.

## Research and staged experiments

The development branches contain larger, **unmerged** code and experiments. Their presence in an issue or feature branch does not establish mainline support or scientific validation.

- [Temporal action understanding: mathematical problem and evaluation](PROBLEM_FORMULATION.md).
- [Temporal Lite PR #82](https://github.com/sidhulyalkar/adventure-klip/pull/82) and [its T0–T6 implementation contract](https://github.com/sidhulyalkar/adventure-klip/blob/feat/temporal-lite-action-graph-v1/docs/TEMPORAL_ACTION_INTELLIGENCE.md).
- [Adaptive POV PR #28](https://github.com/sidhulyalkar/adventure-klip/pull/28), followed by batched decoding PR #29.
- [Segment Intelligence PR #37](https://github.com/sidhulyalkar/adventure-klip/pull/37).
- [Temporal SecondLook PR #49](https://github.com/sidhulyalkar/adventure-klip/pull/49).
- [Temporal Social Lens PR #62](https://github.com/sidhulyalkar/adventure-klip/pull/62) and [blind preferences PR #64](https://github.com/sidhulyalkar/adventure-klip/pull/64).
- [Dataset and human annotation pilot PR #71](https://github.com/sidhulyalkar/adventure-klip/pull/71).
- [Full-archive campaign PR #76](https://github.com/sidhulyalkar/adventure-klip/pull/76).
- [Temporal Action Intelligence epic #83](https://github.com/sidhulyalkar/adventure-klip/issues/83).

## Evidence labels: use words precisely

| Term | Meaning | It does **not** mean |
|---|---|---|
| **Observed** | Bounded, timestamped measurement with source/model provenance | The explanation of that measurement is verified |
| **Proposed** | Model/rule candidate for a human to investigate | Confirmed animal, maneuver, crash or great clip |
| **Reviewed** | Human made a documented decision about a specific source span | Universal truth or reliable performance on unseen trips |
| **Qualified** | A prespecified test passed with reported datasets and failure modes | Fully generalizable to all footage |
| **Released** | Merged, installable, and regression-tested on the default branch | Scientifically validated beyond tested scope |

## Documentation maintenance rules

1. `README.md` is the stable overview, not a chronological changelog. Put individual implementation history in `CHANGELOG.md` or the appropriate feature document.
2. Keep old public doc paths when possible; hundreds of PRs and issues may link to them.
3. Mark an experimental branch explicitly and link its PR. Do not use the word *released* for unmerged code.
4. For benchmarks, give exact model revision, data split, sampling policy, timestamps, annotation provenance, missing-data policy, and negative controls.
5. Use diagrams and equations to clarify **which components exist** and **which are research hypotheses**.
6. Do not commit source video, individual-identifying footage, precise location traces, voices, review secrets, third-party copyrighted media or model weights.

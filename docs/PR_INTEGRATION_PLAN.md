# Pull-request integration and documentation consolidation plan

**Snapshot: November 6, 2026.** This inventory is a point-in-time review, not an evergreen statement about branch health. Re-check GitHub checks, reviewer comments, mergeability and changed files **immediately before** each merge.

[← README](../README.md) · [Documentation index](README.md) · [Action Intelligence epic #83](https://github.com/sidhulyalkar/adventure-klip/issues/83)

## Why the existing stack needs deliberate consolidation

At the snapshot, `main` is at `75ace55b6ac55f8ff79df3c5f83f6d1192cdd45b` and the repository has **60 open PRs**. They are **not** 60 independent, mergeable upgrades. Most are linked in branch-to-branch dependency chains; some are sibling UI approaches on a common ancestor.

Large unmerged stacks create three distinct problems:

1. Work looks implemented in a feature branch but is absent from the default-branch package.
2. A later feature can pass tests on its branch while an ancestor is conflicted or unstable.
3. README and experiment commands drift away from which version a first-time contributor actually installs.

**Policy:** An experiment on an unmerged branch is never advertised as a released `main` capability. Preserve each PR's provenance, keep branch heads immutable during reviews, and integrate in topological order only after fresh checks.

## Immediate safe queue

| PR | Goal | Observed status | Integration rule |
|---|---|---|---|
| [#2](https://github.com/sidhulyalkar/adventure-klip/pull/2) | Verified managed ingest for previously indexed footage | Ready (not draft); clean and previously passing CI | **First merge candidate**, after a final fresh check |
| [#3](https://github.com/sidhulyalkar/adventure-klip/pull/3) | Resumable processing and feature caches | Not draft, CI passed previously, but **merge conflict (`dirty`)** | Resolve conflict and re-run entire matrix, then consider merge |
| [#4](https://github.com/sidhulyalkar/adventure-klip/pull/4) | Review history and per-frame semantic resume | Clean on its feature parent | Merge only after #3 and base reconciliation |
| [#5](https://github.com/sidhulyalkar/adventure-klip/pull/5) | Decoded frame PTS and conservative object hypotheses | Clean on its feature parent | Check actual frame-timing tests and playback semantics |
| [#6](https://github.com/sidhulyalkar/adventure-klip/pull/6) | Source output protection | Clean on its feature parent | Require no-source-overwrite and symlink escape checks |
| [#7](https://github.com/sidhulyalkar/adventure-klip/pull/7) | Progress metrics | Clean on its feature parent | Confirm no changes to active job behavior |
| [#8–#12](https://github.com/sidhulyalkar/adventure-klip/pulls) | Context, examples, Field Studio, visual editor | Stacked, not independent | Merge one at a time with refreshed regression results |

The GitHub connector's immediate merge endpoint returned **403** for PR #2:
`Merging stacked PRs via this endpoint is not supported. Use the asynchronous merge endpoint instead.`
The available integration does not expose that asynchronous merge action, and repository auto-merge is disabled. **No merge was performed.** Use GitHub's supported merge UI or an authorized async merge tool, not a manual Git ref update that bypasses PR protections. This document-only PR is independent and safe to review without resolving that API limitation.

### Practical human merge sequence

1. Open [#2](https://github.com/sidhulyalkar/adventure-klip/pull/2). Recheck final diff, CI, discussion, mergeability and protected-branch requirements. Use GitHub's supported merge flow if green.
2. After #2 lands, inspect #3 against the new `main`. **Rebase/retarget only after preserving the head and understanding conflicts**. Its current `dirty` state must not be ignored because old CI was green.
3. Re-run unit, integration, FFmpeg and source-protection tests **after** conflict resolution. Then merge #3 and continue upward with #4–#12.
4. Make incremental new documentation/code PRs independent of the processing stack whenever possible; avoid forcing a 60-PR rebasing cascade.
5. Verify the latest main commit, repository CI, and installable package after each batch. Keep active SSD runs and checkouts untouched.

## Branch topology: there are forks, not just a ladder

### Foundation (mainline candidate)

```text
main
  └── #2 managed import safety
       └── #3 resumable processing  [CONFLICT AT SNAPSHOT]
            └── #4 review/semantic resume
                 └── #5 decoded timestamps
                      └── #6 output safety
                           └── #7 progress
                                └── #8 context
                                     └── #9 diagnostics
                                          └── #10 scene atlas
                                               └── #11 Field Studio
                                                    └── #12 sequence editor
```

### UI branches diverge after #12

```text
#12 visual sequence editor
 ├── #13 hierarchical review/library ── #16 cinematic focus ── #22 semantic edges ── #23 invalid-media recovery
 └── #14 focused review/library ── #15 quiet polish ── #17 trims ── #18 inspector ── #19 provenance ── #20 audit log ── #21 NIM
```

These **siblings** are not safe to merge blindly. Compare user experience, tests, and all overlapping files. Choose a coherent UI base and reconcile the other branch's features explicitly, rather than merging conflicting UX defaults or deleting helpful functionality.

### Adaptive and research extensions

```text
main ── #28 adaptive POV sampling ── #29 batched decoding
                  └─ subsequent ecology, activity, segment and review work
                         ├── #37 segment intelligence
                         ├── #49 temporal second look
                         ├── #62 / #64 / #65 social temporal and blind preference work
                         ├── #70 / #71+ dataset and human review
                         └── #76 full-archive highlight campaign [DRAFT]
                              └── #82 Temporal Lite [DRAFT]
```

This is a **conceptual roadmap**, not an assertion that every listed arrow is a direct Git branch parent. Use the actual `base.ref` metadata before rebasing. Do not assume #28 is safe to merge into an already updated `main` merely because it was initially based on the older `main`.

## PR admission and release gates

A PR is integration-eligible only after:

- [ ] Confirm current base branch and dependency PRs; no unresolved alternate-UI conflict.
- [ ] Confirm expected head SHA and that the branch did not move after review.
- [ ] GitHub mergeability is clean, required checks green, and draft mode disabled intentionally.
- [ ] PR's own tests plus the **target branch's** integration/regression tests pass after updates.
- [ ] New code preserves original media, source SHA, safe output location and timestamp basis.
- [ ] Verified source decoding and semantic inference costs do not leak into read-only UI or no-media commands.
- [ ] Public README reflects only what is installed on default branch, while experiments have explicit branch references.
- [ ] No training, media upload, or public publishing becomes enabled implicitly.
- [ ] Documentation includes how to test, what is *not* validated, model/data provenance and reproducibility.

A clean PR is **not** automatically proof that feature quality is scientifically validated. The release bar for temporal/action event models is separate from Python CI.

## Merge-method guidance

Favor preserving coherent commit ancestry when integrating stacked PRs so descendants can detect merged ancestors. A squash of an ancestor may require deliberate descendant rebase/cherry-pick reconciliation. GitHub's supported merge UI can enforce its own repository policies. Never `force` an update to `main`, synthesize a merge commit manually, or bypass checks just to close a large backlog.

After every merge, inspect each descendant's GitHub diff. If a descendant PR unexpectedly becomes a massive diff, stop and resolve the base rather than accepting it.

## Docs cleanup, without deleting history

The new [README](../README.md) is the *front door* and should remain relatively stable: problem, math, architecture, quickstart, status, roadmap. The existing per-feature documents still matter as historical implementation and safety contracts. They should remain at their current paths to avoid breaking old issue/PR links.

Use:

- [docs/README.md](README.md): curated documentation map and branch-status clarification.
- [docs/PROBLEM_FORMULATION.md](PROBLEM_FORMULATION.md): mathematical problem, scientific hypotheses, benchmark protocol.
- [docs/ARCHITECTURE.md](ARCHITECTURE.md): existing core data model.
- [docs/PROCESSING.md](PROCESSING.md): on-disk identity, job recovery, source safety.
- [docs/CLIP_TRIM_POLICY.md](CLIP_TRIM_POLICY.md): human-protected trim rules.
- [Temporal Action Intelligence spec](https://github.com/sidhulyalkar/adventure-klip/blob/feat/temporal-lite-action-graph-v1/docs/TEMPORAL_ACTION_INTELLIGENCE.md): unmerged T0–T6 implementation roadmap.

## Suggested consolidation batches

**Batch 1, correctness and safety:** #2, reconcile #3, #4–#7. Release criterion: fresh CI, source integrity, no new media mutations and successful smoke on a separate synthetic workspace.

**Batch 2, coherent review baseline:** #8–#12 plus a deliberate choice or reconciliation of the #13/#14 UI branches. Criterion: one functional default Review/Library experience, legacy access, no dropped human revisions.

**Batch 3, review and clip evidence:** #17–#23 and any alternate UI features only after rebasing and testing. Criterion: strict review guardrails and no unverified autonomous publishing.

**Batch 4, adaptive semantics and research:** #28 onward, then #37/#49 and selected follow-ups. Criterion: cache evidence provenance, benchmark controls, recorded compute and no missing-event concealment.

**Batch 5, human preference and T0:** #62–#82 as *qualified experiments*, not automatically released models. Criterion: versioned datasets, blinded comparisons, separated model hypotheses and independent human annotations.

**Do not process every batch in one merge transaction.** The goal is to reduce risk while achieving a comprehensible, installable repository.

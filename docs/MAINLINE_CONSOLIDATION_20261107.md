# Adventure-Klip mainline consolidation: 7 Nov 2026

**Purpose:** bring the experimental, heavily stacked PR work into a single
reproducible `main` release through GitHub's **supported merge mechanism**.
This is a *snapshot and release plan*, not a claim that any code PR was
merged during this audit. No Mac SSD job or raw video was accessed.

## Evidence at the snapshot

- `main` at commit `602d00ca8c5c55884272300e77ba2d2bae65b867`
  (the research-first README/docs PR #85 had already merged).
- **63 open PRs:** 41 non-draft and 22 draft.
- Only **two** open PRs directly target `main`: #2 (clean) and #28
  (conflicted). All other open PRs have base refs which are themselves
  heads of other open PRs, and must be integrated in dependency order.
- **Three merge conflicts** as reported by GitHub: #3, #12 and #28.
- Forks: #12, #42, #67, #76 and #84, each with two descendant PR paths;
  forks require branch/content reconciliation, not blind chain merging.
- Seven current open-graph leaves: #21, #23, #43, #69, #82, #87, #88.
- The **first foundation PR #2** is non-draft, reported mergeable, has
  passing PR CI at head `426c9b32236279be5e288bc9fff1d64494744efd`.
  Its GitHub review history has **no formal approval**.
- PR #3 conflicts *even against its immediate parent branch* #2, not
  merely after rebasing to current main. It needs actual conflict repair.
- PRs #84, #87, #88 and #82 are research/SSD/temporal branches still marked
  **draft** and are **not approved for automatic merging**.
- A non-draft PR is *not* necessarily code-reviewed/approved. Full
  per-PR current review decisions, exact-head check suites, repository
  protection settings and real-hardware qualification have **not** been
  audited for all 63 changes in this snapshot.
- The configured GitHub connector's immediate
  `merge_pull_request(#2, merge, expected_head_sha)` returned **403**:
  "Merging stacked PRs via this endpoint is not supported. Use the
  asynchronous merge endpoint instead." That alternate endpoint is not
  available through the current connector. GitHub repository
  `allow_auto_merge` is false. **No PR was merged** through that call.
  Do not work around the block by moving the `main` Git ref manually,
  fabricating a merge commit or rewriting another user's history.

The complete point-in-time graph is stored in
`docs/mainline-open-pr-snapshot-20261107.json`, and independently audited
by `scripts/klip_mainline_pr_audit.py`.

## Definition of an approved release change

Treat a PR as *eligible to integrate*, not automatically authorized, only if:

1. The owner explicitly approves the **specific change/diff** or the
   required real reviewer reviews show `APPROVED` (not merely "not draft").
2. Its actual immediate parent changes are already integrated into
   `main`, or it has been safely retargeted/rebased with no giant hidden
   ancestor diff.
3. GitHub reports no merge conflict. All required checks on the latest
   head and correct integration base pass, plus macOS FFmpeg/source-safety
   tests and install/CLI smoke as applicable.
4. Existing human reviews, source SHA identities, on-disk catalogs,
   source-media protection, timestamps and rights controls are preserved.
5. Experimental video analysis, model downloads, data uploads, publishing
   and source-video deletion do not turn on automatically after merge.
6. It is clear whether the change is a production capability, opt-in
   experiment, diagnostic tool, or documentation-only plan.
7. The work is reviewed with competing sibling branches; redundant UI
   choices are reconciled, not stacked blindly.

Green CI is a code integration gate, **not** scientific validation of
event recall or model generalization. "All approved changes" cannot be
defined as "all 41 non-draft PRs".

## Proposed integration trains

### Train A: source correctness and basic local pipeline

`main -> #2 -> #3 -> #4 -> #5 -> #6 -> #7`

- **A1:** Merge #2 via **GitHub's supported PR UI** with a normal merge
  commit, after reviewing its diff, checking the exact head and required
  approval rules. It fixes the source-managed import path bug.
- **A2:** Repair #3's existing merge conflict on its own branch with a
  separate checkout, retarget/reconcile its intended base, preserve
  original media, then run all tests and merge. This PR implements
  resumable processing and has the greatest initial correctness leverage.
- **A3:** Merge #4–#7 one at a time, refreshing each PR diff, base and CI
  after its parent lands. These cover reviewed history, PTS, source-output
  protection and observability.

**Exit gate:** `main` installs in a clean virtual environment and passes
synthetic source SHA, resume, FFmpeg/PTS, output-protection and
review-history tests on macOS and Linux.

### Train B: coherent human review and local editor

`#8 -> #9 -> #10 -> #11 -> #12`, then **choose** the canonical UI branch.

- #12 is currently conflicted and must be repaired before promotion.
- The two sibling alternatives are
  `#13 -> #16 -> #22 -> #23` and
  `#14 -> #15 -> #17 -> #18 -> #19 -> #20 -> #21`.
- Do **not** merge both UI heads expecting GitHub to choose the right
  default screen. Audit UI diffs, take the intended Review/Library
  experience as canonical, and cherry-pick/re-implement any unique
  source-safety or editorial features from the other branch in an
  explicitly reviewed integration PR.
- Retain prior review exports, stable editor URLs and a reversible
  `/classic` fallback where available.

**Exit gate:** UI smoke on real Chromium/WebKit if available,
editable in/out, import/export round trips, no dropped human decisions,
no generated files in original camera folders, and no implicit upload.

### Train C: adaptive sensing, activities and archival review

`#28 -> #29 -> #31 -> #33 -> #34 -> #35 -> #36 -> #37 -> #38
 -> #39 -> #40 -> #42`, followed by the reviewed `#44 -> #45
 -> #46 -> #47 -> #48 -> #49` path.

- #28 is currently conflicted with `main`: reconcile it **after**
  Train A/B, rather than try to force-merge its outdated base.
- #42 forks toward #43 (Clip Value Lab) and #44 (archive benchmark).
  The #44 branch is the ancestor of the larger reviewed-preview sequence;
  evaluate whether #43 adds unique, tested functionality before
  integrating it. It is not a free independent merge.
- Every optional model must remain explicitly opt-in. An existing
  candidate is never promoted into an observed event or verified species.

**Exit gate:** bounded decoding, stable asset and cache identifiers,
unreviewed and uncertain states preserved, source-backed previews,
source-grouped holdout, and no throughput regressions without evidence.

### Train D: advanced experimental and research branches

Later descendant chain:
`#49 -> #50 -> #51 -> #56 -> #57 -> #61 -> #62 -> #64 -> #65
 -> #67 -> #68 -> #70 -> #71 -> #72 -> #73 -> #74 -> #75 -> #76`.

This contains **draft work** beginning at #50. Treat each as an
individually qualified research/feature milestone, not as a cumulative
approved release automatically unlocked by Train C.

- #67 also forks to #69 (source-safe coverage audit), distinct from
  #68's reaction/rights research path.
- #76 forks to #82 (Temporal Lite T0) and #84 (SSD source-root guard).
  Review and independently test each against a common integrated base.
- #84 forks to #87 (personal episodic research spec) and #88
  (source-balanced candidate coverage). These are not merged.
- No raw footage, rights-gated reference media or unvalidated models
  are silently embedded in a release or uploaded to an external service.
- Keep active local SSD campaign on **one stable commit**. Do not update
  the live checkout or mutate its frozen catalog/audit while scanning.

**Exit gate:** stable source-verified campaign recovery and
timestamp-correct evaluation, sample review audit, model/weights revision
provenance, benchmark vs motion and SigLIP2, and human-feedback gates.

## How to proceed operationally

1. **On GitHub**, open
   https://github.com/sidhulyalkar/adventure-klip/pull/2
   and inspect Files changed / checks / review requirements. If
   satisfactory, merge using the GitHub-supported UI ("Merge pull
   request"). Keep the feature branch until downstream stacking is
   reconciled. Do not squash/force-rebase ancestor history casually.
2. Confirm GitHub `main` now includes #2 and its new CI is green.
3. Use a **separate** clean worktree/clone to resolve #3 conflicts. Do not
   use the checkout from which an SSD scan is currently running.
4. Refresh PR base refs and re-run CI at the new heads. Continue
   one parent at a time, with explicit acceptance evidence, not a
   monolithic "merge all" command.
5. For each batch, produce an immutable integration receipt:
   pre-/post-main SHA, PR number, method, CI URL/status, contributor
   approval, installed wheel smoke, tests, known limitations.
6. Merge the integration tracker docs/automation only through normal
   PR review; this document and the graph auditor do NOT grant
   permission to merge unreviewed drafts or conflicts.

## Reproducible offline graph check

```bash
python scripts/klip_mainline_pr_audit.py \
  --snapshot docs/mainline-open-pr-snapshot-20261107.json

python scripts/klip_mainline_pr_audit.py \
  --snapshot docs/mainline-open-pr-snapshot-20261107.json --json
```

The tool refuses graph cycles, duplicate head refs and incomplete parent
chains; it never calls GitHub to update branches. The snapshot **must be
refreshed** after every actual merge to avoid treating now-closed parents
as open or inferring the correct diff from stale base SHA.

## Release checkpoint following consolidation

Keep one `main` source-of-truth for installation and a small set of
optional research PRs. Archive only obsolete branches after their unique
work has been accounted for and with the owner's explicit approval; do
not mass-close drafts on title similarity.

On the resulting `main`, test `python -m pip install -e
'.[vision,images,dev]'`, the full `pytest` suite, wheel build/install,
`klip --help`, FFmpeg short synthetic media and source-protection
regressions. On a connected SSD, validate source SHA and resume only in
a deliberately authorized, finite pilot, never as a side effect of
`pip install`, GitHub CI, or a documentation-only merge.

Release only after verifying the code that the user will install is
exactly the same commit that passed the integration and source-safety
gates. Clearly mark temporal/preference ML as *research-stage* until
held-out outing evaluation supports claimed benefits.

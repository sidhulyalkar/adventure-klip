# Contributing to Adventure-Klip

Adventure-Klip is a **local-first, evidence-grounded research project**, not a universal action detector. The aim is to understand **how complete moments unfold** while preserving source truth, uncertainty, and human editorial decisions.

Read the [project overview](README.md), [mathematical problem formulation](docs/PROBLEM_FORMULATION.md), [documentation index](docs/README.md), and [PR integration map](docs/PR_INTEGRATION_PLAN.md) before opening a large change.

## Good contribution targets

- Motion-compensated temporal features and calibrated source/frame timing.
- Frame and video encoders evaluated on **exactly comparable** clips, held-out outings and compute budgets.
- Event phases that distinguish an observed action, a hypothesis and an unknown interval.
- Better human-review UX with preserved decisions, safe boundary proposals and explicit uncertainty.
- Source-safe media decode, reproducible caches and failure recovery.
- Open, rights-cleared example media, synthetic negatives, schema checks and benchmarks.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[vision,images,dev]'
python -m pytest -q
python -m build
```

When possible, develop against a **small synthetic workspace**. Do not run the full DJI archive, initiate model downloads, or change a checkout supporting an active long processing job.

The package is `adventure-klip`; Python imports use `adventure_intelligence` for backwards compatibility.

## Evidence and safety requirements

Every PR affecting video interpretation must document:

1. **What was measured?** Which frame timestamps, audio, motion or embeddings were actually observed?
2. **What is proposed?** Which event, class, cut, or quality label remains an unverified hypothesis?
3. **What is missing?** Unknown source gaps, corrupt input, unavailable media, inadequate sampling or uncertain model scores.
4. **What can fail?** Include false-positive negatives such as camera pans, blur, chairlifts, stationary video, bubbles, and scene transitions.
5. **How is it reproducible?** Input/source SHA-256, exact model revision, preprocessing, policy version, timestamp basis, and deterministic sample manifest.
6. **How was it evaluated?** Synthetic tests are necessary; real-event performance requires independently annotated and held-out footage, with failure and abstention rates.
7. **Who has authority?** Never silently alter human Keep/Skip, trusted event cores, user exclusions or publishing approvals.

**Source media is immutable by default.** Outputs belong in a separate workspace. New network uploads, external services, model downloads, or training on copyrighted/third-party material must be explicitly opt-in and subject to documented permissions.

Do not commit raw personal videos, bystander voices, exact location histories, review exports with identifying metadata, API keys, license-restricted datasets, or model weights.

## Pull requests and branch dependencies

Keep features small and avoid creating a new 10-PR stack without integration checks.

- Mention the base branch and prerequisite PRs. Review the current [integration plan](docs/PR_INTEGRATION_PLAN.md).
- If based on an unmerged branch, mark it as an **experiment**; do not advertise it as a released default-branch capability.
- Include test commands and explicit passes/failures. No claims of accuracy gains from synthetic tests alone.
- For UX changes, show what stays accessible and preserve existing revision history and source identity.
- For model changes, compare at least a frozen baseline, a hard negative set, and a held-out trip; report runtime and frame-decode costs.
- Do not merge a `dirty`, stale, or draft PR without resolving base compatibility and completing its review gates.
- Use Ruff where installed. Keep public documentation paths stable when renaming or consolidating docs.

Suggested PR scope: **one observable behavior, its invariant tests, and a short evaluation protocol**. Anything substantially larger should first be split into an issue and staged PRs.

## Reporting research results

A strong report contains the number of distinct sources and outings; sampling fractions; annotator counts and agreement; held-out setup; model/encoder hashes; baseline definition; actual CPU/GPU/SSD costs; uncertainty intervals; and known negative results.

Improving AI judge agreement is not proof of improving human experience. The goal is to preserve **complete observable events** and measure whether people genuinely prefer them.

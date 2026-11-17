# Claude Code handoff | scene-first Klip research and source-safe pipeline

**Date:** 2026-11-16. **Status:** review this draft PR before merge. This is an architecture/implementation review request, not authorization for a full archive scan or deploy.

## The user problem

A snorkeling review page currently presents a 20s underwater POV clip. The user previously considered that scene important. The marine-specific UI only offers animal class, behavior and filming ratings, with no explanation field and no use of existing human context. It cannot adequately express a beautiful reef reveal, visually meaningful swim, personal memory, filmed intention or a narrative transition. The user wants:

    Complete archive -> find important scenes -> explain story and action ->
    rank/sort by story and filming quality -> propose continuous/event edits ->
    owner reviews the highlighted results -> feedback improves future searches.

Not an animal classifier as the primary definition of "good."

## Frozen facts and learned mistakes

- About 232 cataloged source recordings; prior sampled V2 pilot was 120 proxies, not a full-source recall benchmark.
- Seven-frame local VLM accepted 108, schema-failed 12. Proposed ski_park for 45/45 ski clips and blur for 102/108 accepted. Treat those as uncalibrated proposals.
- The original 20s point-centered scan truncated a park run. Source expanded from 16–36s to 0–48.128s. Four owner jump markers: 11.846, 17.960, 23.714, 37.639s.
- Owner-approved main park line has jumps 1–3, fourth is bonus; standalone and connected edits played well. Do not infer that cadence-only grouping is a reliable trained action model.
- A playable Top 24 contained unknown clips, one-second fragments and overly many generic bike categories. These are review/coverage failures, not reason to delete sources.
- Marine focused on ray/fish tags and still did not have a place for a scene description. The 57-clip study is frozen and should remain reusable.
- Dense 8-window temporal pilot had 649 frames and 641 connected pairs, useful infrastructure but not demonstrated full-scene retrieval quality.
- All claims and dates must distinguish branch CI vs actual Mac/SSD execution.

## What this PR should implement and preserve

- Versioned, source-linked scene narrative: description, why keep/skip, story role, scene type, filming intent, personal importance and owner engagement 1–5.
- Preserve existing 57-clip sampleId and source preview URLs, hide unverified model guesses during review.
- Marine specificity is optional. No-animal scenic Keep is legal, and animal-confirmed Keep remains a separate tier.
- v1 marine JSON exports remain importable without silently manufacturing narrative facts. v2 exports retain all prior animal/rating/boundary fields.
- A sidecar upgrade creates review-scene-first.html beside old review.html; it must not erase in-browser unsaved labels or alter the frozen sample.
- A validated review can be merged into the existing evidence report with its human-authored description intact. No automated sentiment rating of raw notes.
- All prior outputs/reports and camera originals remain immutable. No network calls, uploads, publishing or training in this PR.

## Request to Claude: rigorous critique

1. Trace existing dataflow end-to-end from catalog -> 120 moment plan -> seven-still scout -> review export -> source-backed Top Clips gallery. Identify where owner Keep/description context is currently lost. Point to precise files/functions/tests.
2. Inspect legacy adapters: marine v1/v2, moment annotations v2, blind calibration, highlight curation and ski-run annotations. How can an append-only evidence index deduplicate by source SHA + interval + editorial goal, while retaining conflicts and provenance?
3. Critique the new UI for hierarchy, accessibility, import/export reliability, long notes, partial export, malformed JSON and XSS. Ensure scenic/no-animal Keep remains valid.
4. Propose a typed scene/event/observation data contract that makes long scenes and short moments coexist; don't assume each 20-second window represents a complete scene.
5. Design a full source-timeline discovery benchmark: 8–12 complete representative recordings; controls; independent outings; source coverage; event recall; rarity; animal visibility; edit completeness; explanation quality.
6. Decide which model components can reuse SigLIP2, cheap scene observations, or the dense K-MOMENT cache; justify video-native model and tracking only where temporal evidence makes them necessary.
7. Write a testable implementation plan, precise CLI contracts, failure/resume cases and sensible performance budgets. Flag costs and unverified assumptions.
8. Do not invent real-media test results, train a model automatically or merge/deploy the draft branch.

## Recommended next isolated PRs (in order)

**A. Human evidence index.** Read-only import of selected old review exports, support multiple schemas, SHA + interval/outing matches and disagreement flags. Test that older Keep labels are discoverable without polluting blind labels.

**B. Source-wide coverage report.** Build a reproducible interval manifest showing what is and isn't known about all source recordings; no full data reread or silent "complete" claim.

**C. Continuous scene candidate retrieval.** Across a small labeled set of *whole source recordings*, use cached embeddings + cheap temporal observations. Keep quiet/control segments to measure missed highlights; compare with fixed-window baseline.

**D. Human preference and story ranking.** Test text-image retrieval from human-authored scene descriptions; support scene-interest and editing-role labels. Evaluate against held-out human choices, including visually beautiful nonanimal scenery, not just animal tags.

**E. Draft reel validation.** Preserve complete park runs and underwater encounters, optional short edits, robust source PTS/audio, and owner A/B signoff.

## Accept/reject checklist

- [ ] Current 57-clip study can open a new scene-first sidecar without changing its media or manifest.
- [ ] Owner can Keep a scenic underwater scene with no animal and describe why.
- [ ] Old v1 review JSON imports and exports to v2 without losing original judgments.
- [ ] Scene descriptions round-trip and remain attributable to the correct source SHA/record ID.
- [ ] Invalid notes, unknown review IDs, inconsistent source hashes and incorrect bounds fail closed.
- [ ] Model guesses cannot appear as owner descriptions or animal-verified facts.
- [ ] Synthetic tests, JavaScript syntax validation and full CI matrix pass.
- [ ] Report explicitly distinguishes local real-media testing from CI.
- [ ] No private video, review exports or absolute personal directories committed.
- [ ] No merges, deployments, model training or broad SSD rescans.

See the dated research ledger in ../logs/experiments, the scientific roadmap and the current branch changes. When suggesting improvements, show their effect on scene recall, story relevance, label reuse or output quality, not merely architectural elegance.

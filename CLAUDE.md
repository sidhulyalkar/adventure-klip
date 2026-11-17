# Claude Code / Klip working contract

Read these first in order:

1. [Research mission](docs/KLIP_RESEARCH_MISSION.md)
2. [V3 architecture and evidence contracts](docs/KLIP_ARCHITECTURE_V3.md)
3. [Experiment outcomes](logs/experiments/2026-11-16-state-and-lessons.md)
4. [Implementation roadmap](docs/KLIP_IMPLEMENTATION_ROADMAP.md)
5. [Code review / next implementation brief](docs/KLIP_CLAUDE_CODE_BRIEF.md)
6. [Current code map](docs/CURRENT_PIPELINE.md)

**North star:** discover meaningful scenes from full recordings, understand temporal/story context, rank and assemble preferred highlights, and learn from *validated* owner feedback. Wildlife, sport and quality labels are evidence, not substitutes for significance. The user should be able to describe *why* an otherwise ordinary or scenic clip is worth keeping.

## Invariants

- Original DJI media and existing reports/checkpoints must remain unmodified.
- Inspect local/private footage only on explicit user execution or after safe approval with a healthy mounted source. Don't commit video, private annotations, photos or hard-coded home paths.
- No model-scored content becomes human ground truth. Keep source SHA, time spans, schema/model version, uncertainty and sample IDs attached to every result.
- Older v1 marine labels and frozen 57-clip manifest remain valid. New review should not force wildlife presence to label a valuable reef scene.
- Use a separate branch and draft PR for each research phase. Do not merge, deploy, publish, automatically retrain or kick off a whole-archive rescan without permission.
- Claims must separate: implemented, synthetic CI passed, real-media pilot tested, owner-reviewed and full-library qualified.
- Avoid repeated blind labeling of already-reviewed videos by provenance-aware reconciliation. A similar filename is insufficient to transfer a label.
- Keep sources/recordings/outing groups isolated between train and evaluation; no leakage via adjacent windows or near-duplicates.
- Ranking should be conditional on editorial goal and story, not a sum of categorical model guesses.
- Keep scripts/checkpoints resumable, bounded and idempotent; preserve all unknown/failure review controls.

## What to run without the user's SSD

    python -m pytest -q tests/test_complete_highlight_rerank.py tests/test_scene_first_review.py

Other baseline tests may be used as relevant. CI output is necessary but not sufficient: record model coverage, real playback and source integrity separately.

## Current branch stage

The scene-first review implementation uses src/adventure_intelligence/scene_review_narrative.py and src/adventure_intelligence/marine_encounter_review.py. scripts/klip_prepare_marine_encounter_review.py can create a compatible v2 review or sidecar-upgrade an existing study. Free-text descriptions are captured and preserved; they do NOT train or drive an automatic model yet.

First critique this design, find label migration and accessibility bugs, and propose evidence-index and timeline-scanner changes with tests before expensive new scans. The user prefers substantial implementation with precise acceptance criteria, not generic model suggestions.

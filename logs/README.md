# Klip logs | append-only experiment and decision record

These files are **public/portable descriptions** of source-backed experiments, architecture choices and measured outcomes. They are not the raw artifacts.

**Never commit**: original photos/videos, complete private owner review JSON, raw frame caches, device/home directories, credentials, personally identifying notes or confidential data. The private local workspace under $WS/reports is the home for media and full data.

## Where to look

- [Historical outcomes and lessons](experiments/2026-11-16-state-and-lessons.md)
- [Within-outing value learning (2026-11-17)](experiments/2026-11-17-within-outing-value-learning.md)
- [Research mission](../docs/KLIP_RESEARCH_MISSION.md)
- [Technical architecture](../docs/KLIP_ARCHITECTURE_V3.md)
- [Prioritized roadmap](../docs/KLIP_IMPLEMENTATION_ROADMAP.md)
- [Claude Code review brief](../docs/KLIP_CLAUDE_CODE_BRIEF.md)
- [Log template](EXPERIMENT_TEMPLATE.md)

## Logging contract

Every experiment should have a stable ID and:
1. User objective and falsifiable hypothesis.
2. Exact code branch/commit, command entrypoint, model key and review schema.
3. Input cohort identity, count, source SHA receipt/checkpoint identifiers, group split and fraction of full library covered.
4. Distinguish *measured by code*, *observed in owner terminal*, *owner-reviewed*, *not inspected* and *planned*.
5. Runtime/I/O/compute if measured. Otherwise explicitly **not measured**.
6. PASS/FAIL matrix: coverage, correctness, media integrity, tests, uncertainty, output review, privacy.
7. Failure modes, human examples, resolved vs unresolved issues, go/no-go decision and next experiment.
8. Link to relevant docs or PR, not a mutable private path.

Append a new record for each experiment. Do not rewrite old observations to retroactively improve a model. Corrections should point to the old entry with a new dated entry.

The research log does not imply a model automatically retrains; only a documented, evaluated promotion step changes a deployed ranking model.

# Owner evidence ledger and review draft persistence | 2026-11-16

**Evidence level:** implemented, synthetic CI passed (754 tests). Not run on real exports or footage.

## What changed

- `evidence_ledger.py`: one append-only JSONL record per owner judgment, keyed by source SHA,
  interval, goal and export digest. Adapters for marine v1/v2, blind calibration and ski-run
  annotations reuse each schema's own validator. Outing is attached at read time from an
  explicit map and defaults to `unassigned`.
- Each record states how its interval was chosen. Windows the pipeline selected are kept apart
  from spans the owner marked; only the latter can measure discovery recall.
- Conflicts (same source, goal and interval kind, overlapping, different decisions) are
  reported for an owner decision and never resolved automatically.
- Marine review page: drafts persist in the browser's local storage per frozen sample, the
  page warns before closing with unexported work, and status changes are announced.
  The exported JSON remains the durable record.

## Not established

- How many real prior Keeps exist, or whether any real exports conflict. Run the importer
  locally against the private exports to find out.
- Adapters for moment annotations v2, highlight curation and reel feedback are not written.
- Draft persistence was checked for syntax only, not in a browser against the 57-clip study.

## Next decision

Import the real exports, read the report, then choose how full-timeline evidence is collected.

# Klip value system: design, stores and the road ahead

This is the core path from footage to "the best moments of each day". The repository
also holds about 70 older docs and 130 modules from earlier phases. Treat those as
history unless they appear below. Commands are in the
[runbook](KLIP_VALUE_LEARNING_RUNBOOK.md); results are in
[the experiment log](../logs/experiments/2026-11-17-within-outing-value-learning.md).

## The problem, stated once

Learn the owner's preference among moments **from the same day**, for each activity. The
data has two sides:

- **Owner judgments:** a few hundred, and the only ground truth.
- **Pretrained understanding:** abundant, and model output, never ground truth.

So the learned part stays small. It learns how much the owner cares about checkable
things the pretrained models can see. All of it is tested on held-out days, or trips once
there are enough, against simple baselines.

## Layers

| Layer | What it is | Producer (command) | Store under `$WS/reports/` | Unit |
|---|---|---|---|---|
| 0 Sources | Original recordings, never modified | camera, catalog | catalog.sqlite3 (outside reports) | recording (SHA-256) |
| 1 Structure | Trips → days → activity, accidental | `roster`, review app | `owner-ledgers/roster.json` | recording |
| 1 Profile | Headers, shooting mode, filming behaviour, label coverage | `atlas` | `atlas/` (derived, replaceable) | recording |
| 2 Signals | SHA check, motion, audio timeline, candidate segments, review clips | `index` (wraps discovery) | `discovery-NNN/<sha>/` | recording, segment |
| 2 Description | SigLIP 2 frames every 2 s, CLAP audio, zero-shot concepts | `index` (wraps describe) | `evidence/<sha>/` | 2 s samples |
| 2 Scene cards | Checkable facts, caption, people, animal from a local VLM | `cards` | `scene-cards/<sha>/<start>-<end>.json` | segment |
| 3 Owner evidence | Every past review, reconciled; grid and pair answers | `harvest`, review app | `owner-ledgers/*.jsonl` + read-only exports | judgment, pair |
| 4 Items and pairs | Segments with features; keep-over-skip and direct pairs | `evaluate`, `deck` | in memory | segment, pair |
| 5 Model and evaluation | Bradley–Terry head versus baselines, held-out days or trips | `evaluate` | `value-NNN/` | day, trip |
| 6 Review | Organizer, grid rounds; answers go back to layer 3 | `review` | `owner-ledgers/` | screen of 4 |

## Contracts every layer keeps

- **Identity:** everything is keyed by source SHA-256 and millisecond spans. File names
  never identify a recording.
- **Provenance:** every output carries its version, the model and prompt fingerprint
  where there is one, frame times, and the decoder used.
- **Model output stays model output:**
  - `ownerVerified: false`;
  - an abstention is never a "no";
  - control questions guard the vision-language model.
- **Resumable and idempotent:** rerunning a command continues; finished work is skipped
  by version and fingerprint, not by file name.
- **Read-only on footage and exports:** derived stores can be rebuilt. Owner ledgers are
  append-only.
- **One read per recording:** layer-2 producers that need the file run inside `index`,
  while the file is in the OS cache.
- **Label search skips derived folders:** `owner_evidence` never reads `discovery-*`,
  `value-*`, `evidence`, `atlas`, `thumbs` or `scene-cards`.

## Known design debts (in order of cost if left)

1. **Labels are biased toward one question and one kind of clip.**
   - 256 of 258 pairs are filed under "athletic skill".
   - All are on windows the old pipeline proposed from motion.
   - Mitigations: the `same_day` pair rule; random controls in cards and grid rounds;
     one question in future rounds.
2. **Pair accuracy under two pair rules is not one number.** The rules produce different
   test pairs. A fixed test set of held-out days' same-day pairs is needed before rules
   or feature families are compared by a single score.
3. **Scene cards cover only labelled segments and controls.** They can be compared on
   pair accuracy. Whole-day recall must wait until candidates are carded too.
4. **Discovery folders of 12 are a historical unit.** Readers glob `discovery-*`, so
   duplicates resolve as "first listed wins". The next step is a per-recording registry
   (like `evidence/`) that `index` writes and every reader uses. Existing folders become
   an imported view, not a move.
5. **`scripts/klip_value_experiment.py` is about 850 lines.** Split it into a package once
   the command set settles. Behaviour is covered by tests, so the split is mechanical.
6. **Activity is still mostly unknown.** 187 of 232 recordings have none, and the roster
   is unconfirmed. Every per-activity claim waits on the organizer.

## Road ahead, with acceptance criteria

| Step | Done when |
|---|---|
| Index the library | All 232 recordings scanned and described; `atlas` coverage shows 232/232; no recording read twice in `index.jsonl` |
| Organizer, then roster confirmation | Activity for every non-accidental recording, accidental marked, `status: owner_confirmed`; card and concept activity guesses shown as suggestions only |
| Scene cards on labelled segments plus controls | Abstention and control-failure rates reported. `evaluate` with and without `--scene-cards` on the same held-out days, under `--pairing same_day` |
| Fixed evaluation set | One frozen list of held-out days and their pairs, by digest; every comparison reports on it |
| One-question review rounds per activity | Rounds mix random, uncertain and unlabelled-activity segments; repeats measure self-consistency; at least 5 days per activity with scored pairs |
| Best-of-day view | Per day: a ranked, varied shortlist with reasons (facts, concepts, peak time); a skipped suggestion is recorded as feedback |
| Telemetry | What `djmd` contains on these files is documented; if it holds motion-sensor data, jump and crash timelines are added as layer-2 evidence and ablated like any other family |

Claims stay in the four tiers from the project contract:
1. implemented;
2. synthetic CI passed;
3. real-media pilot;
4. owner-reviewed, then full-library qualified.

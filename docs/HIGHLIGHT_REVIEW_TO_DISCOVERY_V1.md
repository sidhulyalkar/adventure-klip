# Klip: From human curation reviews to better discovery (v1)

This module addresses a common category error: **Skip this exact 20-second
cut** is not equivalent to **this source video contains no highlights** or
**delete the original**. It uses deliberate, transparent routing from
previously collected independent human reviews.

## Generalized review routing examples

No private user review text, choices, source IDs, trip details or personal
memories are checked into the repository. The following are **hypothetical**
examples of annotation implications, not claims about a particular user's
recordings:

- A Keep judgment on a wildlife encounter suggests checking a complete
  setup and outcome window before promoting it into a reel.
- A Skip judgment accompanied by preserve-original language indicates
  the selected cut is weak, not that the original event is worthless.
- A low-value starting segment with a comment about the rest of a run
  suggests searching later intervals of that same source recording.
- A visually blurry view with a valuable event may warrant checking
  alternative time windows instead of erasing the source.
- A deletion word in an annotation is never an instruction to delete
  media; it triggers a manual-only hold for explicit future confirmation.
- Keep/Skip and cinematic quality can disagree because personal memory,
  event consequence, technical accomplishment and visual polish are
  different objectives.

These examples must not be turned into pseudo-ground-truth labels, and
automatic interpretation of free text remains an auditable heuristic.

## CLI: make a source-linked plan without opening the SD card

The review was originally exported from a particular frozen pilot:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export PILOT="$WS/reports/highlight-curation-pilot60-v1"

    klip --workspace "$WS" highlight-feedback-plan \
      --pilot-dir "$PILOT" \
      --reviews "$HOME/Downloads/klip-curation-reviews-dcd760ce211a.json" \
      --output-dir "$WS/reports/highlight-review-next-pass-v1"

If the local pilot folder or browser download path differs, use its
actual path. The command rejects unmatched manifest SHA-256, duplicate
reviewer/interval entries, forged unavailable preview references,
invalid event observations and mismatching frozen selection roster.
It reads **only** local workspace JSON and supplied human review JSON,
no original camera paths, no model decoding or source-hash bypass.

Outputs:
- review-discovery-plan.json: each reviewed source hash and original
  start/end time, previous actual reviews and separate proposed routes;
  candidate context windows (metadata only); review rationale;
  no claim that any context clip is verified or visually observed.
- provisional-highlights.json: provisional, goal-specific human-Keep
  intervals with source hashes and times; NOT a rendered or verified reel.
- source-context-proposals.json: bounded before/after and same-run search
  windows for future verification; original-video reads not authorized.
- manual-deletion-holds.json: explicitly flagged requests requiring
  separate, conscious confirmation; no deletion machinery exists here.
- index.html: readable, private summary of provisional highlights,
  skipped exact cuts, conflicting opinions and annotation limitations.

Routing logic:
1. Keep -> provisional highlight for the specified goal; propose a
   bounded expanded context cut to confirm setup, payoff and ending.
2. Skip -> exclude **this specific interval**, not the source.
3. If reviewer explicitly says save the source despite skipping the
   cut, propose archive/preserve and potentially neighboring windows.
4. If the note says start/rest/complete run, schedule a bounded
   forward three-window inspection using source-relative 30s
   segments; this is NOT a claim that an action exists there.
5. If a valuable source is blurry/low visibility, suggest nearby
   source-time alternative views; do NOT falsely "enhance" details.
6. Delete/trash/erase in notes -> manual-only request flag. The
   command cannot delete, move or alter original camera media.
7. Disagreement -> human follow-up, not majority consensus or
   synthesized ground truth.
8. Other/unknown/uncertain activity remains unresolved. Never
   treat missing proposals, labels or previews as negative footage.

The comment-cue matcher is **literal deterministic text matching**,
not a general-purpose NLP model or semantic truth. It is useful for
making follow-ups visible; future versions should add dedicated
reviewer-controlled archive, full-run-search and discard-request
fields rather than relying on natural-language cues.

## Better retrieval after the review

Use the plan to prioritize original *source families* for further
evidence rather than generating another indiscriminate 40-window job.

P0:
- Preserve the actual four selected human highlight candidates.
- Focus on rays/fish encounters, unique underwater views and family
  memories as **different reel goals**, not a single score.
- For the skipped but valuable deeper ray encounter, examine nearby
  **verified** preview alternatives for clearer composition.
- Extend the start of the steeper ski run into a bounded next-three
  windows; check for meaningful turns, pace/terrain change,
  encounters and actual peaks before extracting anything.
- Confirm visibility and source-time stamps for kept highlights:
  peaks were null in the original submitted review export.

P1:
- Audit 172 previously unrepresented eligible source recordings from
  the frozen earlier study, with PR #69's source-safe coverage audit.
- Recover sport classification and missing durations from trusted
  local metadata, never hallucinated activity.
- Use adaptive temporal probes and verified cached embeddings to
  propose **new events** from source-time coverage, not just rank old
  episode proposals. Ground candidate confidence in observed media.

P2:
- Only once the source card is healthy and authorized, prepare new
  proxies using PR #67 resumable SHA-verified checkpointed workflow.
- Obtain blind second-observer ratings and sport-specific expert
  adjudication of source boundaries, event claims and viewpoint.
- Compare near-source expanded clips with current human-positive cuts
  at the same explicit highlight goal.
- Report top-K human acceptance, unique source/outing coverage,
  time per retained scene, event temporal accuracy, and disagreements.
- Avoid model training until enough diverse labels, a sealed
  evaluation set and actual training permissions exist.

## Security and science boundaries

No automatic classification of species, stunt completion, athletic
difficulty or footage usefulness beyond explicit human preference.
Do not delete sources based on a JSON note. No internet connection,
cloud inference, secret processing or public review uploads. All
output stays under new private workspace reports/ or exports/.

This tool neither resolves external SD card paths nor asserts that
neighboring source windows already have valid previews. The output
is a **prioritized, source-linked inspection plan**, not a rendered
reel. Extraction of the actual best clips requires access to the
underlying recordings, verified derivative media and source metadata.

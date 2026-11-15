# Klip V2: model category disagreement follow-up

The full Auto-Scout E3–E5 run attempted 120 private preview clips:
108 accepted, 12 failed. Diagnostic retry returned the same 12 errors:

    model_schema → Invalid, duplicated or cross-sport category proposal

The strict JSON schema knows all category strings but cannot enforce that
a \`ski\` activity contains only ski tags, etc. The explicit model validator
correctly rejected contradictory or duplicated tags.

**Do not loosen the production validator or turn rejected categories into
human labels.** The original failed model JSON was not saved, so no tool can
reconstruct the *exact* contradictory tag pair from a receipt alone.

This separate experiment repeats the *same* seven-still, local Ollama prompt,
only for the 12 failed previews, saving:

- whether the second reply was valid or still invalid;
- duplicate tags, cross-sport tags, other schema problems;
- an activity proposal and category hints for **review only**;
- a local offline HTML playback deck of the 12 verified preview MP4s.

Because the model can respond differently on a repeat, this only diagnoses
repeat behavior, not the exact original response. It does not change model
prompt, old checkpoint fingerprint, original receipts, labels, previews,
original camera videos, or edited reels.

## Execute from a separate code worktree

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export PILOT="$WS/reports/klip-v2-pilot-20261114-153934-88998"
    export ORIGINAL="$WS/reports/klip-v2-autoscout-20261115-130506-89529"
    export DIAG="$WS/reports/klip-autoscout-disagreement-$(date +%Y%m%d-%H%M%S)-$$"

    python scripts/klip_autoscout_disagreement_probe.py \
      --workspace "$WS" --pilot "$PILOT" --experiment "$ORIGINAL" \
      --output "$DIAG" --max-items 12

    caffeinate -i python scripts/klip_autoscout_disagreement_probe.py \
      --workspace "$WS" --pilot "$PILOT" --experiment "$ORIGINAL" \
      --output "$DIAG" --max-items 12 --run

    open "$DIAG/review.html"

If interrupted, repeat with \`--run --resume\`. Completed diagnostic receipts
in the NEW directory will be reused. The tool requires all prior original
receipts and verifies the frozen plan/source proxies and original policy hash.

For next product improvement:

1. Calibrate category suggestions against human review rather than editing
   scene prompts until all clips pass.
2. Separate broad ski/snorkel classification, candidate feature tags, and
   Keep/Skip ranking into independent evidence channels.
3. Allow source-approved clips to enter collections; do not pass raw model
   suggestions as owner annotations.
4. Compare SigLIP2 exemplar retrieval on different outings against the
   current local VLM, especially likely false-positive \`ski_park\` and
   \`blur\` classifications.

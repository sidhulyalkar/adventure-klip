# Klip Highlight Curation Pilot V1: source-safe human data collection

This is the first **executable** layer for building a multi-perspective action-sports
dataset. It does NOT discover validated events, train a model, grant licensing
rights, read originals or solve an unresponsive DJI SD card.

The design keeps frozen:
- archived raw-candidate ranking (hypotheses, not highlight truth);
- source-backed, reproducible candidate identities;
- blank/unknown action labels;
- technical and cinematic film quality separate from action outcomes;
- human-rater opinions separate from model scores and expert adjudication;
- every source without a verified outing ID in ONE conservative
  unresolved-outing group, so train/test leakage cannot silently occur.

## 1. Create a candidate and annotation packet without original reads

On the feature branch with package installed:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export STUDY="$WS/reports/archive-value-study-pilot-v1/study.json"
    export FEATURES="$WS/reports/social-rubric-balanced40-v1/feature-template.json"

    klip --workspace "$WS" highlight-curation-pilot \
      --study "$STUDY" --features "$FEATURES" \
      --limit 60 --seed 104 --max-per-source 2 \
      --output-dir "$WS/reports/highlight-curation-pilot60-v1"

This reads existing workspace-local SQLite and study/template JSON only.
The sampler includes approximately equal budgets from:

1. frozen previous 40-window pilot;
2. relatively high Klip-ranked episode hypotheses;
3. mid/low uncertainty diagnostic hypotheses;
4. randomly sampled source-time controls from source recordings absent
   from the previous ranked episode list.

Group quotas backfill when an archive lacks sufficiently distinct sources.
Sampling role and score remain in selection-report.json for later audit,
**NOT** in the blind review UI. A control is unknown, never automatically
a "bad clip." All random times use catalog duration only; none is
decoded or proven event-free by this command.

A source with invalid/missing duration is excluded and counted separately.
Klip never invents duration, activity, outcome or permission.

Outputs:
- curation-manifest.json: machine-readable blank records adhering to
  the V1 action/filming/observer/rights annotation schema.
- selection-report.json: frozen sampler seed/roles/coverage counts;
  do NOT show this during blind reviews.
- presentation.json: SHA of frozen manifest, record-to-local verified
  H.264 derivative availability and expected derivative SHA-256.
- index.html: minimal **offline blind, single-clip video reviewer**.
- media/: only private bounded SHA/receipt-verified H.264 preview
  **hardlinks** where the local same-volume filesystem permits them.
  No original videos are copied, decoded, read or linked.

The source-video original path is never stat()'d or resolved as part
of the candidate plan or output directory protection. If a preview is
missing, the review UI marks that item unavailable and prevents a
human review. The source is UNKNOWN, not a negative, and the packet
never silently generates the missing preview.

Hardlinked derived files occupy no additional video data blocks on a
normal same-volume filesystem. Existing proxy links must not be
modified; import verifies their SHA before accepting ratings.

## 2. Capture blind action and camera judgments

Open the generated HTML page:

    open "$WS/reports/highlight-curation-pilot60-v1/index.html"

Each available clip is shown without an algorithm score, sampling role,
Ollama commentary, popularity figure or reaction. The human reviewer
selects Keep / Skip / Neither / Cannot tell, observed outcome and whether
the outcome is visible, distinct technical/aesthetic/action-readability
ratings (optional 1–5), highlight objective and a short visual rationale.
The page requires a pseudonymous reviewer ID; separate reviewers should
use separate IDs. Browser controls and ArrowLeft/ArrowRight support
navigation; the player does not autoplay.

The browser does **not** silently persist private human opinions on disk
or upload them to a server. Click **Export JSON reviews** to save them.
Use **Import previously exported JSON** for manual draft recovery. The
export's frozen manifest digest prevents it from being imported into
another experiment without explicit conversion.

The UI does not yet collect full event-phase boundaries, track
annotations frame-by-frame, validate expert biographies or display
multi-angle synchronized views. Those are future modules. Ratings
are collected for explicit human review and are not an approved
training target until adjudication and rights checks.

## 3. Import independent reviewer exports

After each volunteer independently reviews the clips and exports JSON:

    klip --workspace "$WS" highlight-curation-import \
      --pilot-dir "$WS/reports/highlight-curation-pilot60-v1" \
      --reviews "$HOME/Downloads/klip-curation-reviews-EXACT.json" \
      --output-dir "$WS/reports/highlight-human-review1-v1"

Repeat --reviews PATH for up to 25 independent exported files.
The importer checks exact source packet SHA, record IDs, duplicate
reviewer/clip keys, review scale and outcome visibility. A definite
successful or failed outcome requires visible evidence. If the
preview's local SHA changes, import is refused. It opens no original
source video.

Outputs:
- curated-manifest.json: **unchanged event/filming consensus** plus
  separately preserved per-reviewer labels and reasons.
- review-qualification.json: transparent role/choice counts and
  disagreement markers. No forced consensus.

The imported human opinions are submitted by people but **not
independently expert-adjudicated**. Training permission, source hash
reverification, final action phase confirmation and frozen research
split remain unsatisfied. This importer never automatically promotes
labels or approves a training item.

    klip highlight-curation-preflight \
      --manifest "$WS/reports/highlight-human-review1-v1/curated-manifest.json"

This structural preflight still cannot independently establish
that a real permission grant exists or that any event was truly seen.

## 4. Scientific gates before any adaptation

The immediate scientific goal is *annotation feasibility*, not
any new model metric. Count actual video-ready clips, missing cache,
source diversity, unresolved activities, absent event outcomes,
disagreements and review time.

Next:
- Acquire missing preview media ONLY after the SDXC device is healthy
  or authorized intact originals are moved to healthy local storage;
  rely on PR #67 resume checkpoints and never weaken source hashing.
- Repair source duration/identity, review source/outings, and create
  more truly diverse source-time controls using the read-only
  discovery audit in PR #69.
- Add verified time-localized peak, event phase and objective shot
  coverage labels; let a sport expert adjudicate uncertain completion.
- Invite independent licensed athlete/coach, filmmaker and spectator
  reactions to the same already-licensed source footage.
- Split by confirmed source family and outing BEFORE training; hold
  out unedited clips and randomized source-time intervals.
- Run action-only, filming-aware, reaction-only, and full-perspective
  ablations. Require held-out human preference improvement plus
  correct outcome grounding and misaligned-reaction controls.

Do not use public YouTube references or public/private social
engagement statistics as ground-truth labels or training media without
the applicable independent creator, platform and underlying clip rights.
The earlier PR #68 reference catalog remains **reference only**.

## Acceptance and limitations

- At fixed seed and source catalog, assignment order is identical.
- Unrepresented source controls are represented without looking at
  SD-card original files.
- Off-camera outcomes cannot be declared definitely completed/failed.
- Missing or altered verified H.264 proxy prevents import.
- Human ratings remain separate, with multiple observers kept apart.
- All new sample records explicitly have rights pending and split
  unassigned; no training code runs.
- A cloned review packet with a spoofed original receipt is not proof
  of permission or expert truth; the issuer's real source/provenance
  must still be qualified.

The interface is an initial private research instrument. It is not yet
a complete scientific video annotation studio, nor a polished production
UI. Provenance and truthful missingness take priority over decoration.

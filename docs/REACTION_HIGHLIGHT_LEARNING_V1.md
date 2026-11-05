# Multi-perspective action-highlight learning from reactions (research v1)

Status: **source scouting, experimental design only**. Public YouTube links are
references for manual research and permission outreach, **not a training
corpus**. The first nine candidate references are in
[reaction-highlight-reference-v1.json](../research/reaction-highlight-reference-v1.json).
No audiovisual bytes, captions, reactions, voices, thumbnails, personally
identifiable viewer data or embeddings have been collected.

## Why reactions might help

The current Klip archive bottleneck is missing discovery: the user's frozen
pilot has 232 catalog recordings, 214 deemed valid and accessible *when the
study ran*, 42 with candidate episodes, 355 episode windows, and a selected
feature template containing 36 \`other\` (unresolved) windows and only four
snorkeling windows. These are user-reported counts from a **particular
frozen study** and do not imply the other 172 eligible recordings lack great
moments. Blind review sampling is not equivalent to top-highlight selection.

Reaction commentary offers a different possible supervision type:
**what specific event or transition did an observer notice, and why?**

A skateboard expert can recognize stance, trick difficulty and execution.
A casual viewer may respond to height, perceived danger or an unexpected
outcome. A comedy commentator may prefer a failed trick. A highlight is
a conditional judgment involving context, observer goals and evidence,
not a binary ground-truth category or equivalent to popularity.

## Distinct perspectives, not consensus manufacturing

| Perspective | Hypothesized explanation targets | Common confounds |
| --- | --- | --- |
| Athlete / coach | Difficulty, body control, novelty, execution, landing | Expert jargon, sport-specific exposure |
| Casual spectator | Surprise, size, clarity, suspense, humor | Exaggerated excitement, presenter reputation |
| Editor / filmmaker | Readable visual hook, continuity, payoff, pacing | Music, cutting, overlays, color grading |
| Nature specialist | Behavioral significance, unexpected encounter | Species ambiguity, anthropomorphic narrative |
| Athlete / participant | Personal achievement, conditions, emotional memory | Private knowledge not observable from frames |
| Safety analyst | Consequence, risk, near misses, prevention | Risk is not equivalent to entertainment |
| Cross-sport audience | Transfer of action salience across modalities | Different norms across sports |

The model should predict a **distribution of reasonable perspectives**
and highlight the places they disagree. Never infer a participant's
internal emotion or intent from a face or vocal tone as objective truth.
Reaction words and visible expressions are observations that require
consent and contextual interpretation.

## Research corpus design: two separate layers

**Layer A: public reference catalog.** Stores manually verified URLs,
titles, channel, sport/perspective hypothesis, and explicit
\`trainingAuthorization=not_obtained\`. Never creates local audiovisual
files or automatically fetches YouTube captions. Use only to identify
creators for possible partnerships or human research references. YouTube
view and like counts are not learned significance labels.

**Layer B: separately supplied, rights-cleared licensed media.** Do
not ingest YouTube media just because it is public, CC-labeled, embeddable,
or has a favorable training-permission status. Require relevant platform
access rights and the applicable original-footage, commentary,
music/soundtrack, included performers and model-use permissions.
If rights are not independently confirmed, the item does not enter a
training pipeline. A reaction video showing another party's footage
may require several different rights grants. Maintain an auditable
asset provenance and revocation/deletion procedure.

The [YouTube Video Third-Party Trainability API](https://developers.google.com/youtube/data/video-trainability)
can be one signal for company-specific creator settings, which default
off. It is **not an unrestricted download authorization or complete
chain-of-title proof**. Check the platform's developer terms and the
specific contractual grant before use.

Sourcing alternatives for legal pilot training: direct opt-in
creator/filmer partnerships, original camera files from rights holders,
a small set of voluntary expert annotations on the user's own video
archive, and truly permissive media with individually reviewed licenses.
Prefer original and reaction tracks supplied separately over extracting
one from a reposted compilation.

## Annotation unit

Train on individual **action intervals** (not whole reaction uploads) with
up to three synchronized streams supplied by the rights holder:

- **Action evidence**: original camera time (source SHA, start/end);
  coarse sport/activity hypotheses, image/pose/motion, scene context,
  event phases, landing/result and observable quality.
- **Reaction evidence**: commentator identity only by opt-in stable
  pseudonym, role/expertise self-report, local recording time,
  statement/transcript and its uncertain alignment to action time.
  Do not infer emotion/biometrics from a person or scrape comments.
- **Interpretation evidence**: what was *literally said*, a concise
  rationale in commentator's words, event hypothesis, whether the
  claim is supported by the visible action, importance by dimension,
  abstentions and multiple dissenting perspectives.

For each reaction use offset + potential replay/pause/cut intervals.
A high-volume exclamation may occur **after** the associated moment,
or after a replay. Do not label the speech timestamp itself as the
visual peak. Count the same original clip appearing across reaction
uploads as **one source family** for train/test leakage control.

Suggested machine-readable labels (not yet collected):

\`\`\`json
{
  "sourceFamily": "locally_pseudonymous_authorized_original",
  "actionIntervalMs": [1000, 9000],
  "reactionIntervalMs": [2500, 11000],
  "alignmentStatus": "human_verified_or_unknown",
  "perspective": "expert_athlete",
  "eventObservable": "observed | not_observed | unknown",
  "outcome": "completed | failed | partial | off_camera | unknown",
  "rationale": {
    "technicalDifficulty": "unrated",
    "visualReadability": "unrated",
    "narrativePayoff": "unrated",
    "noveltyInContext": "unrated",
    "humor": "unrated",
    "perceivedRisk": "unrated"
  },
  "support": "visibly_grounded | interpretation | unknown",
  "humanBlindPreference": "not_collected",
  "rightsStatus": "must_be_verified_before_training"
}
\`\`\`

The strings above illustrate a schema, **not evidence or synthetic
human labels**.

## Model stages

1. **Freeze independent baselines.** Klip heuristics, existing SigLIP2
   semantic trajectories, 4 fps grayscale evidence, local story judge,
   and random/time-matched controls. Do not expose reactions or
   posthoc ratings to the baseline.
2. **Event/temporal grounding.** Use a locally licensed action clip
   with time-indexed frames, audio (where licensed), optical flow and
   optional pose. Annotate setup -> initiation -> peak -> outcome.
   Seven sampled stills do not prove a landed maneuver.
3. **Interpretation proposals.** Train a small per-aspect probe or
   low-rank adapter on frozen action embeddings and authorized
   *human-authored rationale labels*. Model independent expert,
   spectator and editor heads rather than a single popularity score.
   Mask missing labels, represent uncertainty, and keep explanation
   text separate from objective event evidence.
4. **Preference alignment.** Optimize ordinal/pairwise comparisons
   **within sport and observer role**, not engagement totals. Compare
   action-only, reaction-only, combined, and context-shuffled baselines.
   Keep an explicit "neither" and "cannot tell" option.
5. **Human evaluation.** Blindly rank held-out, *source- and
   outing-disjoint* original videos, including randomized intervals,
   malformed clips, exceptional low-view moments, scary-but-failed
   actions, high-view-but-boring edits and deliberately misleading
   commentaries.

Start with a small creator-permitted pilot and stop if permissions
are absent. Model fine-tuning is not automatically warranted:
a frozen encoder and calibrated per-aspect linear/lightweight ranker
may be sufficient, faster and easier to audit. Do not train or download
anything until the rights and ground-truth gates pass.

## Success and failure criteria

Primary: improvement in **blind human pairwise choice agreement** and
source/outings-disjoint held-out top-K accepted-highlight precision,
compared with Klip's current baseline. Human annotators choose an
observer goal (best athletic achievement, best story, funniest moment,
best personal memory, best underwater encounter). Measure reliability
between annotators rather than forcing unanimity.

Secondary: discovery recall on independent random source-time audits;
time required to discover a retained clip; true vs false claims of
event completion; narrative peak localization; calibration and
abstention when outcome is not visible; unique scenes per highlight
reel; and diversity across sport and source.

Do not use absolute YouTube views, likes, presenter loudness or
watch-time speculation as gold labels. Such metrics are confounded by
audience, distribution, video length, thumbnail, platform and creator.
YouTube API restrictions may also prohibit deriving metrics from API
data. Train on cleared *content and independent judgments* instead.

Important ablations:
- Action-only versus action + reaction interpretation.
- Commentary text versus acoustic exuberance (the latter should not be
  needed).
- Expert versus novice perspectives; hard negatives and dissent.
- Randomly shifted reaction times and unrelated commentary: the
  joint model must **not** perform equally well on mismatched pairs.
- Within-athlete versus held-out-athlete and held-out-event splits.
- Equal-length source windows, with/without editing overlays/music,
  to detect contamination by publication polish.
- Clip outcome outside visible window: model must abstain.

## Suggested V0 pilot budgets (research targets, not reported results)

- 8-15 distinct **permitted** action source recordings from 2+ sports,
  preferably with paired commentary on the **same** action.
- 30-60 human-annotated event moments, 2-3 independent perspectives
  each, plus randomized/non-highlight controls.
- 80-150 within-sport blind pairwise judgments, source-clustered.
- Frozen local encoders plus a light per-aspect head before any
  end-to-end video model training.
- Require genuine rights clearance and permission to train **before**
  collecting the action or reaction files.

These pilot sizes are for designing instrumentation and testing failure
modes, not for statistically validated generalization claims.
Do not report accuracy, training costs or success until evidence exists.

## Integration with existing Adventure-Klip

The source-safe metadata audit remains independent of external training.
This work should complement, not replace:
- PR #65: independent scene stories and post-vote evidence.
- PR #67: removable-media-safe temporal checkpoints, progress and resume.
- The planned coverage audit: 172 currently unrepresented valid source
  recordings should be explored before promoting 40 selected clips as
  "the best".

New outputs should use versioned report folders and keep published
blind-human reviews frozen. Include rights provenance, source family,
split keys, annotator role and source SHA in every *permitted* training
example. Keep anecdotal reaction anecdotes in a **hypothesis layer**,
separate from measured visual evidence and human outcomes.

## Reference policies (checked November 2026)

- [YouTube Terms of Service](https://www.youtube.com/t/terms)
- [YouTube API Developer Policies](https://developers.google.com/youtube/terms/developer-policies)
- [YouTube third-party training settings](https://support.google.com/youtube/answer/15509945?hl=en)
- [Video Third-Party Trainability API](https://developers.google.com/youtube/data/video-trainability)

This is a research and technical design, not legal clearance advice.


## Run the public scouting catalog audit locally

In the Adventure-Klip repository checkout:

    klip reaction-reference-audit \
      --catalog research/reaction-highlight-reference-v1.json

This command only reads the reference JSON and returns nine scouted URLs,
counts by sport/perspective, and **zero training-eligible examples**. It
does not load media, captions, API engagement data, audio or personal
information. The command intentionally rejects attempts to change any
reference URL into an apparently approved training item.

The catalog is a seed for **human observation and permission outreach**,
not the source of any training split. Once actual permissioned source
files and clear grants exist, construct a **separate** licensed training
corpus with event-level provenance and human-verified annotations. Do not
flip the reference catalog's \`modelTrainingAllowed\` field to bypass this
restriction.

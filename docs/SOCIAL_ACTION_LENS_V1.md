# Social Action Lens v1: licensed social-video preference study versus Klip

## Research question

Can a model trained on *authorized* skiing, snorkeling, biking and action-sport
short-form videos identify useful visual/temporal factors that Klip currently
underranks in a user's unedited private action-camera archive?

**Do not use follower count, views or brand reach as ground truth for a beautiful
raw action moment.** This v1 learns a narrow association: for organic videos
from the same creator, same platform, same sport, similar duration,
similar publication era and similar age at metric snapshot, which has
a higher smoothed exposure-conditioned engagement index?

```
log((1.0*shares + 1.5*saves + 0.4*comments + 0.08*likes + 0.5)
    / (views + 100))
```

These fixed research weights are *heuristics*, not scientifically calibrated
business value or retention. Watch time, impressions, completion, replays,
caption editing, account reach, hashtag luck and algorithmic distribution
are not observed. The outcome is observational, not causal. A trained model
MUST NOT predict views, follower growth or publication success from this index.

## External data rights: two independent permissions

1. **Metadata:** a platform-approved research endpoint, an authorized
   creator export, or a licensed dataset.
2. **Video features:** independently documented permission to inspect
   the particular videos and use them to train this model. Public
   availability of a Reels/TikTok post does not by itself establish
   training permission. Store a stable pseudonymous creator ID and
   content-family ID, not personal usernames in the model artifact.

TikTok Research Tools are restricted to eligible and approved researchers
and return engagement metadata, not an unrestricted license to train on
others' videos. Meta Content Library has controlled research access.
Follow the current terms of your approved access, creator grants and
applicable law. No TikTok/Instagram scraping or downloading is implemented.

Start with 200–500 **explicitly licensed creator videos**, across many
creators, 4 sports, high and low engagement, camera types and action
levels. Sample *consecutive/random uploads* within a creator, not
merely videos described as viral. Freeze labels and a creator-disjoint
test split before any iterative tuning. If only a tiny permissioned
dataset is available, report the result as a feasibility experiment.

Where external video use permission is unavailable, perform a
metadata-only descriptive study and DO NOT train the visual head.
Ego4D/Ego-Exo4D can support temporal visual representations *only under
the specific research-data license terms*, but cannot provide social
engagement outcomes by themselves.

## Offline v1 raw-feature rubric

This first trainer needs the SAME directly observed 0..1 fields for
authorized external clips and Klip's private candidate footage:

```
subjectVisibility
framingQuality
imageClarity
cameraStability
motionCoherence
actionContinuity
eventInterest
narrativeCompleteness
visualNovelty
earlyVisualHook
```

Explicitly assign the common `action-raw-human-rubric-v1` version to
fully reviewed records. Missing/unknown fields stay null, and candidates
with missing fields **abstain**. Do not silently convert a camera-motion
heuristic, cosine similarity, uncalibrated VLM confidence or
three-frame narrative into a measured human rubric rating.

This model scores raw clips for *captured content* only. It must remain
separate from a future model for publication packaging (aspect ratio,
captions, text overlay, soundtrack, narration, cuts, pace, thumbnails,
hashtags and trends). Sound source RMS is not skiing ground speed.

The initial model is a small offline pairwise logistic head on those
features. It trains only on pairs from the same creator/platform/sport,
compares only independently edited content families, and holds out
entire creators. It never requests a GPU or external API. Training
does not imply validation against personal Klip preferences.

## Input: fully permissioned local feature corpus

Create a JSON file under a private location with
`"version": "licensed-social-action-features-v1"`. The following is
a single **illustrative** record, not a licensed real video. The
trainer requires at least six records from three creators, enough
comparable matched pairs, and records with all metric fields observed.

```json
{
  "version": "licensed-social-action-features-v1",
  "items": [
    {
      "clipId": "example_clip_012345",
      "creatorGroup": "pseudonymous_creator_001",
      "contentGroup": "unique_original_take_001",
      "platform": "tiktok",
      "sport": "ski",
      "featureMethod": "action-raw-human-rubric-v1",
      "rights": {
        "videoModelTrainingAllowed": true,
        "sourceLicense": "Replace with actual documented permission reference",
        "metadataAccess": "creator_export"
      },
      "promoted": false,
      "durationSec": 24,
      "metrics": {
        "postedDate": "2026-08-01",
        "observedDate": "2026-09-01",
        "viewCount": 8000,
        "likeCount": 420,
        "shareCount": 48,
        "saveCount": 70,
        "commentCount": 18
      },
      "features": {
        "subjectVisibility": 0.75,
        "framingQuality": 0.75,
        "imageClarity": 1.0,
        "cameraStability": 0.75,
        "motionCoherence": 1.0,
        "actionContinuity": 1.0,
        "eventInterest": 0.75,
        "narrativeCompleteness": 0.75,
        "visualNovelty": 0.5,
        "earlyVisualHook": 0.75
      }
    }
  ]
}
```

For platforms/exports without reliable `saveCount` or comparable
metrics, **do not fill missing as zero**. The initial strict training
schema will reject those rows. Before mixing different metadata
coverage, introduce distinct documented outcome definitions and rerun
the complete creator-disjoint evaluation.

Rights are currently self-attested in the manifest; this code cannot
verify a license document or downstream AI-training eligibility.
Do not treat passing schema validation as legal authorization.

## Commands (branch: feat/social-action-lens-v1)

On a clean worktree, stop a running Studio server before switching:

```bash
git fetch origin
git switch --track origin/feat/social-action-lens-v1
python -m pip install -e '.[dev]'

export WS="$HOME/AdventureAI-clean.QJnopt"
export STUDY="$WS/reports/archive-value-study-all-v1/study.json"
```

### Train, only once real rights-cleared data exists

```bash
klip --workspace "$WS" social-lens-train \
  --corpus "$WS/reports/social-reference-authorized-v1.json" \
  --seed 23 \
  --output-dir "$WS/reports/social-action-head-v1"
```

Outputs `model.json`, including coefficients, the source dataset
fingerprint, creator-disjoint held-out accuracy, and an equal-feature
baseline. A small heldout pair count is explicitly reported. Test
accuracy does not establish transfer to personal action clips.

### Generate missingness-preserving personal feature template

```bash
klip --workspace "$WS" social-lens-feature-template \
  --study "$STUDY" \
  --output-dir "$WS/reports/social-rubric-template-v1"
```

Every feature defaults to `null` and `featureMethod: "unassessed"`.
After a user or genuinely validated shared extractor has measured
ALL ten fields for a candidate, fill them and set
`featureMethod: "action-raw-human-rubric-v1"`.
Never set this flag for guessed or missing values. Save the reviewed
manifest to a *different* filename to preserve the original template.

### Compare the same exact Klip candidate windows

```bash
klip --workspace "$WS" social-lens-compare \
  --model "$WS/reports/social-action-head-v1/model.json" \
  --study "$STUDY" \
  --features "$WS/reports/social-rubric-reviewed-v1.json" \
  --top-k 20 \
  --output-dir "$WS/reports/social-versus-klip-v1"
```

Reports per-sport Top-K overlap, examples Social Lens promoted over
Klip, candidate coverage and the exact per-window score pairs.
The scores are NOT probabilities. If no features are actually
reviewed, the comparison must report zero comparable candidates,
not zero engagement or a model "loss".

## Next major model: frozen video representation, two ranking heads

- Start with **SigLIP2** (existing Klip cached still-frame embeddings).
  Add **V-JEPA 2**, or a compatible video encoder, to represent temporal
  structure and motion beyond three static screenshots. Verify
  weights/license and pin the resolved checkpoint/preprocessing ID.
- Build a temporal raw-video feature set: smooth high-speed *appearance*
  versus actual GPS speed, turns/feature execution, subject occupancy
  over time, correct mount orientation, recoverability, scene transitions,
  narrative setup/peak/outcome, and species-agnostic wildlife behavior.
- Fit a small social-engagement head on *permitted* external examples.
  Fit a separate personal-Keep head on reviewed Klip examples.
  Model packaging/edits with a distinct head; do not penalize raw clips
  for lacking a viral soundtrack or TikTok captions.
- Evaluate the same held-out **trip/session** footage against existing
  Klip policy and a uniform/strong handcrafted baseline.
  Compare Keep@20, missed controls, precision for defects,
  distinct scenes, temporal completeness, and blinded **pairwise
  human votes** where the two models disagree.
- Deployment gate: only promote an external score into Klip ranking if
  the held-out human preference gains are robust and it does not increase
  missed rare wildlife, safe editing/deletion risks, or duplicated footage.

Training the video backbone end-to-end before these tests is expensive
and unlikely to isolate why users enjoy a particular raw recording.
Start with feature/ranking-head qualification; expand only after
licensed data, human ground truth and evidence of real transfer.

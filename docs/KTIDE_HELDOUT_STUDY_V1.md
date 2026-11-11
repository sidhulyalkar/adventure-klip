# K-TIDE V2 P1: blind, trip-held-out representation study

**Status:** executable offline evaluation protocol. No personal archive data or
real evaluation results are included in Git. A passing synthetic benchmark
does **not** establish a real model improvement or scientific novelty.

## 1. Study question and hypotheses

**Primary question:** Does the trained K-TIDE temporal representation retrieve
human-adjudicated *similar situations from unseen trips* better than the
same frozen SigLIP2 frame embeddings averaged over time?

Predeclared primary endpoint: **trip-macro nDCG@5** on independently
adjudicated, mixed-relevance candidate sets. Secondary: Recall@1/3/5/10,
nDCG@1/3/10, Precision@1/3/5/10, MRR, and pairwise grade agreement.

**Controls:**
1. Frozen mean SigLIP2 embedding (the preexisting baseline).
2. Randomly initialized K-TIDE with identical architecture/inputs, fixed
   random seed, no training (architecture-only control).
3. Gap-filtered mean absolute change of adjacent frozen embeddings,
   normalized (simple temporal signal, not an action classifier).
4. Trained K-TIDE features using the same frozen teacher and timestamps.

The last two controls are optional for older embeddings reports or
zero-change clips. They are evaluated on an **identical supported-query
subset**, and the report explicitly states coverage. Never compare one
model evaluated only on easy clips against another evaluated on all clips.

The initial study is *not* a pixel-level model evaluation, species-truth
benchmark, causal inference test, preference fine-tuning or automated reel
editor. Those require additional ground-truth protocols.

## 2. Dataset freeze and anti-leakage

Train K-TIDE V1 using a local feature manifest with owner-verified trip
groupIds. It already records source SHA, frozen teacher key and exact
source-relative [startMs,endMs) windows, and saves the deterministic
train/validation/test split map.

The study code verifies the *exact* dataset manifest SHA-256, teacher model
key, checkpoint identity, split seed and split assignments. It rejects
training reports that do not state that test data were excluded from model
selection.

**Query and candidate windows must both be from the test split, but from
different trip groupIds and different sources.** Entire trips and repeated
recordings must be grouped *before training*, not after seeing results.

The blind planner uses a random seed and a same-subsport sampling preference,
but never K-TIDE/teacher similarity or old highlight scores. It balances
query counts across trip groups and includes any quiet/negative-control
windows in the input manifest. Do not interpret that sampling as
representative of the whole archive without a frozen coverage audit.

A credible study needs multiple independent outings. With the current
15% test-group split, around 14+ trip groups give at least two test groups.
A trip-cluster bootstrap requires **at least five independent test query
groups** (roughly 34+ total), otherwise the report explicitly withholds
confidence intervals. Fewer outings can produce descriptive pilot metrics,
but not a statistically supported performance claim.

## 3. Human relevance rubric

For every query window, play the query and each candidate **using a
separately verified local source or preview reader**. The study script
only displays source SHA and source-relative timestamps. It does not open
video or recheck preview SHA receipts. Do not rate unseen clips.

Blinding: show no learned-model results, similarity scores, or original
Keep/Skip/algorithm predictions during labeling. The owner should declare
the task and editorial goal before reviewing. Suggested tasks:

- scene_situation: comparable visible setting, actions and relationships;
- temporal_event: comparable progression, approach, peak, and resolution;
- editorial_context: comparable usefulness under the chosen goal.

Judgment grades:

| Grade | Human assessment |
|---|---|
| 0 | No meaningful relation for the declared task |
| 1 | Only a general visual/background relation |
| 2 | Clearly related situation or useful analogous event |
| 3 | Strong, specific match in action/interaction and progression |

Annotate every candidate with an integer grade 0..3,
"adjudication": "confirmed", and a nonempty "evidenceIds" array
referencing the private visual review receipt. If unsure, set relevance
to **null**, adjudication to **uncertain**, and preserve the ambiguity.

A candidate selected as a prior Skip derivative is not automatically
grade 0. A failed ski landing may be grade 3 for technique analysis.
Rare animal scenes may have NO related candidates: label all as 0/1,
rather than inventing a positive. The evaluator preserves these
**no-match queries** separately, excluding them from rank metrics until
a calibrated no-match/rejection experiment is built. The same is true of
all-positive sets, which cannot test ordering quality.

The current P1 evaluates one adjudicated reference deck. Independent
second-rater agreement and gold-standard disagreement adjudication are
needed before scientific claims about label reliability.

## 4. End-to-end local private protocol

Use a separate checkout of updated main and an existing completed training
run. None of the following commands read camera originals, decode video,
generate model predictions, train, publish or upload files.

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export DS="$WS/reports/ktide-pilot-001/manifest.json"
    export RUN="$WS/reports/ktide-run-pilot-001"

First, export representations for the same frozen dataset. The V2 embedding
export includes trained, frozen-mean, untrained, and observed-change controls.

    python scripts/klip_embed_ktide.py \
      --manifest "$DS" \
      --checkpoint "$RUN/ktide-best.pt" \
      > "$WS/reports/ktide-embeddings-v2.json"

Generate a model-blind pilot of 12 queries with 5 cross-trip candidates:

    python scripts/klip_ktide_study.py \
      --mode plan \
      --manifest "$DS" \
      --training-report "$RUN/training-report.json" \
      --max-queries 12 --candidates-per-query 5 --seed 31 \
      --task scene_situation \
      > "$WS/reports/ktide-blind-study-v1.json"

Generate a worksheet of sample IDs, source SHA and timestamps, without
revealing model predictions:

    python scripts/klip_ktide_study.py \
      --mode worksheet \
      --manifest "$DS" \
      --training-report "$RUN/training-report.json" \
      --study "$WS/reports/ktide-blind-study-v1.json" \
      > "$WS/reports/ktide-review-worksheet.json"

Review the source intervals in an independently receipt-verified local
viewer. Make a private COPY of the original deck, annotate the copy and
retain the pristine original for auditing. Never commit the annotations.

Audit completion without treating unreviewed grades as negatives:

    python scripts/klip_ktide_study.py \
      --mode audit \
      --manifest "$DS" \
      --training-report "$RUN/training-report.json" \
      --study "$WS/reports/ktide-adjudicated-v1.json"

When all candidates are confirmed and at least one query has both
relevant (2/3) and irrelevant (0/1) candidates, run held-out evaluation:

    python scripts/klip_ktide_study.py \
      --mode evaluate \
      --manifest "$DS" \
      --training-report "$RUN/training-report.json" \
      --study "$WS/reports/ktide-adjudicated-v1.json" \
      --embeddings "$WS/reports/ktide-embeddings-v2.json" \
      > "$WS/reports/ktide-heldout-evaluation-v1.json"

The report contains exact study and embedding-report hashes, the
checkpoint identity, fully judged query coverage, no-match/out-of-corpus
counts, trip-macro metrics, per-subsport descriptive results and paired
trip-bootstrap deltas where at least five query trips support them.
No individual review rationales or complete source paths are copied to
the evaluation report.

## 5. Mathematical evaluation

Similarity is normalized cosine between source-backed clip embeddings.
For grade y in {0,1,2,3}, graded gain = 2^y-1.
nDCG@K = DCG@K / ideal DCG@K on the **fully judged** candidate bank.

For a query with at least one grade>=2 and one grade<=1, Recall@K counts
retrieved grade>=2 items divided by all grade>=2 items in that query's
*judged pool*. This is **pool-relative recall**, not event recall across the
entire videoset. Precision@K uses min(K,judged pool size) as denominator.

Pairwise grade agreement compares every candidate pair with *different*
human grades, scoring an exact prediction tie as 0.5. MRR uses the first
grade>=2 result.

Aggregate queries *within each test trip*, then average trip means.
This prevents one outing with many windows dominating the estimate.
For >=5 independent test query trips, paired bootstrap resamples trips
while keeping both representations evaluated on the same human comparisons.
The 95% bootstrap intervals are **exploratory**, and do not independently
establish statistical significance or generalization. With fewer than five,
the report withholds them.

**Do not tune hyperparameters after looking at test results.**
Iterate on validation trips, freeze a new checkpoint and create a new
independent test-group study before making comparative improvement claims.

## 6. Practical limits and next experiment

This P1 study covers *retrieval of similar situations*, not temporal
localization. True event-phase understanding needs a separate annotation
schema (setup/onset/peak/resolution) with verified source PTS and
reviewed negative controls, followed by temporal IoU and boundary-error
metrics. No human-action, animal behavior or technique truth is generated
here from comments or similarity.

Next milestones in issue #98:

- owner-approved trial on 30-60 diverse episodes, then expand to
  independent outings and enough judged cross-trip candidates;
- second-rater agreement, explicit no-match/abstention and partial previews;
- compare temporal encoder against frozen baseline on untrained controls,
  rare wildlife, high-speed ski runs, moguls, jumps, surfacing footage,
  personal memories, failures, blurry/corrupt negatives;
- after a frozen held-out benchmark, train a goal/sub-sport-specific
  preference head from genuine blind A/B annotations;
- subsequently evaluate stronger vision/video backbones or local LoRA
  training if source-verified evidence and available compute justify it.

No user media, training feature shards, model checkpoints or label
exports are uploaded by this study. All synthetic fixtures contain
fabricated identifiers and labels only.

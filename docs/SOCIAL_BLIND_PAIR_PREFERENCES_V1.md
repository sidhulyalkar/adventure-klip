# Klip Blind Paired Preference Study v1

## Purpose

Social Lens v1 has a useful *temporal evidence extractor* but not yet an
externally trained, scientifically validated social-preference model. An
engagement predictor trained on non-permitted or invented creator footage would
be scientifically meaningless.

The immediate path to a better editor is to test an actionable hypothesis:

> When Klip ranks two verified skiing, biking or snorkeling moments,
> does the user agree, and can we identify when the preference differs
> from pixel change, clip clarity, coherent action or subject visibility?

This v1 generates **same-sport, different-source A/B comparisons**, frozen to
the exact candidate timestamp boundaries and source SHA identities, playable
from checksum-verified local H.264 proxies only. The browser hides Klip
scores, sample type and source identifiers. It records A/B/Tie/Neither,
optional reason checkboxes and comments, then exports a local JSON for
reproducible read-only evaluation.

**No model training, source video reads, public posting or deletion occurs
in either the pair-builder or the result evaluator.**

### What the evidence can and cannot say

- *Within candidate preference*: valid for this particular chosen set of
  reviewed, playable candidates. Not a claim about full archive precision or
  unseen event recall.
- *Klip concordance*: fraction of decisive non-tie votes on which Klip's
  higher-score candidate is also chosen. Tie and Neither retain separate
  denominators; this is *not* an unbiased estimate of accuracy.
- *Failure examination*: where Klip loses, the report includes the human
  reason tags and supported **chosen-minus-rejected** differences in
  observed proxies (pixel-change median, gradient texture, luminance
  fractions, cached source motion/audio and sparse semantic continuity).
  These are descriptive associations, not causal explanations or learned
  weights.
- *Correlation*: clips may appear in two pairs and different recordings
  may originate from the same outing or mount. We do not calculate fake
  confidence intervals, precision or generalization from this design.
- *No negative controls*: these are Klip's own nominated candidates. Use
  `archive-value-study` controls and a later matched control-pair
  experiment to measure undiscovered worthwhile footage.

## Why paired comparisons first?

Ranking "a whale, an empty reef, a ski descent, or a rock garden" with a
single arbitrary score is difficult; determining whether A or B better
preserves a complete sporting/wildlife moment is often much easier for
a human. That feedback answers the current failure question without
asking you to manually fill 10 dimensions × 300 clips.

Pair selection includes:

- High-vs-low Klip score **ranking contrasts**
- Smaller-gap **hard comparisons**, which are easier to misrank
- Balanced sport exposure when sufficient candidates exist
- Distinct source SHA for A and B (not a verified outing split)
- Max 2 uses of any individual clip within the generated review set
- Deterministically randomized A/B side and review order

The user sees **only the sport and original clip proxies**. The private
`pairs.json` contains baseline scores for post-review diagnostics;
avoid opening that manifest before rating.

## Run with the local outputs

This branch stacks on the existing temporal report branch. From a clean
worktree after running the 40-clip temporal pilot:

```bash
git status --short
git fetch origin
git switch --track origin/feat/social-blind-pair-preference-v1
python -m pip install -e '.[dev]'

export WS="$HOME/AdventureAI-clean.QJnopt"
export STUDY="$WS/reports/archive-value-study-pilot-v1/study.json"
export FEATURES="$WS/reports/social-rubric-balanced40-v1/feature-template.json"
export TEMPORAL="$WS/reports/temporal-proxy40-v1/temporal-evidence.json"

klip --workspace "$WS" social-lens-pair-review \
  --study "$STUDY" \
  --features "$FEATURES" \
  --temporal "$TEMPORAL" \
  --max-pairs 16 \
  --seed 104 \
  --output-dir "$WS/reports/social-blind-pairs16-v1"

open "$WS/reports/social-blind-pairs16-v1/index.html"
```

**Use the exact template used to produce the temporal report**.
The command verifies its SHA-256 before joining evidence, and checks
every playable proxy's checksum receipt again.

If you've processed only eight clips, use
`$WS/reports/temporal-proxy8-v1/temporal-evidence.json` and the
corresponding `$FEATURES` used for that run. There may be too few
*different source recordings per sport* to form pairs. A safe
`no_comparable_verified_pairs` report is expected in that case;
analyze more sources rather than pairing unrelated sports.

In the browser, play A, play B, select the stronger moment or tie/neither,
and optionally tag what mattered. Export JSON regularly for safety. The
browser page intentionally does not transmit ratings to a server; the
review export can be imported again to resume the same frozen experiment.

Move the downloaded export into any convenient local folder (the downloads
folder also works). Then run:

```bash
klip --workspace "$WS" social-lens-pair-results \
  --manifest "$WS/reports/social-blind-pairs16-v1/pairs.json" \
  --reviews "$HOME/Downloads/klip-paired-review-<digest-prefix>.json" \
  --output-dir "$WS/reports/social-blind-pair-results16-v1"
```

Replace `<digest-prefix>` with the filename downloaded by the browser.
The exported vote JSON is cryptographically *bound to the exact frozen
manifest* and will be rejected if it belongs to another study.
`results.json` then shows per-sport concordance, ties, Neither,
reason frequency, and disagreements with observed cue differences.

All output directories must be new. No label is inserted into Social
Lens's external data, no original footage is written, and the reviewed
choices are not automatically converted into a ranking model.

## How to improve scientifically after reviewing the pilot

**P0: Human preference coverage.** Expand to enough different sources
within each sport. Evaluate failure patterns per sport and capture
condition. Prioritize cases with decisive disagreement. Ensure your
personal Keep/Skip definitions remain stable over time.

**P0: Missed-event controls.** Current A/B selection is conditional on
Klip having nominated the clip. Sample independent time intervals that
Klip ranked low or never analyzed, then ask whether such moments beat
a matched candidate. This measures actual missed value rather than
merely how well Klip sorts its preselected proposals.

**P1: Temporal subject and event recognition.** Add source-verified
person/animal subject occupancy with track continuity, camera-roll
quality where a genuine horizon/gyro exists, and segmentation of
approach/action/outcome. Compare video-model embeddings against
the current low-res motion and semantic cosine baseline. Human-label
the actual event rather than assuming high frame change means sport.

**P1: Temporal model calibration.** Freeze a model/checkpoint and
preprocessing, hold out *entire outings*, evaluate A/B preferences
and Keep@K against the existing Klip scoring function, and test
marine rare-event recall. Do not confuse observational social-media
engagement with personal clip value.

**P2: External reference model.** Use *independently permissioned*
social videos/metadata, keeping editing, captions and music separate
from filmed action. A frozen shared encoder plus small ranking head
should be qualified before expensive end-to-end fine-tuning.

This version measures actionable disagreement without asking you
to annotate 3,000 feature ratings and without pretending that missing
external TikTok/Instagram training footage has been obtained.

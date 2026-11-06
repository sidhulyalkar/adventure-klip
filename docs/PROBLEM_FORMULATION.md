# Problem formulation: from visual novelty to complete action experiences

> **Research status:** The formulations in this document are hypotheses and proposed metrics. Do not mistake them for trained models, calibrated probabilities, validated event labels, or experimentally established improvements.

[← Project README](../README.md) · [Documentation index](README.md) · [Temporal research epic](https://github.com/sidhulyalkar/adventure-klip/issues/83)

## 1. The observed world and its unobserved events

A first-person action recording gives imperfect observations of a changing physical scene. We see camera motion, objects, texture, audio, and sometimes equipment or terrain. We usually **cannot** directly observe the athlete's internal state, full body pose, skill, intent, true speed, or why a viewer likes a moment.

For a video with duration \(T\), define:

$$
\mathcal{X}=\{(t_i, V_i, A_i, M_i, P_i, Q_i)\}_{i=1}^{N}
$$

- \(t_i\): **requested video-relative** time (not necessarily measured decoded-frame PTS).
- \(V_i\): image or frozen visual embedding.
- \(A_i\): timestamped acoustic evidence where available.
- \(M_i\): raw optical-flow / camera and scene-motion measurements.
- \(P_i\): source identity, model revision, preprocessing, requested/decoded/rendered timing references.
- \(Q_i\): evidence availability, sampling interval, missingness, and quality metadata.

The unknown event sequence is \(\mathcal{E}=\{e_j\}_{j=1}^{K}\). It must be kept separate from recorded \(\mathcal{X}\): model hypotheses \(\hat e_j\) do not become facts because a detector produces a number.

### Example of genuine ambiguity

A large image displacement can arise from a ski turn, torso motion, a head pan, a camera impact, or simply an optical-flow error. A snorkeling recording can contain bubbles, camera-body movement, waterline refraction, or a large animal. Treat each cause as a possible explanation requiring additional evidence.

## 2. The real hierarchy

An event may have phases \(\mathcal{P}_j\) and the same source interval may participate in multiple valid interpretations:

$$
e_j=(s_j,e_j^{\mathrm{end}},z_j,\mathcal{P}_j,\mathcal{I}_j,q_j)
$$

where \(z_j\) is a proposed event class, \(\mathcal{I}_j\) contains traceable observations, and \(q_j\) describes uncertainty / abstention rather than automatically pretending to be a calibrated probability.

**Micro**: near-frame changes, appearance/disappearance, audio onset, occlusion, optical-flow discontinuity.

**Meso**: approach, execution, encounter, landing, departure, interruption, extended movement.

**Macro**: exploration, challenge, anticipation, payoff, return, narrative continuity, possibly with multiple overlapping activities.

These levels are not assumed to have fixed real-world durations. Initial pilot windows of about 0.25–2s, 2–30s, and 10–120s are controllable engineering approximations. Some events are much shorter or longer.

### A change-point baseline

For successive **compatible** frozen embeddings \(\mathbf v_i\) with sufficient sampling density:

$$
d_i=1-\frac{\mathbf v_i^\top\mathbf v_{i-1}}
{\|\mathbf v_i\|_2\|\mathbf v_{i-1}\|_2}
$$

Candidate visual boundaries may occur where \(d_i\ge\tau\). With unobserved gaps \(t_i-t_{i-1}>g_{\max}\), **do not** form a continuity edge or an event boundary inferred from missing evidence.

This is a **visual-difference** baseline, not an event-boundary ground truth. Its thresholds and support windows must be tuned on validation recordings only, and frozen before a held-out test.

### Temporal support and abstention

For sampled times \(t_i\), each valid sample supports only a bounded neighborhood \(S_i=[t_i-r,t_i+r]\cap[0,T)\), potentially split at nearby sample midpoints. Let the observed-support set be:

$$
S_{\mathrm{obs}}=\bigcup_i S_i,\qquad
U=[0,T)\setminus S_{\mathrm{obs}}
$$

\(U\) is **unknown footage**, not empty, low quality, uninteresting, or negative. Report its duration and coverage rather than silently filling it with synthetic observations. If data is missing or model revisions differ, abstain or segregate representations.

### Timeline alignment

Track separately:

$$
t_{\mathrm{requested}},\quad t_{\mathrm{decoded\,PTS}},
\quad t_{\mathrm{preview}},\quad t_{\mathrm{rendered}}
$$

A proposal in source-relative milliseconds is not proof that a browser seek or an FFmpeg trim lands on an exact frame. For HEVC/VFR sources, independently test decoded timestamps, normalization offsets and render frame PTS; preserve observed errors. Do not perform a naive \(t\times\mathrm{fps}\) conversion as a precision guarantee.

## 3. What is a good clip?

Let \(c=[a,b)\) be a proposed clip and \(e^*=[s^*,t^*)\) a **human-annotated critical event core**. A measurable core-retention fraction is:

$$
R_{\mathrm{core}}(c,e^*)=
\frac{|[a,b)\cap[s^*,t^*)|}{t^*-s^*}
$$

This alone is insufficient. A short clip could contain the complete airborne phase yet omit the approach and landing. Event *completeness* must be annotated separately: for example, visible initiation, peak, resolution, and context adequacy, each with \(mathrm{unknown}\) and \(mathrm{not\ applicable}\) states.

The clip policy must also respect:

- explicit human Skip/exclusion windows;
- valid source bounds, provenance, and snapshot identity;
- a declared edit-duration budget;
- human approval before publication;
- no automatic deletion or rewriting of Keep/Skip labels.

### Candidate objective, not a production score

For a selected clip set \(C\), we might eventually solve:

$$
\begin{aligned}
\max_C\quad &
\alpha\,R_{\mathrm{core}}(C)
+\beta\,U_{\mathrm{human}}(C)
+\gamma\,D(C)
-\delta\,J(C)-\eta\,K(C)\\
\mathrm{s.t.}\quad &
\sum_{c\in C}|c|\le B,\\
& C\cap H_{\mathrm{excluded}}=\varnothing,\\
& \mathrm{publish}(C)\implies\mathrm{humanApproved}(C)
\end{aligned}
$$

\(D\) means diversity, \(J\) jarring/incomplete cuts, and \(K\) compute or review burden. The coefficients are **not learned or tuned yet**. \(R_{\mathrm{core}}\) uses human event-core annotations for evaluation, not secretly presumed ground truth from a detector. \(U_{\mathrm{human}}\) cannot be set equal to a VLM excitement score.

### How human utility could be estimated

A future pairwise model could start from an explicitly limited Bradley–Terry-style formulation:

$$
P(c_i\succ c_j)=\sigma\big(u_\theta(c_i)-u_\theta(c_j)\big)
$$

Use **actual blinded human comparisons** with order randomization, ties, inability-to-judge responses, annotator roles, and trip-held-out assessment. The formula is only a candidate model; the current project has no claim of a trained, generalizable preference function.

## 4. Falsifiable hypotheses

| ID | Hypothesis | Evidence that would refute it |
|---|---|---|
| H1 | Explicit temporal grouping improves complete-event retention over frame novelty at the same review budget. | No held-out event-core gain or systematically worse missed-event recall. |
| H2 | Event-phase-aware boundaries produce clips preferred to fixed-context trims. | Blind preference near chance (with uncertainty), or degraded pacing / context. |
| H3 | Frozen video embeddings outperform frozen image embedding change points on motion-sensitive events. | No gain at comparable frame/compute budgets or inconsistent out-of-trip improvement. |
| H4 | Modeling **unknown evidence** separately reduces overconfident wrong labels. | Same/more wrong definitive claims or abstention that merely hides all difficult samples. |
| H5 | The pipeline generalizes across ski, bike, snorkel and new outings. | Improvements restricted to one subject, trip, camera, or lighting regime. |

Do not combine a negative H3 with a negative conclusion about H1. A low-cost visual-change approach might improve editing without requiring a massive video model.

## 5. Four fair comparison arms

| Arm | Observations | Proposed mechanism |
|---|---|---|
| **A: Existing baseline** | Current motion, scene embeddings, episode rules | Current candidate selection and reviewed trim policy |
| **B: Fixed context** | Same candidate pool as A | Fixed before/after padding; same length and review budget |
| **C: Temporal Lite + phases** | Same frozen image features as A | Change-point proposals, explicit temporal context and event-core protection once T2 exists |
| **D: Frozen video encoder** | Contiguous sampled video windows | Temporal video features + the same downstream candidate/boundary/evaluation constraints |

**Important:** Temporal Lite T0 alone produces *visual support* and transitions, not an editor. Arm C should only be promoted as a clip-editing experiment after T2 implements explainable candidate boundaries.

A model should have to **earn its compute cost**. Report actual frame extraction, hashing, cache reads, inference and review time separately. Equalize selected candidate windows and editing budgets where feasible, and include an explicit runtime trade-off.

## 6. A real annotation study

### Pilot sampling

Propose **120 windows** across snorkeling, skiing and mountain biking, but only if enough independent verified outings exist. Use random-source intervals alongside high-ranked candidate intervals, low-value contexts, and known missed-event challenges. Never manufacture a balance by inventing footage or activities.

Freeze sampling IDs and model configurations **before** inspecting preference labels. Split at the outing/trip level and prevent near-duplicate recordings from crossing train, validation and test.

### Record independent human observations

For each review window, collect:

- independently marked start, action peak and resolution where visible;
- visible versus inferred action, uncertain class, and hard negatives;
- original human-edited in/out with reasons;
- completeness, pacing and framing judgments kept separate;
- whether the encounter is interesting, regardless of event-class accuracy;
- source and rights provenance, plus rater pseudonyms and relevant expertise.

Obtain multiple independent judgments on a subset or all windows. Resolve disagreement transparently; do not replace competing annotations with a fabricated single truth. An annotator might prefer the view while disputing the model's proposed activity.

### Blind A/B review

Compare edits from the **same source event** at the same approximate output budget. Hide model name, condition, event score and other raters' votes. Randomize A/B order. Include `tie`, `neither`, and `cannot_tell`.

**Primary measures:** event-core retention, human clip preference with confidence intervals, and seconds to usable edit.

**Secondary measures:** boundary error median/p90, candidate recall on randomly sampled source intervals, useful Keeps per 20 reviews, redundancy, diversity, false-positive visual transitions, missing-evidence coverage, compute and adjudication burden.

For preference, report a stratified win/tie/loss table plus trip-clustered uncertainty. Bootstrap by recording day/outing, not adjacent frames. A prospective rule could require the lower 95% bound of the preference win rate to exceed 50% and a non-inferiority margin for event-core retention. These margins must be **frozen before seeing held-out labels**, not retrofitted to a positive result.

## 7. Known failure families

| Failure | Example | Required handling |
|---|---|---|
| Camera ego-motion | Helmet shake mistaken for a ski maneuver | Negative control, abstention, separate motion cue |
| Fast/brief action | Jump occurs between sparse samples | Report sampling gap; targeted real-time burst or explicit miss |
| Long continuous event | Animal remains in frame for 45 seconds | Preserve event core; do not apply midpoint shortening blindly |
| Subtle dynamics | Ray approaches without large embedding change | Use sequential motion evidence / targeted temporal encoder |
| Underwater refraction | Bubbles or surface transition create scene changes | Multimodal uncertainty, no breathing inference |
| Distribution shift | New mountain, season, species, camera or lighting | Trip-held-out evaluation and calibration diagnostics |
| False clip quality | Sharp frame selected with no anticipation | Human completeness and editorial comparison |
| Training leakage | Adjacent windows in train and test | Group by source/outing, deduplicate near-identical clips |
| Identity or rights | Bystanders, voices, restricted footage | Consent/licensing gate and local-first controls |

## 8. Promotion gates

- **T0:** deterministic, bounded, cache-only visual change graph; no original media read or false action claims.
- **T1:** source PTS, browser preview and rendered-frame alignment verified on representative real DJI HEVC/VFR recordings.
- **T2:** human-event-core retention and exclusion safety for multi-scale clip proposals.
- **T3:** source-verified object-versus-camera motion on manually labeled motion negatives.
- **T4:** frozen video encoder only if it improves the measured quality/compute frontier.
- **T5:** blind, trip-held-out human assessment with registered decision criteria and honest negative results.
- **T6:** release a reproducible benchmark only with explicitly redistributable content and rights clearance.

Full engineering tasks: [Issue #83](https://github.com/sidhulyalkar/adventure-klip/issues/83). The actual implementation branch spec is [Temporal Action Intelligence](https://github.com/sidhulyalkar/adventure-klip/blob/feat/temporal-lite-action-graph-v1/docs/TEMPORAL_ACTION_INTELLIGENCE.md).

## 9. Research references

These are architectural references, not claims that Adventure-Klip reproduces their results.

- **STITCH (2026)**, training-free temporal abstraction from video embeddings: https://arxiv.org/abs/2608.27929
- **V-JEPA 2**, frozen self-supervised video representations: https://github.com/facebookresearch/vjepa2
- **ruptures**, change-point detection methods: https://centre-borelli.github.io/ruptures-docs/
- **QVHighlights**, moment retrieval and highlight evaluation dataset: https://github.com/jayleicn/moment_detr

For release accuracy, always state *exact model revision, annotation definition, evaluation split, evidence coverage, and hardware cost*. Synthetic test success does not establish real-world event recognition quality.

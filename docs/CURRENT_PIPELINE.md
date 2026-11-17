# Klip current architecture and next modeling strategy

**Current canonical design:** [Research mission](KLIP_RESEARCH_MISSION.md) · [V3 contracts](KLIP_ARCHITECTURE_V3.md) · [Roadmap](KLIP_IMPLEMENTATION_ROADMAP.md) · [Experiment evidence](../logs/README.md). The code inventory below records implemented, historical and partial components; it is **not** a claim of complete source-wide temporal scene discovery.


**Active research track:** full-timeline scene discovery → temporal/story
understanding → goal-conditioned ranking → owner review, as defined in the
research mission. Sport and animal labels are evidence, not the objective. This
document is the orientation point for new engineers, not a replacement for
older versioned experiment reports. Older docs are historical evidence.

## A map of what is implemented now

| Responsibility | Current implementation | Evidence level |
|---|---|---|
| Immutable recording identity + local catalog | catalog.py, import code | Verified source SHA, read-only use preferred |
| Cached features | scene_atlas.py, semantic_jobs.py, SigLIP2 | Frozen frame-image representations; not continuous action |
| Whole-archive coarse candidate discovery | full_archive_campaign.py, status + packet | SHA-qualified source windows, motion/control hypotheses |
| Proxy triage | auto_scout_v2.py (evidence), auto_scout_review.py (presentation) and auto_scout CLI | Seven stills, provisional Ollama observations and brightness |
| Reviewed examples / exemplar matching | examples.py, scene_atlas.py, exemplar retrieval baseline | Human Keep/Skip examples + same-model cosine |
| Source-backed moment + edit variants | moment_edit_v2.py | Owner event intervals, reversible trims, preview-only |
| Local owner labeling | moment_label_studio_v2.py | Explicit owner sports, tags, and defect spans |
| Category reel planning/rendering | campaign_reels.py, klip_build_collections_v2.py | Draft local H264 proxies; no verified final master |
| Owner evidence ledger | evidence_ledger.py, scripts/klip_evidence_ledger.py | Append-only; adapters for marine v1/v2, blind calibration, ski run; synthetic tests only |
| Variable-length scene segments | timeline_segments.py, scripts/klip_timeline_segments.py | Level-shift boundaries, in-scene event candidates, duration-proportional budget; synthetic tests only |
| K-MOMENT / K-TIDE research | main branch scripts and offline cache | Eight dense windows; no proven ranking advantage |

Core contracts: **Asset → Observation → Candidate Moment → Owner-reviewed
Event → Edit Variant → Reel Recipe → Preview/Final Render**.

Separate:
- source-verified facts: SHA, original-relative times, cached-embed model key;
- model observations: possible scene, feature, subject, quality defect;
- human decisions: Keep/Skip, labels, event boundaries, viewing goal;
- editorial actions: proposed edit, ranked reel, explicit export approval.

Never write model suggestions into human ground truth. Never silently infer
native PTS alignment from approximate preview seeks.

## Reduce cognitive load without destabilizing experiments

The original archive CLI and documentation cover multiple eras, from 0.2
through 1.0, along with newer experimental branch workflows. Do not delete
historical modules while old worktrees or saved reports can reference them.
Instead:

1. **One current entrypoint:** this file plus README root top navigation.
2. **One model interface:** immutable \`modelKey\`, timebase, sampling rate,
   input SHA and typed output with evidence/abstention.
3. **One moment contract:** source SHA + source-relative times + provenance.
4. **One review store:** keep owner decisions distinct from model proposals.
5. **One ranking evaluation:** split by independent outing, compare only
   models on the same labeled examples, log coverage and abstentions.
6. **One reel renderer:** source-quality master rendering is a separate
   gate after preview acceptance.

Repeated local judge prompts, independent ranking policy files, and
multiple nearly-identical report serializers are consolidation candidates,
but should be migrated behind compatibility adapters rather than
renamed or removed mid-experiment.

## Models: cheapest credible order

1. **Already installed SigLIP2** for scene/category retrieval: zero new
   training, compare verified positive vs negative exemplars using frozen
   embeddings. Existing \`scene_atlas.enrich_candidates\` already matches
   reviewed examples; the read-only baseline isolates this behavior
   without mixing it with hand-set motion weights.
2. **Local VLM** on ambiguous candidates only. Seven-frame Gemma scouting
   is cheap compared to source decode, but still cannot prove trick execution
   or continuous wildlife interaction.
3. **Pretrained video-native encoder** (e.g. V-JEPA 2) on dense
   motion-dependent windows if action recall is lacking. Frozen encoder +
   small probe before any fine-tuning of backbone.
4. **Object tracking** for marine wildlife where localization and departure
   matter. SAM 3 concept-prompted video segmentation is a candidate
   subject to feasibility benchmarking, not yet integrated.
5. **Train a personalized preference ranker** with blind binary or pairwise
   human judgments only after source/outing-disjoint evaluation is viable.
6. **Native render** with conservative stabilization, brightness/color,
   audio handling and PTS verification, after actual cut comparison.

Reference footage from the web can be used with permission to propose
category exemplars. It is not a clean training/evaluation substitute for
the user's own first-person camera footage: broadcast, drone, montage and
POV shots have different viewpoints and editing distributions. Separate
source domains and respect licenses. For actual conditioning prefer
several owned positive *and* hard-negative examples per category.

## Minimal read-only exemplar probe

This utility uses only catalog-referenced, SHA-verified *existing* SigLIP2
feature artifacts and verified private pilot-proxy manifests. The SQLite
connection opens \`mode=ro\` and \`query_only\`; it does **not** invoke the
catalog's schema-initializing \`connect()\` context manager.

Run it from the feature branch checkout after the pilot and with the
private workspace mounted. It does not call Ollama or process camera videos:

    export WS="$HOME/AdventureAI-clean.QJnopt"
    export PILOT="$WS/reports/klip-v2-pilot-20261114-153934-88998"
    export OUT="$WS/reports/klip-exemplar-baseline-$(date +%Y%m%d-%H%M%S).json"
    python scripts/klip_exemplar_baseline.py --workspace "$WS" --pilot "$PILOT" --output "$OUT"

If no confirmed Keep/Skip examples exist, the report states that
explicitly rather than fabricating ranked labels. If coarse embeddings
do not overlap a candidate interval, it remains unavailable.

**This is NOT a fair held-out benchmark.** It excludes same-source
matches but not necessarily the same outing across recordings; report
and inspect examples before using similarity to influence ranking.

## What to measure next

Human-reviewed 10-20 positive and 10-20 hard negative examples per ski
or snorkel target category are a *planning goal*, not a dataset status.
Use entire outing groups for hold-out, and measure:
- event recall@K and false-negative count by category;
- retrieval coverage and source/outing independence;
- clip start/end error relative to annotated ground truth;
- paired A/B viewer preference for edited vs unchanged clips;
- compute cost per verified highlight, not model calls per frame.

Proceed to full-library reprocessing only if this pilot demonstrably beats
the existing coarse/visual retrieval baseline.


## 2026-11 cleanup: module boundaries

Auto-Scout was previously one 470-line module mixing model inference, disk
integrity, receipt loading, review queue ranking and HTML rendering.
Pure review report + gallery code now resides in
`auto_scout_review.py`. `auto_scout_v2.py` re-exports the same
`build_report`, `review_html`, and `VERSION` names, preserving scripts,
test imports, model prompt, receipt policy, and the running V2 checkpoint
contract. This is structural refactoring, not a new classifier or a policy
change. CI must validate before merge. Do not switch the in-flight worktree.


## Scout failure observability (new, resume compatible)

The original Auto-Scout wrote only `errorType: ValueError` plus the
generic `read_model_or_decode_failure_unverified`. A failed receipt now
also includes sanitized `failureStage` and `failureCode` to distinguish
`proxy_quality`, `frame_sampling`, `ollama_request`, `model_json`,
`model_schema` and integrity errors. Raw local model responses, original
media paths and personally identifiable frame contents are never logged.
The summary reports counts by failure stage and code. Previously failed
receipts remain unchanged until explicitly retried. The prompt, strict
JSON schema, fingerprint, checkpoint version and successful receipt format
remain unchanged. Do not modify an ongoing worktree or retry failures
concurrently with that run.

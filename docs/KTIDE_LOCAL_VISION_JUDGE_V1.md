# K-TIDE vision judge V1: local model-aided scene assignments

The source inventory found **16 histogram-ready** recordings from 232 catalog
sources, all with unknown activities and identical histogram revision
cdd573e7a357ac567854. These color histograms cannot reliably infer sport,
wildlife species or full video dynamics. Instead, Klip can inspect **four
sparsely sampled real frames** per selected recording with a local multimodal
judge and propose likely scenes, sub-sports and feature candidates.

This is an optional, real video-read stage. Nothing here uploads footage.
No model is used to certify an independent trip, real-world speed, species,
physical action outcome, personal identity or exact temporal action phase.

## 1. Model and method

Use local Ollama with a model that understands images, e.g. gemma3:4b:

    ollama list
    ollama pull gemma3:4b     # Only if not already installed; downloads weights

The only supported LLM API endpoint is http://127.0.0.1:11434/api/chat
(or http://localhost:11434/api/chat). Other hosts or ports are refused,
including HTTPS cloud endpoints. This is not a network-free call because
it communicates with a locally running Ollama process. Verify that Ollama
itself has not been configured to proxy an external service.

The script:
1. Selects up to 8 diverse ready sources from the existing frozen-feature
   inventory, distributing selections across coarse capture-time buckets.
2. **Read-only plan by default:** no source reads, frame decoding, model
   inference, or file creation.
3. On explicit --run, opens each selected source via the existing SQLite
   catalog in read-only mode and recomputes its SHA-256, only proceeding if it
   matches its catalog content identity.
4. Decodes exactly 2..8 evenly spaced ordered PNG stills (4 by default)
   from each source using existing FFmpeg source-PTS extractor. Each still
   has source-relative requested time, available decoded PTS, and SHA-256.
5. Sends only those resized stills and an explicit constrained JSON rubric
   to local Ollama (no video path or full video upload).
6. Validates the returned sub-sport against KSRE's real profile vocabulary
   and any nominated dimension against the 35 KSRE dimensions.
7. Writes a new private directory under workspace/reports with model
   proposals and an exact-format **smoke-only** assignments file.

The event hints refer exclusively to the sparse seen frame indices, never
assert continuous events in unobserved time. Feature candidates contain
"evidenceSource": "model_hypothesis" and "measuredValue": null. They
cannot become calibrated KSRE observations without human or independent
instrumental evidence.

## 2. Run against existing 16 histogram-ready sources

Use an updated separate Klip checkout/worktree, with the same WS pointing
at the private existing workspace. This command DOES NOT alter its SQLite
catalog, source files, or the original manual assignments file.

    export WS="$HOME/AdventureAI-clean.QJnopt"
    git fetch origin
    git switch --detach origin/main

    # Pure selection preflight, no media read or network call
    python scripts/klip_ktide_judge.py \
      --workspace "$WS" \
      --inventory "$WS/reports/ktide-histogram-inventory.json" \
      --limit 16

    # Optional source read and local model inference
    python scripts/klip_ktide_judge.py \
      --workspace "$WS" \
      --inventory "$WS/reports/ktide-histogram-inventory.json" \
      --name ktide-judge-pilot-001 \
      --limit 16 --frames 4 --model gemma3:4b --run

Outputs:
- reports/ktide-judge-pilot-001/proposals.json: per-video model
  summary, tags, referenced observations, proposed KSRE dimensions,
  possible temporal hints, uncertainties, hash/PTS receipts and
  model-failure statuses.
- reports/ktide-judge-pilot-001/assignments-smoke.json:
  schema-compliant {version,sources} for the existing K-TIDE planner.
  Unknown/failed labels fall back to general_adventure and smoke-only
  capture group IDs, never fabricated human-confirmed annotations.

If you already have manually reviewed some group IDs and sport assignments,
you can pass --existing-assignments PATH. Existing nonprovisional group IDs,
explicit specialist sub-sport choices, and verified owner goals win over
judge guesses. The source file is never overwritten.

## 3. Provisional group IDs: hard line between smoke and science

A multimodal model cannot establish trip identity from sampled scenery.
The judge uses a conservative **14-day capture-gap grouping** for smoke
tests: nearby source recordings are collapsed into a common provisional
bucket to reduce obvious leakage; recordings without timestamps share
a single unknown group. This is a heuristic and **does not verify** that
different buckets are independent trips, especially for long vacations
and incorrect capture clocks.

All automatic groups are marked with the literal "provisional_" prefix.
The normal source-assignment planner refuses such groups, unless explicitly
told to allow **engineering smoke only**:

    python scripts/klip_plan_ktide.py \
      --workspace "$WS" \
      --provider histogram \
      --assignments "$WS/reports/ktide-judge-pilot-001/assignments-smoke.json" \
      --allow-provisional-smoke \
      --window-seconds 120 --stride-seconds 120 \
      --max-per-source 20 \
      > "$WS/reports/ktide-selection-smoke.json"

    python scripts/klip_prepare_ktide.py \
      --workspace "$WS" \
      --provider histogram \
      --selection "$WS/reports/ktide-selection-smoke.json" \
      --name ktide-histogram-judge-smoke-001

    python scripts/klip_train_ktide.py \
      --workspace "$WS" \
      --manifest "$WS/reports/ktide-histogram-judge-smoke-001/manifest.json" \
      --name ktide-run-histogram-judge-smoke-001 \
      --epochs 5 --batch-size 8 --hidden 64 --layers 2 --device auto

120-second windows are advisable because existing histogram sampling is
sparse. A source with fewer than four frames in a window may contribute
zero windows; the planner will fail rather than invent observations if
fewer than three groups have eligible windows.

The training report explicitly marks provisional group IDs as
scientificHeldoutEligible=false. The scientific held-out study runner
**refuses** any dataset with provisional groups, even if a model happened
to train successfully. Do not interpret this smoke result as independent
heldout representation validation.

## 4. Toward meaningful, model-trained representations

The local judge is an annotation proposal engine. Color histogram-only
K-TIDE training tests the code, not general video comprehension. For the
real study:

- Organize video-trip identities using independently established outing
  metadata and source timelines, with human review only for ambiguous
  clusters, rather than blindly accepting LLM guesses.
- Generate *frozen SigLIP2* embeddings from bounded, SHA-verified,
  independently selected source recordings, prioritizing ski action,
  underwater reef/rays, biking, kayaking and negative/quiet scenes.
- Train K-TIDE over real frozen visual feature streams, then use human
  blind heldout query judgments to compare mean teacher, temporal
  controls and trained embeddings.
- Maintain separate tracks for the 35 KSRE usefulness dimensions,
  uncertain scene hypotheses, and eventual verified action/event labels.

This code never silently modifies original videos, assigns ground-truth
species, updates the catalog, sends footage to a cloud provider, or
automatically approves deletions or publication.


## 5. Recover partially judged archives without repeating successes

A private pipeline smoke run processed 16 histogram-ready sources and
46 feature windows on MPS. Five sources received valid local vision-judge
interpretations and 11 did not. Training completion is not evidence of
understanding events, wildlife, or human preferences.

Inspect the existing report without opening any media:

    python - <<'PY'
    import json, os
    from pathlib import Path
    from collections import Counter
    p = Path(os.environ["WS"]) / "reports/ktide-judge-pilot-001/proposals.json"
    d = json.loads(p.read_text())
    failed = [r for r in d["proposals"] if r["judgeStatus"] != "model_hypothesis"]
    print("Failed:", len(failed))
    print("Failure types:", dict(Counter(r.get("failureType","unknown") for r in failed)))
    print("Failures with decoded frames:",
          sum(bool(r.get("observedFrames")) for r in failed))
    for r in failed:
        print(r["sourceSha256"][:12], r.get("failureType"),
              len(r.get("observedFrames",[])))
    PY

Run a new, bounded, **failure-only retry**. Previously successful sources
are not opened, re-hashed, decoded or rejudged. Failed sources are
source-identity-verified again before sampling.

    python scripts/klip_ktide_judge.py \
      --workspace "$WS" \
      --inventory "$WS/reports/ktide-histogram-inventory.json" \
      --retry-from "$WS/reports/ktide-judge-pilot-001/proposals.json" \
      --name ktide-judge-pilot-retry-001 \
      --limit 16 --frames 4 --model gemma3:4b --run

The retry requires the same exact source roster, grouping, model, frame
count and valid prior successful frame SHA-256 receipts. New reports use
a distinct directory, protecting the earlier pilot and its training results.

The new output distinguishes reusedSuccessfulJudgments, freshlyJudgedSources,
failureStages, failureTypes and feature hypothesis coverage. Failure stages
include source_verification, frame_decode, local_model and schema_validation.
Only error **types** and **stages** are reported; original paths and raw
model responses are not exposed.

Missing source recordings, FFmpeg frame decoding failures, local Ollama
problems and invalid JSON predictions require different repairs. Use
the stage counts to choose the next step, rather than blindly retrying
forever. Continue to treat every proposed label or group identity as
unverified. No previous training checkpoint needs to be overwritten.

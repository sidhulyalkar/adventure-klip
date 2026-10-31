# NVIDIA NIM independent clip-judge benchmark (experimental)

This is a **human-calibrated model evaluation tool**, not an autonomous
ground-truth factory. The initial implementation uses 3–8 user-selected
**ordered still frames per clip**. It does not send complete video, audio,
precise motion, usernames, source filenames, absolute paths, or original
media. Human editorial decisions remain authoritative.

NVIDIA documents OpenAI-compatible multimodal NIM chat-completion APIs;
the media type supported **varies by model**. Cosmos Reasoner and multimodal
Nemotron models are potential judges, but **verify each chosen model's current
availability, image support, request limits, credentials and terms** at
https://build.nvidia.com before use. A model name alone does not prove
a hosted video endpoint works.

## Safety contract

- **Nothing is uploaded by default.** `prepare-nim-benchmark` only creates a
  local plan unless `--decode-frames` is explicitly given. Frame decoding is
  local; it may increase SSD I/O and should be run only after the current
  long DJI processing job finishes.
- `judge-nim` validates planned requests without network access. It only
  transmits after explicit `--allow-external-upload` and an API key through
  `NVIDIA_API_KEY`. Never commit or print an API key.
- Hosted endpoint is restricted to
  `https://integrate.api.nvidia.com/v1/chat/completions`. A custom
  self-hosted NIM endpoint is allowed only on loopback localhost.
- Frames must be real JPEG/PNG signatures, 3–8 per clip, at most 320 KB
  each and 2 MB per clip. Request count caps at 48, default 12. No retries
  are performed, preventing silent repeat costs.
- A hosted request sends *frame contents* to NVIDIA. NVIDIA-hosted trial
  services may record submitted inputs and outputs for service purposes.
  Review the applicable service/privacy terms, footage rights and any
  identifiable people **before approving uploads**.
- Judge responses contain short model descriptions and rationales in the
  resulting **local** JSON. No image data or absolute media paths are written
  to judge reports.
- The model never sees the sampling pool ('candidate' vs 'random'),
  model score, folder name, original filenames or algorithm label.

## Controlled pilot, after the 315-video processing job

1. Create a new private sample manifest and extract six still frames per clip:

   ```bash
   klip --workspace ~/AdventureAI prepare-nim-benchmark \
     --output-dir ~/AdventureAI/nim-benchmark/pilot-01 \
     --samples 6 --seed 20261031 --decode-frames
   ```

   The output folder must be new and inside the workspace. Source files are
   SHA-256 verified, read only, and never modified. Local FFmpeg seeks are
   approximate and not validated frame-accurate timestamps.
   Alternatively omit `--decode-frames` to generate a plan with **no media
   reading**; the judge cannot proceed until frames exist.

2. Inspect the actual JPEG frames, remove any you don't want to share, and
   edit the manifest to select only approved clips. The manifest needs
   `schemaVersion=1`, an array of `clips` with `clipId`, `pool`,
   `startMs`, `endMs`, plus ordered `frames: [{tMs, path}]`
   relative to the frames directory. Do not label random samples as
   negative examples. Keep at least 3 frames per clip.

3. Select two or three **currently image-capable** NVIDIA NIM models from
   the NVIDIA API catalog. Substitute actual model IDs below.

   First execute a dry run (no API key required; no uploads):

   ```bash
   klip --workspace ~/AdventureAI judge-nim \
     ~/AdventureAI/nim-benchmark/pilot-01/manifest.json \
     --frames-dir ~/AdventureAI/nim-benchmark/pilot-01/frames \
     --model MODEL_ID_A --model MODEL_ID_B
   ```

4. After personally approving the selected frames and service terms, place
   the API key in the `NVIDIA_API_KEY` environment variable using a secure
   method appropriate for your shell/session. **Do not put it in command
   history, Git, or chat.** Then explicitly opt in:

   ```bash
   klip --workspace ~/AdventureAI judge-nim \
     ~/AdventureAI/nim-benchmark/pilot-01/manifest.json \
     --frames-dir ~/AdventureAI/nim-benchmark/pilot-01/frames \
     --model MODEL_ID_A --model MODEL_ID_B \
     --allow-external-upload --max-calls 12 \
     --output ~/AdventureAI/nim-benchmark/pilot-01/jury-results.json
   ```

   Successful model votes are also checkpointed immediately to
   `jury-checkpoint.jsonl` in the manifest folder, with request fingerprints
   based on the exact frame hashes, model, prompt version and endpoint.
   Rerunning the same experiment skips these completed votes instead of
   resending them. Failed requests are not silently retried during a run.
   A network failure **after** the provider accepted a request but
   **before** the client received its response can still cause duplicate
   billing on a deliberate rerun; at-most-once billing cannot be guaranteed
   from the client alone. Check your provider usage before retrying.

   Different model families may require model-specific parameters and may not
   support still-frame arrays. Never count such errors as low-quality video.

5. Use Klip's existing Review/Library to make explicit human choices on
   the **same** source intervals. Then compare:

   ```bash
   klip --workspace ~/AdventureAI calibrate-nim \
     ~/AdventureAI/nim-benchmark/pilot-01/manifest.json \
     ~/AdventureAI/nim-benchmark/pilot-01/jury-results.json
   ```

## Model contract and rubric

Every judge independently receives the same ordered frames, timestamps and
fixed rubric, not previous judges' scores. Four ordinal criteria (0–4):
`event_interest`, `event_completeness`, `visual_quality`,
`story_value`. The model also returns `recommendation` as keep/later/skip,
`insufficient_evidence` as a mandatory boolean, visible content, short
rationale, and any missing evidence.

The jury aggregates median scores and exposes vote dispersion. Fewer than
two non-abstaining judges yields insufficient consensus; wide disagreement
routes to human review. Consensus is **not calibrated confidence**.
Images cannot prove movements between samples, audio continuity, a verified
crash, animal species, human skill, or exact temporal boundaries. Use full
video-capable NIMs for a later separately validated experiment.

## Benchmark design

The sampler uses a seeded mixture of existing episode candidate windows and
duration-weighted random source intervals, with no more than two intervals
per asset. Sampling pools are stored **locally** and hidden from models.
Random windows are *unlabeled*, not known negatives. Quotas alter the
sampling probabilities, so raw random-pool frequencies cannot be casually
extrapolated to all hours of footage.

For the first validation, target 24–36 AI-judged clips, then at least
40–60 reviewed intervals spanning ski/bike/snorkeling/unknown, different
recording days, non-action negatives and genuinely missed event examples.
At least one human review must cover 80% of the sampled interval for
`calibrate-nim` to compare its editorial label. The report separately
quantifies unreviewed samples, ambiguous labels, abstention and disagreements.
Its recall metric is **conditional recall on evaluated human-reviewed
intervals**, not end-to-end retrieval recall across all source video.

Separate projects or capture days between prompt development and final
evaluation. Blind judges to the origin of a candidate. Compare against
the motion-only baseline, random selection and any single judge.
Evaluate whether jury ranking really improves human review speed and
useful highlights discovered per hour. Do not optimize solely for
inter-model agreement because correlated failure can look like consensus.

## Cost and scalability

NVIDIA Developer Program endpoints are for experimentation; provider
availability, quotas and production licensing differ. There is **no**
trustworthy fixed per-clip cost for mixed image requests without selected
model IDs, their media-token accounting, and current terms. A run's basic
upper bound is `number_of_clips × number_of_judges` API calls. The CLI
requires an explicit maximum call budget and records token usage when
returned, but does not predict dollars from unverified rates.

Future versions: explicit video-capable judges; run-level resume identifiers
to avoid duplicate billing; encrypted/redacted private frame review;
additional prompts for sport-specific skills and narrative preferences;
pairwise clip ranking; sampling-probability-aware population estimation;
per-trip holdouts; model drift and cost/quality dashboards.

**This experimental branch has no real-user-footage benchmark yet.**
Only fake/mock model responses and synthetic/local fixtures are tested in CI.

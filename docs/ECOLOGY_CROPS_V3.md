# Ecology v3: exact-PTS, high-detail wildlife evidence crops

## Purpose

The prior ecology releases can create ecological species-search candidates and
broad `ecology-owlv2` object-track **review proposals**. This opt-in command
prepares **high-detail organism crops** from a *small number* of existing
candidate frames. It deliberately does **not** classify species, download
models, upload images, change the editor, or run a full-library video scan.

Each crop retains an audit trail:

- Exact SHA-256 asset identity verified before and after extraction
- Detection provider and model-key SHA, object-group label, original 384px
  sampled detector box, and score (uncalibrated)
- Requested video-relative time and cached vs redecoded **source PTS**
- Decoded image dimensions, crop bounds, crop content checksum
- Explicit unverified status, no guessed species or individual-animal count

The command abstains from source decoding entirely when the cached object
proposals lack usable actual source PTS. It refuses to save a crop if the
newly decoded source PTS differs by more than two milliseconds from the
cached detected frame's PTS. This is crucial for VFR and nonzero-start video
streams where timestamp arithmetic cannot safely be guessed.

## Usage

After your current histogram pilot, create an **opt-in**, snorkeling-only
adaptive plan, and obtain OWLv2 marine-group detections:

```bash
export WS="$HOME/AdventureAI-clean.QJnopt"
klip --workspace "$WS" adaptive-plan \
  --activity snorkeling --limit 2 --coverage-seconds 40 \
  --interest-seconds 2 --max-frames 96 \
  --output "$WS/exports/ecology-pilot-v3.json"

klip --workspace "$WS" adaptive-scenes \
  --plan "$WS/exports/ecology-pilot-v3.json" \
  --backend ecology-owlv2 --decode-mode batched
```

The OWLv2 step may download model weights when run for the first time via
Transformers; it is an explicit semantic-inference step, not part of the crop
packet. It can be GPU- and time-intensive, so keep the pilot small.

Find the exact snorkeling asset SHA-256 from the local catalog:

```bash
sqlite3 "$WS/catalog.sqlite3" \
  "SELECT id,activity,duration FROM assets WHERE activity='snorkeling' LIMIT 8;"
```

Optionally inspect the **cached-only** encounter report:

```bash
klip --workspace "$WS" ecology-encounters \
  --asset "REPLACE_WITH_EXACT_ASSET_SHA256" \
  --output "$WS/reports/ecology-encounters-03.json"
```

Create a small, *new*, private crop packet from matching source footage:

```bash
klip --workspace "$WS" ecology-crops \
  --asset "REPLACE_WITH_EXACT_ASSET_SHA256" \
  --max-events 6 --crops-per-event 3 --width 1536 \
  --output-dir "$WS/reports/ecology-crops-03"
```

`max-events` is bounded 1–8 and `crops-per-event` 1–3, for at
most **24 crop operations**. `width` is 768, 1536 or 2048 pixels.
This width is the requested **decoded-frame width**, not a promise
of additional optical resolution. If the original video was recorded at a
lower resolution, FFmpeg can upscale it, and the manifest warns accordingly.

The packet contains `manifest.json`, numbered PNGs and an **offline**
`index.html` contact sheet. Open `index.html` directly in your browser to
review the candidate organisms without a server or any remote requests.
The contact sheet is *not* a source for automatic species claims.
The packet folder must not already exist and must be under workspace
`reports/` or `exports/`. It does not modify any camera file.
If all cached boxes lack source PTS the command returns an explicit
abstention and creates no folder.

## Limits and scientific cautions

- Source files are **read** for this explicitly requested extraction, and
  source SHA-256 is checked twice; the command does not write originals.
- Object-coordinate scaling assumes boxes were generated from the standard
  384px-wide semantic sampler with unchanged aspect ratio, as in this repo.
- A crop of an organism-like region is not proof of a species or even a real
  organism. Bubbles, reflections, kelp and rock patterns are common confounders.
- Temporary tracking association is a hypothesis, **not** animal identity or
  biodiversity abundance.
- Small objects may be blurred. Evidence crop quality is bounded by the
  source footage, water turbidity, compression and actual optical resolution.
- New crop packets may reveal sensitive wildlife habitats or filming locations;
  keep the private reports off public repositories.

## Next evaluation

1. Inspect 10–20 true and false positive marine detections including
   fish schools, swaying algae, sea urchins and brief encounters.
2. Quantify frame PTS match rate and crop containment of the target organism.
3. Compare crop-level image embeddings against taxonomy-verified **licensed
   reference images**, with a documented train/test split by trip and location.
4. Keep an explicit `unknown` and genus/family-level output when distinctive
   anatomical details are absent; expert review is required before adding
   a verified species label.
5. Measure retrieval improvements on **held-out** recordings, not just
   examples used to choose prompts or reference photos.

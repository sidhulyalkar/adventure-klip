# Ecology context v1: species search priors without invented sightings

**Experimental, local-only, opt-in.** This is a species-search *candidate generator*
for indexed snorkeling videos, not a species classifier. It does not change
`klip process`, `adaptive-scenes`, the existing highlight rankings, or any
source media. It requires an explicit site and a frozen regional occurrence export.

## Why two evidence streams?

1. **Visual evidence:** cached OWLv2 generic query proposals (fish, ray, coral,
   kelp, etc.) or optional SigLIP2 image-to-species-name similarities. These are
   uncalibrated proposals, not species labels.
2. **Ecological context:** historical presence records near the user-provided
   location, conditioned conservatively on month, broad habitat tokens and depth
   when available. These are plausible search targets, *not* occupancy
   probabilities or evidence of organisms present in footage.

A high ecological score must never convert a generic fish proposal into a
species assertion. Unknown location, absent reference records and unreviewed
frames are not negative evidence. Algae, seagrasses and corals remain separate
biological groups: coral is animal, kelp is macroalga, seagrass is a flowering plant.

Data sources for your own exports:
- OBIS marine occurrences: https://obis.org/ and https://api.obis.org/
- GBIF occurrence search: https://www.gbif.org/occurrence/search
- WoRMS marine taxonomic lookup: https://www.marinespecies.org/aphia.php?p=webservice

**Download only a small, appropriate geographic subset** from those sites, with
provenance, citation and license. The current adapters accept JSON export/API
responses containing a `results` array; GBIF and OBIS may also offer other
export formats, which this first version does not parse. No direct API calls
are made by Klip. Source record geographic uncertainty, season, survey bias,
synonyms and historical changes remain material limitations.

## Prepare a local pilot (after existing processing, no full video rescan)

Find an exact snorkeling asset ID in the *existing* workspace:

```bash
export WS="$HOME/AdventureAI-clean.QJnopt"
sqlite3 "$WS/catalog.sqlite3" \
  "SELECT id,activity FROM assets WHERE activity='snorkeling' LIMIT 8;"
```

Make a *private* explicit site metadata file outside any published outputs:

```bash
cat > "$WS/reports/ecology-site-private.json" <<'JSON'
{
  "schemaVersion": 1,
  "source": "user_supplied",
  "latitude": 36.6,
  "longitude": -121.9,
  "horizontalUncertaintyKm": 2,
  "month": 7,
  "habitats": ["kelp_forest", "rocky_reef"],
  "depthMeters": 5
}
JSON
```

The numbers above are **illustrative only**. Replace them with the actual
recording region, correct month, realistic geographic uncertainty and
observed habitat. Do **not** copy these defaults to an unrelated recording,
infer location from wildlife, or publish this private input file.

Normalize an offline downloaded small GBIF JSON export:

```bash
klip --workspace "$WS" ecology-import \
  --provider gbif \
  --input "$HOME/Downloads/your-regional-occurrences.json" \
  --retrieved-at "2026-11-02" \
  --citation "Your dataset and download citation" \
  --license "Your exported source license" \
  --output "$WS/reports/occurrences-normalized-v1.json"
```

For OBIS, set `--provider obis`. The import is fully offline. Records
without valid coordinates, taxonomy, dataset and occurrence IDs are skipped,
not fabricated. Every record is source-attributed. Duplicate conflicting
identifiers are rejected.

By default, biological groups remain `other`, because taxonomy and common
names alone cannot safely imply ecology or organism type. To add
**manually verified** coarse groups, create an optional mapping:

```json
{
  "worms:REPLACE_WITH_VERIFIED_APHIA_ID": "macroalgae",
  "gbif:REPLACE_WITH_VERIFIED_TAXON_KEY": "fish"
}
```

Save to a local file and pass `--group-map PATH` during the import. Supported
groups include fish, marine_mammal, marine_reptile, invertebrate, coral,
macroalgae, seagrass, marine_plant and other. This is an **organism group
assignment**, not an accepted-name taxonomic validation; use WoRMS or GBIF
for accepted taxonomy and synonyms before merging references.

Then create a *new* per-asset context report:

```bash
klip --workspace "$WS" ecology-context \
  --asset "REPLACE_WITH_COMPLETE_64_CHARACTER_ASSET_SHA256" \
  --site "$WS/reports/ecology-site-private.json" \
  --snapshot "$WS/reports/occurrences-normalized-v1.json" \
  --radius-km 30 --top 30 \
  --output "$WS/reports/ecology-review-01.json"
```

Optional **local-only SigLIP2 text-image retrieval**, if a version-matched
completed `siglip2` cache and matching model weights already exist on disk:

```bash
klip --workspace "$WS" ecology-context \
  --asset "REPLACE_WITH_COMPLETE_64_CHARACTER_ASSET_SHA256" \
  --site "$WS/reports/ecology-site-private.json" \
  --snapshot "$WS/reports/occurrences-normalized-v1.json" \
  --top 30 --with-cached-siglip2 \
  --output "$WS/reports/ecology-retrieval-01.json"
```

This opt-in path embeds **text prompts once**, compares against previously
cached image vectors, and does not decode footage or download model weights.
If the exact weights, model revision or preprocessing are missing, the report
abstains and states why. Relative cosine similarity is *not* a probability,
diagnostic morphological identification, or verified animal sighting.

## What each candidate contains

- Source taxonomy ID, scientific name, group and rank, with the original
  snapshot checksum and dataset/record references.
- Bounded, heuristic **ecologicalPriority** used only for allocating a *search*
  budget, plus record count and independent dataset count. These aren't
  occurrence probabilities or local abundance.
- Broad visual object proposals from cached frames, explicitly unverified.
- Optional SigLIP2 text-image similarity and top candidate timestamps,
  explicitly uncalibrated and unreviewed.
- Historical same-*survey* candidate associations only when source records
  share an explicit dataset and survey ID. Never claimed as visual
  co-occurrence or a predator/prey relationship.

The generated report omits raw GPS coordinates and uses coarse distance bands.
The local **input site and occurrence snapshot still contain coordinates**:
keep them private, do not commit them to Git, and review ecology reports
before sharing location-sensitive observations. Fine-scale coordinates of
threatened species must not be exposed in public media or published metadata.

## Follow-on model architecture

After the above is qualified using confirmed videos and expert reference
images, the natural next modules are:

1. Taxonomy resolver: versioned WoRMS/GBIF accepted names, parent taxa,
   synonyms, common names and unresolved-taxonomy abstention.
2. Hierarchical vision: organism vs background -> fish/algae/coral/etc ->
   family/genus -> candidate species when morphology, temporal crops and
   geographically appropriate comparison sets support it.
3. Open-vocabulary detection and object tracking: localize organisms,
   de-duplicate detections, capture changing appearance, and rank encounters
   by duration, visibility, behavioral interest and novelty.
4. Ecological prior calibration: compare search recall and false-species
   assertions with and without priors using held-out trips/regions; account
   for dataset sampling bias and seasonal/spatial uncertainty.
5. Optional GPS/GPX and habitat layers: explicitly opted-in device metadata,
   bathymetry and protected-region data; propagate coordinate uncertainties
   and never infer exact location from a species guess.

**Release gates:** zero original-byte mutation, no hidden uploads, no species
labels invented from generic detector boxes, geographic provenance for every
candidate, rank/accepted-name hierarchy, taxon-level precision and recall
against expert-reviewed evidence, and no regression in rare-organism recall.

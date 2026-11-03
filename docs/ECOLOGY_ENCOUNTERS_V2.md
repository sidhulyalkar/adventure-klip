# Ecology v2: taxon provenance + cached encounter review

Experimental, opt-in, local-only. This PR adds **two bounded review tools** on
top of ecology v1. It does not modify `klip process`, default semantic
extraction, your current histogram/batched pilot, editor output or source bytes.

## Why this release

A plausible regional species, a visible generic OWLv2 object, a tracked box
and a human-confirmed species are **four different evidence types**. Never
conflate them. Names in occurrence databases can also be synonyms; OBIS
exports may include explicit absences or QC-excluded records. This release
keeps those separate.

Official references:

- WoRMS taxonomy and accepted names:
  https://www.marinespecies.org/aphia.php?p=webservice
- OBIS occurrence quality, including `absence` and `dropped`:
  https://portal.obis.org/data/access/
- OBIS occurrence/event documentation:
  https://manual.obis.org/format_occurrence.html

## A. Create encounter *review proposals* without decoding another frame

Use the existing workspace and an exact snorkeling asset ID. This operation
only reads the local SQLite catalog and verified completed OWLv2 semantic
JSON caches. It doesn't require your external SSD to be mounted.

```bash
export WS="$HOME/AdventureAI-clean.QJnopt"
sqlite3 "$WS/catalog.sqlite3" \
  "SELECT id,activity,duration FROM assets WHERE activity='snorkeling' LIMIT 8;"
klip --workspace "$WS" ecology-encounters \
  --asset REPLACE_WITH_COMPLETE_SHA256 \
  --max-results 40 \
  --max-gap-ms 5000 \
  --output "$WS/reports/ecology-encounters-01.json"
```

The result offers bounded source-relative review windows, broad object labels,
object-box count, sampled-frame count, peak **uncalibrated** detector score,
source-provider/model provenance and the original sampled timestamps.
No species name or unique-animal count is produced. Two sequential fish boxes
may be the same individual, different fish, an imperfect track, or false
detections. Long gaps deliberately break continuity. Short clips include
three seconds before the first sampled detection and up to five after the
last, bounded by recording duration; timestamps need manual adjustment.

If caches are absent, you get `no_cached_broad_visual_evidence`, not an
assertion that no animals existed in the video. On your current histogram-only
pilot this is the expected result until you run the **separate optional**
`ecology-owlv2` semantic pass from v1.

## B. Resolve accepted names and synonyms using an explicit frozen catalog

Create a small **independently verified** private JSON snapshot. Example IDs
and taxa below are synthetic placeholders, NOT valid WoRMS records:

```json
{
  "schemaVersion": 1,
  "source": {
    "provider": "WoRMS frozen, manually reviewed",
    "retrievedAt": "2026-11-02",
    "citation": "Supply actual data citation",
    "license": "Supply actual license"
  },
  "taxa": [
    {
      "taxonId": "worms:REPLACE_ACCEPTED_ID",
      "scientificName": "Verified accepted name",
      "rank": "species",
      "group": "fish",
      "status": "accepted"
    },
    {
      "taxonId": "worms:REPLACE_SYNONYM_ID",
      "scientificName": "Verified synonym name",
      "rank": "species",
      "group": "fish",
      "status": "synonym",
      "acceptedTaxonId": "worms:REPLACE_ACCEPTED_ID"
    }
  ]
}
```

The resolver requires exact IDs, accepted targets, consistent ranks/groups,
valid parent taxon references and no conflicting or cyclic relationships.
It does **not** fuzzy-match names or invent links between WoRMS and GBIF IDs.

Pass the snapshot to the existing ecology-context command:

```bash
klip --workspace "$WS" ecology-context \
  --asset REPLACE_WITH_COMPLETE_SHA256 \
  --site "$WS/reports/ecology-site-private.json" \
  --snapshot "$WS/reports/occurrences-normalized-v1.json" \
  --taxonomy "$WS/reports/taxonomy-private.json" \
  --with-encounters \
  --output "$WS/reports/ecology-with-encounters-01.json"
```

`--taxonomy` consolidates only explicit synonyms in the returned shortlisted
candidates. It retains each source taxon ID and **does not sum**
occurrence counts or confidence scores across aliases. Missing taxa remain
unresolved, with no negative claim.

`--with-encounters` attaches the source-backed review queue. It remains
group-level visual evidence, **not** proof of the occurrence shortlist's
species identities.

## C. Validation priorities for the next phase

- Select at least one snorkeling clip containing multiple similar-looking fish,
  one moving camera with coral/kelp and one very brief wildlife appearance.
- Collect visible **expert-reviewed** taxonomy labels at the most specific
  defensible level (species, genus, family, group, unknown).
- Measure how often tracks fragment, merge, miss a brief appearance or flag
  water bubbles/rocks as fauna.
- Evaluate reference-image / crop-level retrieval and morphology only
  after a curated reference corpus, annotation standard and trip/region held-out
  data exist. Do not add individual counts or biodiversity richness metrics
  based solely on detector boxes or ecological priors.

**Privacy:** Private site snapshots and external occurrences may contain
fine-scale locations. Keep them outside git commits and any public report.
Output paths are new, in workspace `exports/` or `reports/`, never the
indexed video source. Original source bytes are not opened or modified.

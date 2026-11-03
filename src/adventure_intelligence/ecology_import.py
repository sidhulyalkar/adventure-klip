"""Offline import adapters for user-exported GBIF and OBIS JSON.

Only explicitly georeferenced and named records are retained. Source exports
may include unsupported taxa/ranks; those are counted as skipped, not guessed.
No API calls, requests, GPS inference, or footage inspection.
"""
from __future__ import annotations

from datetime import date
import json
import math
from pathlib import Path

from .ecology_context import ALLOWED_GROUPS, _read_json, _str, validate_snapshot

WORMS_RANKS = {220: "species", 230: "subspecies", 180: "genus",
               140: "family", 100: "order", 60: "class", 30: "phylum"}
GBIF_RANKS = {x.upper(): x for x in
              ("species", "subspecies", "genus", "family", "order", "class", "phylum")}


def _get(row: dict, *keys):
    for k in keys:
        v = row.get(k)
        if v is not None and v != "":
            return v
    return None


def _number(v, lower: float, upper: float):
    try:
        n = float(v)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(n) or not lower <= n <= upper:
        return None
    return n


def _observed_month(row: dict):
    direct = _get(row, "month", "eventMonth")
    if type(direct) is int and 1 <= direct <= 12:
        return direct
    try:
        if direct is not None and 1 <= int(direct) <= 12:
            return int(direct)
    except (ValueError, TypeError):
        pass
    # Identification date is not the observation date; do not infer season from it.
    event = _get(row, "eventDate")
    if isinstance(event, str) and len(event) >= 7 and event[4] == "-":
        try:
            return date.fromisoformat(event[:10]).month
        except ValueError:
            pass
    return None


def _source_rows(source: dict):
    rows = source.get("results")
    if not isinstance(rows, list):
        raise ValueError("Export requires a JSON 'results' array")
    if len(rows) > 12000:
        raise ValueError("Limit input to 12000 records; use a smaller regional export")
    return rows


def normalize_export(raw: dict, provider: str, *, retrieved_at: str,
                     citation: str, license: str, group_map: dict | None = None) -> dict:
    if provider not in {"obis", "gbif"}:
        raise ValueError("Unsupported ecology export provider")
    if not isinstance(raw, dict):
        raise ValueError("Raw export must be a JSON object")
    provenance = {"provider": provider.upper(), "retrievedAt": _str(retrieved_at, "retrievedAt"),
                  "citation": _str(citation, "citation", 400),
                  "license": _str(license, "license", 400)}
    if group_map is None:
        group_map = {}
    if not isinstance(group_map, dict) or len(group_map) > 12000:
        raise ValueError("Group map must be a bounded JSON mapping")
    if any(not isinstance(v, str) or v not in ALLOWED_GROUPS
           or not isinstance(k, str) for k, v in group_map.items()):
        raise ValueError("Group map must contain explicit supported biological groups")
    records, skipped = [], 0
    for row in _source_rows(raw):
        if not isinstance(row, dict):
            skipped += 1
            continue
        if provider == "gbif":
            key = _get(row, "acceptedTaxonKey", "taxonKey")
            rank = GBIF_RANKS.get(str(_get(row, "taxonRank", "rank")).upper())
            name = _get(row, "acceptedScientificName", "scientificName")
            record_id = _get(row, "gbifID", "key", "occurrenceID")
            dataset = _get(row, "datasetKey", "datasetID")
            taxon_id = "gbif:" + str(key) if key is not None else None
        else:
            key = _get(row, "acceptedAphiaID", "aphiaID")
            raw_rank = _get(row, "taxonRank")
            try:
                rank = WORMS_RANKS.get(int(raw_rank))
            except (TypeError, ValueError):
                rank = GBIF_RANKS.get(str(raw_rank).upper())
            name = _get(row, "acceptedNameUsage", "scientificName", "scientificname")
            record_id = _get(row, "occurrenceID", "id")
            dataset = _get(row, "datasetID", "datasetId", "dataset_id")
            taxon_id = "worms:" + str(key) if key is not None else None
        latitude = _number(_get(row, "decimalLatitude"), -90, 90)
        longitude = _number(_get(row, "decimalLongitude"), -180, 180)
        if (not taxon_id or not rank or not name or record_id is None
                or dataset is None or latitude is None or longitude is None):
            skipped += 1
            continue
        uncertainty = _number(row.get("coordinateUncertaintyInMeters", 0),
                              0, 1_000_000)
        depth = _number(_get(row, "depth", "minimumDepthInMeters"), 0, 11000)
        if uncertainty is None:
            skipped += 1
            continue
        survey = _get(row, "eventID")
        item = {
            "recordId": str(record_id), "taxonId": taxon_id,
            "scientificName": name, "rank": rank,
            "group": group_map.get(taxon_id, "other"),
            "datasetId": str(dataset), "latitude": latitude,
            "longitude": longitude, "coordinateUncertaintyMeters": uncertainty,
            "month": _observed_month(row), "depthMeters": depth,
            "habitats": [], "surveyId": str(survey) if survey is not None else None,
        }
        records.append(item)
    doc = {"schemaVersion": 1, "source": provenance,
           "importStatistics": {"inputRecords": len(_source_rows(raw)),
                                "retainedRecords": len(records),
                                "skippedRecords": skipped,
                                "groupingPolicy": "explicit_group_map_else_other"},
           "records": records}
    # Ensures source IDs/taxa are internally coherent before writing output.
    validate_snapshot(doc)
    return doc


def import_export(input_path: Path, provider: str, output_path: Path, *,
                  retrieved_at: str, citation: str, license: str,
                  group_map_path: Path | None = None) -> dict:
    raw, _ = _read_json(input_path)
    groups = None
    if group_map_path is not None:
        groups, _ = _read_json(group_map_path, max_bytes=1_000_000)
    snapshot = normalize_export(raw, provider, retrieved_at=retrieved_at,
                                citation=citation, license=license, group_map=groups)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return {"output": str(output_path), **snapshot["importStatistics"],
            "provider": snapshot["source"]["provider"]}

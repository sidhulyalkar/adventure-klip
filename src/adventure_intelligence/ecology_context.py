"""Local, evidence-separated ecological search context for adventure footage.

This module does NOT identify organisms in footage.  It derives a bounded,
un-calibrated candidate shortlist from a user-supplied location and a frozen
occurrence snapshot, then attaches broad cached OWLv2 visual proposals.  Its
output is a *search plan*, not a detection, sighting, or biological inventory.

No network, media reads/decodes, taxonomy guesses, or source-file mutations.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
import math
from pathlib import Path

VERSION = "ecology-prior-v1"
ALLOWED_RANKS = {"species", "subspecies", "genus", "family", "order", "class", "phylum"}
ALLOWED_GROUPS = {
    "fish", "marine_mammal", "marine_reptile", "invertebrate",
    "coral", "macroalgae", "seagrass", "marine_plant", "other",
}
# These labels are *object-level queries*, not taxonomy or species identities.
OBJECT_GROUPS = {
    "fish": "fish", "shark": "fish", "ray": "fish", "eel": "fish",
    "sea turtle": "marine_reptile", "dolphin": "marine_mammal",
    "whale": "marine_mammal", "sea lion": "marine_mammal",
    "sea otter": "marine_mammal",
    "octopus": "invertebrate", "jellyfish": "invertebrate",
    "sea urchin": "invertebrate", "sea anemone": "invertebrate",
    "sponge": "invertebrate", "crab": "invertebrate", "lobster": "invertebrate",
    "nudibranch": "invertebrate", "sea star": "invertebrate",
    "coral": "coral", "kelp": "macroalgae", "seagrass": "seagrass",
}


def _read_json(path: Path, max_bytes: int = 15_000_000) -> tuple[dict, str]:
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("Input must be an existing, size-bounded local JSON file")
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise ValueError("Oversized ecology JSON")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Ecology JSON root must be an object")
    return data, sha256(raw).hexdigest()


def _finite(value, name: str, low: float, high: float) -> float:
    if (not isinstance(value, (int, float)) or isinstance(value, bool)
            or not math.isfinite(value) or not low <= value <= high):
        raise ValueError(f"Invalid {name}")
    return float(value)


def _str(value, field: str, limit: int = 180) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit:
        raise ValueError(f"Invalid {field}")
    return value.strip()


def _habitats(value) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError("habitats must be a short list")
    values = set()
    for item in value:
        token = _str(item, "habitat", 45).lower()
        if not all(c.isalnum() or c in "_-" for c in token):
            raise ValueError("habitats must use normalized tokens")
        values.add(token)
    return tuple(sorted(values))


def _month(value, label: str):
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= 12:
        raise ValueError(f"Invalid {label}")
    return value


def validate_site(site: dict) -> dict:
    if site.get("schemaVersion") != 1:
        raise ValueError("Unsupported site schemaVersion")
    if "latitude" not in site or "longitude" not in site:
        raise ValueError("Explicit site latitude and longitude are required")
    lat = _finite(site["latitude"], "latitude", -90, 90)
    lon = _finite(site["longitude"], "longitude", -180, 180)
    uncertainty = _finite(site.get("horizontalUncertaintyKm", 0),
                          "horizontalUncertaintyKm", 0, 500)
    month = _month(site.get("month"), "month")
    depth = site.get("depthMeters")
    if depth is not None:
        depth = _finite(depth, "depthMeters", 0, 11000)
    source = _str(site.get("source", "user_supplied"), "source", 120)
    habitats = _habitats(site.get("habitats", []))
    return {"latitude": lat, "longitude": lon,
            "horizontalUncertaintyKm": uncertainty,
            "month": month, "depthMeters": depth,
            "source": source, "habitats": habitats}


def validate_snapshot(snapshot: dict) -> tuple[dict, list[dict]]:
    if snapshot.get("schemaVersion") != 1:
        raise ValueError("Unsupported snapshot schemaVersion")
    source = snapshot.get("source")
    if not isinstance(source, dict):
        raise ValueError("Snapshot requires external-source provenance")
    provenance = {key: _str(source.get(key), key, 400)
                  for key in ("provider", "retrievedAt", "citation", "license")}
    records = snapshot.get("records")
    if not isinstance(records, list) or len(records) > 12000:
        raise ValueError("Invalid bounded occurrence records")
    normalized = []
    seen = {}
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("Occurrence must be an object")
        taxon = _str(row.get("taxonId"), "taxonId")
        name = _str(row.get("scientificName"), "scientificName")
        rank = _str(row.get("rank"), "rank").lower()
        group = _str(row.get("group"), "group").lower()
        if rank not in ALLOWED_RANKS or group not in ALLOWED_GROUPS:
            raise ValueError("Unknown taxon rank or biological group")
        record_id = _str(row.get("recordId"), "recordId")
        dataset = _str(row.get("datasetId"), "datasetId")
        lat = _finite(row.get("latitude"), "record latitude", -90, 90)
        lon = _finite(row.get("longitude"), "record longitude", -180, 180)
        uncertainty = row.get("coordinateUncertaintyMeters")
        if uncertainty is not None:
            uncertainty = _finite(uncertainty, "coordinateUncertaintyMeters", 0, 1_000_000)
        month = _month(row.get("month"), "record month")
        depth = row.get("depthMeters")
        if depth is not None:
            depth = _finite(depth, "record depthMeters", 0, 11000)
        survey = row.get("surveyId")
        if survey is not None:
            survey = _str(survey, "surveyId")
        item = dict(taxonId=taxon, scientificName=name, rank=rank, group=group,
                    recordId=record_id, datasetId=dataset, latitude=lat, longitude=lon,
                    coordinateUncertaintyMeters=uncertainty, month=month,
                    depthMeters=depth, habitats=_habitats(row.get("habitats", [])),
                    surveyId=survey)
        key = (dataset, record_id)
        if key in seen:
            if seen[key] != item:
                raise ValueError("Conflicting duplicated occurrence record")
            continue
        seen[key] = item
        normalized.append(item)
    # A stable key must not silently refer to different taxonomy elsewhere.
    accepted = {}
    for item in normalized:
        t = item["taxonId"]
        identity = (item["scientificName"], item["rank"], item["group"])
        if t in accepted and accepted[t] != identity:
            raise ValueError("Conflicting taxon identity; resolve synonyms upstream")
        accepted[t] = identity
    return provenance, normalized


def _distance_km(lat_a, lon_a, lat_b, lon_b) -> float:
    dlat = math.radians(lat_b - lat_a)
    dlon = math.radians(lon_b - lon_a)
    aa = (math.sin(dlat / 2) ** 2 +
          math.cos(math.radians(lat_a)) * math.cos(math.radians(lat_b))
          * math.sin(dlon / 2) ** 2)
    return 12742.0 * math.asin(min(1.0, math.sqrt(aa)))


def _weight(item: dict, site: dict, radius_km: float):
    distance = _distance_km(site["latitude"], site["longitude"],
                            item["latitude"], item["longitude"])
    if distance > radius_km or (item["coordinateUncertaintyMeters"] is not None
                                and item["coordinateUncertaintyMeters"] > 10_000):
        return None
    # Relative search heuristic, not a probability or occupancy estimate.
    spatial = 0.25 + 0.75 * max(0., 1 - distance / radius_km)
    imonth, smonth = item["month"], site["month"]
    month_delta = None if smonth is None or imonth is None else min(
        abs(smonth - imonth), 12 - abs(smonth - imonth))
    seasonal = 1. if month_delta is None else max(.4, 1 - .12 * month_delta)
    habitat = 1.
    if site["habitats"] and item["habitats"]:
        habitat = 1. if set(site["habitats"]) & set(item["habitats"]) else .55
    depth = 1.
    if site["depthMeters"] is not None and item["depthMeters"] is not None:
        depth = max(.45, 1 - abs(site["depthMeters"] - item["depthMeters"]) / 100)
    spatial_quality = (.6 if item["coordinateUncertaintyMeters"] is None else
                       max(.5, 1 - item["coordinateUncertaintyMeters"] / 20_000))
    return round(spatial * seasonal * habitat * depth * spatial_quality, 6), distance


def _visual_groups(db, workspace: Path, asset_id: str) -> dict:
    from .scene_atlas import frames_for_asset
    result = defaultdict(lambda: {"labels": set(), "times": set(), "providers": set()})
    frames_by_provider = []
    for provider in ("owlv2", "ecology-owlv2"):
        frames, _ = frames_for_asset(db, workspace, asset_id, provider)
        frames_by_provider.extend((provider, frame) for frame in frames)
    for provider, frame in frames_by_provider:
        t = frame.get("decodedPtsMs")
        if type(t) is not int or t < 0:
            t = frame.get("tMs")
        for obj in frame.get("objects", []):
            if not isinstance(obj, dict):
                continue
            label = obj.get("label")
            group = OBJECT_GROUPS.get(label.lower().strip()) if isinstance(label, str) else None
            score = obj.get("score")
            if group is None or not isinstance(score, (int, float)) or isinstance(score, bool):
                continue
            if not math.isfinite(score) or not 0 <= score <= 1:
                continue
            result[group]["labels"].add(label.strip().lower())
            result[group]["providers"].add(provider)
            if type(t) is int and t >= 0:
                result[group]["times"].add(t)
    return {group: {"labels": sorted(v["labels"]),
                    "providers": sorted(v["providers"]),
                    "sampleTimesMs": sorted(v["times"])[:40],
                    "sampleCount": len(v["times"]),
                    "status": "unverified_broad_object_query"}
            for group, v in sorted(result.items())}


def build_ecology_context(workspace: Path, asset_id: str,
                          site_file: Path, snapshot_file: Path, *,
                          radius_km: float = 30., top: int = 30,
                          with_cached_siglip2: bool = False,
                          taxonomy_file: Path | None = None,
                          with_encounters: bool = False) -> dict:
    """Return local-only context, with locations omitted from output by default."""
    if not isinstance(asset_id, str) or len(asset_id) != 64 or any(
            c not in "0123456789abcdef" for c in asset_id):
        raise ValueError("Expected exact lowercase SHA-256 asset ID")
    radius = _finite(radius_km, "radiusKm", 0.5, 250)
    if type(top) is not int or not 1 <= top <= 100:
        raise ValueError("top must be 1..100")
    site_doc, site_sha = _read_json(site_file, max_bytes=100_000)
    source_doc, source_sha = _read_json(snapshot_file)
    site = validate_site(site_doc)
    provenance, records = validate_snapshot(source_doc)
    from .catalog import connect
    with connect(workspace) as db:
        row = db.execute("SELECT id,activity FROM assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise ValueError("Asset not present in the selected workspace")
        if row["activity"] != "snorkeling":
            raise ValueError("Ecology v1 requires an explicitly classified snorkeling asset")
        visual = _visual_groups(db, workspace, asset_id)

    if site["horizontalUncertaintyKm"] > radius:
        # Widening the radius without consent could invent locally plausible taxa.
        return {"schemaVersion": 1, "version": VERSION, "assetId": asset_id,
                "status": "abstained_site_uncertainty_exceeds_search_radius",
                "sourceSnapshotSha256": source_sha, "siteSha256": site_sha,
                "locationCoordinatesInOutput": False,
                "candidates": [], "broadVisualProposals": visual,
                "limitations": ["Uncertain site is not used as species presence evidence"]}
    evidence = defaultdict(list)
    nearby = []
    for item in records:
        weighted = _weight(item, site, radius)
        if weighted is not None:
            weight, distance = weighted
            evidence[item["taxonId"]].append((weight, distance, item))
            nearby.append(item)
    candidates = []
    for taxon, rows in evidence.items():
        rows.sort(key=lambda pair: (-pair[0], pair[2]["datasetId"], pair[2]["recordId"]))
        dataset_weights = {}
        for weight, _, item in rows:
            dataset_weights.setdefault(item["datasetId"], []).append(weight)
        # Cap multiple records from one dataset. Independent datasets add only
        # a small diversity bonus; collection effort does not equal abundance.
        best_by_dataset = [max(v) for v in dataset_weights.values()]
        ranking = min(1., max(best_by_dataset) * .80 +
                      min(3, len(best_by_dataset) - 1) * .06)
        best = rows[0][2]
        group = best["group"]
        generic = visual.get(group)
        candidates.append({
            "taxonId": taxon, "scientificName": best["scientificName"],
            "taxonRank": best["rank"], "biologicalGroup": group,
            "status": "unverified_ecological_search_candidate",
            "videoConfirmation": "not_evaluated",
            "speciesIdentifiedInVideo": False,
            "ecologicalPriority": round(ranking, 5),
            "supportingRecords": len(rows),
            "recordsWithUnknownCoordinateUncertainty": sum(
                x[2]["coordinateUncertaintyMeters"] is None for x in rows),
            "independentDatasets": len(dataset_weights),
            # Public occurrence IDs plus precise distances could reconstruct
            # a private filming location; publish only a coarse proximity band.
            "nearestRecordDistanceBand": (
                "within_10_km" if min(x[1] for x in rows) < 10 else
                "10_to_30_km" if min(x[1] for x in rows) < 30 else
                "30_to_100_km" if min(x[1] for x in rows) < 100 else
                "100_to_250_km"),
            "exampleReferences": [
                {"datasetId": r["datasetId"], "recordId": r["recordId"]}
                for _, _, r in rows[:3]],
            "broadVisualProposal": generic if generic else None,
            "searchPriority": round(min(1., ranking + (.08 if generic else 0)), 5),
            "needsVisualSpeciesValidation": True,
        })
    candidates.sort(key=lambda c: (-c["searchPriority"],
                                    -c["independentDatasets"], c["taxonId"]))
    # Only surveys with explicit matching survey IDs and dataset IDs can give
    # a cautious historical *same-survey* association. Never imply that the
    # organisms were simultaneously visible in footage.
    surveys = defaultdict(set)
    for item in nearby:
        if item["surveyId"] is not None:
            surveys[(item["datasetId"], item["surveyId"])].add(item["taxonId"])
    pairs = defaultdict(int)
    for taxa in surveys.values():
        if len(taxa) > 30:  # Huge inventory surveys do not establish close contact.
            continue
        ordered = sorted(taxa)
        for i, left in enumerate(ordered):
            for right in ordered[i + 1:]:
                pairs[(left, right)] += 1
    selected = {x["taxonId"] for x in candidates[:top]}
    associations = [{"taxonIds": [left, right], "sharedExternalSurveyCount": count,
                     "status": "historical_same_survey_not_video_cooccurrence"}
                    for (left, right), count in sorted(
                        pairs.items(), key=lambda e: (-e[1], e[0]))
                    if left in selected and right in selected][:50]
    report = {
        "schemaVersion": 1, "version": VERSION, "assetId": asset_id,
        "status": "ecological_shortlist_only" if candidates else "no_nearby_snapshot_records",
        "sourceSnapshotSha256": source_sha, "siteSha256": site_sha,
        "source": provenance,
        "context": {"radiusKm": radius, "siteUncertaintyKm": site["horizontalUncertaintyKm"],
                    "month": site["month"], "habitats": list(site["habitats"]),
                    "depthMeters": site["depthMeters"],
                    "coordinateStatus": "explicit_user_supplied_local_only",
                    "preciseCoordinatesOmitted": True},
        "inputRecordCount": len(records), "nearbyRecordCount": len(nearby),
        "candidates": candidates[:top], "broadVisualProposals": visual,
        "neighborAssociations": associations,
        "limitations": [
            "Occurrence records are not video sightings, occupancy probabilities, or abundance.",
            "OWLv2 broad-group scores are uncalibrated hypotheses, not species IDs.",
            "Proximity and same-survey associations do not prove simultaneous presence.",
            "Snapshot sampling effort, taxonomy, season and habitat coverage can be biased.",
            "Taxa absent from this snapshot are unknown, not absent from the environment.",
        ],
    }
    if taxonomy_file is not None:
        from .ecology_taxonomy import load_taxonomy, reconcile_taxa
        source, digest, nodes = load_taxonomy(taxonomy_file)
        reconciled = reconcile_taxa(report["candidates"], nodes)
        report["candidates"] = reconciled["candidates"]
        for candidate in report["candidates"]:
            # A reviewed crosswalk may correct a generic "other" taxon group.
            # Attach any existing broad-group detector evidence, but do not
            # change scores or claim the named taxon is visually present.
            if candidate.get("broadVisualProposal") is None:
                candidate["broadVisualProposal"] = visual.get(
                    candidate["biologicalGroup"])
        report["taxonomyCrosswalk"] = {
            "version": reconciled["version"], "source": source,
            "snapshotSha256": digest,
            "sourceCandidateCount": reconciled["sourceCandidateCount"],
            "canonicalCandidateCount": reconciled["canonicalCandidateCount"],
            "unresolvedSourceCandidates": reconciled["unresolvedSourceCandidates"],
            "limitations": reconciled["limitations"],
        }
    if with_encounters:
        from .ecology_encounters import build_encounter_report
        report["encounterReview"] = build_encounter_report(workspace, asset_id)
    if with_cached_siglip2:
        from .ecology_embeddings import cached_siglip2_retrieval
        retrieval = cached_siglip2_retrieval(workspace, asset_id, report["candidates"])
        report["embeddingRetrieval"] = {
            "status": retrieval["status"],
            "modelKeySha256": retrieval.get("modelKeySha256"),
            "note": "Never a confirmed species sighting or calibrated classifier",
        }
        for candidate in report["candidates"]:
            result = retrieval["proposals"].get(candidate["taxonId"])
            if result is not None:
                candidate["visualRetrieval"] = result
    return report

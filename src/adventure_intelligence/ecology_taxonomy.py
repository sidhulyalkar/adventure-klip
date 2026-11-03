"""Versioned, explicit taxonomic crosswalk for ecological search candidates.

Only exact IDs from an operator-supplied frozen catalog are resolved.  No fuzzy
name guessing, network requests, inferred synonym edges, or biological sightings.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path

from .ecology_context import ALLOWED_GROUPS, ALLOWED_RANKS, _read_json, _str

VERSION = "ecology-taxonomy-crosswalk-v1"
# Bigger index means more general classification.
LEVEL = {"subspecies": 0, "species": 1, "genus": 2, "family": 3,
         "order": 4, "class": 5, "phylum": 6}


def load_taxonomy(path: Path) -> tuple[dict, str, dict]:
    """Read a user-curated, cited snapshot. Reject conflicting/circular mappings."""
    doc, checksum = _read_json(path)
    if doc.get("schemaVersion") != 1:
        raise ValueError("Unsupported taxonomy schema")
    provenance = doc.get("source")
    if not isinstance(provenance, dict):
        raise ValueError("Taxonomy provenance required")
    source = {key: _str(provenance.get(key), key, 400)
              for key in ("provider", "retrievedAt", "citation", "license")}
    records = doc.get("taxa")
    if not isinstance(records, list) or len(records) > 12000:
        raise ValueError("Taxonomy catalog must contain <=12000 taxa")
    nodes = {}
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("Taxonomy node must be an object")
        tid = _str(item.get("taxonId"), "taxonId")
        name = _str(item.get("scientificName"), "scientificName")
        rank = _str(item.get("rank"), "rank").lower()
        group = _str(item.get("group"), "group").lower()
        status = item.get("status")
        if rank not in ALLOWED_RANKS or group not in ALLOWED_GROUPS:
            raise ValueError("Invalid taxonomy rank or group")
        if status not in {"accepted", "synonym", "unresolved"}:
            raise ValueError("Taxonomic status must be accepted, synonym or unresolved")
        accepted = item.get("acceptedTaxonId")
        parent = item.get("parentTaxonId")
        if accepted is not None:
            accepted = _str(accepted, "acceptedTaxonId")
        if parent is not None:
            parent = _str(parent, "parentTaxonId")
        if status == "synonym" and accepted is None:
            raise ValueError("Synonyms require a canonical acceptedTaxonId")
        if status != "synonym" and accepted is not None:
            raise ValueError("Only synonyms may have acceptedTaxonId")
        if parent == tid or accepted == tid or tid in nodes:
            raise ValueError("Self-link or duplicate taxon ID")
        nodes[tid] = {
            "taxonId": tid, "scientificName": name, "rank": rank,
            "group": group, "status": status,
            "acceptedTaxonId": accepted, "parentTaxonId": parent,
        }

    for tid, node in nodes.items():
        if node["status"] == "synonym":
            accepted = nodes.get(node["acceptedTaxonId"])
            if accepted is None or accepted["status"] != "accepted":
                raise ValueError("Synonym must target an explicit accepted taxon")
            if (node["rank"] != accepted["rank"]
                    or node["group"] != accepted["group"]):
                raise ValueError("Synonym rank/group cannot change silently")
        parent = node["parentTaxonId"]
        if parent is not None:
            ancestor = nodes.get(parent)
            if ancestor is None or ancestor["status"] != "accepted":
                raise ValueError("Parent must exist as accepted taxon")
            if (LEVEL[ancestor["rank"]] <= LEVEL[node["rank"]]
                    or ancestor["group"] not in {node["group"], "other"}):
                raise ValueError("Incompatible parent hierarchy")
    for tid in nodes:
        visited = set()
        current = tid
        while current is not None:
            if current in visited:
                raise ValueError("Circular taxonomic hierarchy")
            visited.add(current)
            current = nodes[current]["parentTaxonId"]
    return source, checksum, nodes


def reconcile_taxa(candidates: list[dict], nodes: dict) -> dict:
    """Annotate exact-ID matches; return grouped candidates without summing counts.

    A confirmed reference taxonomy is not a confirmed video observation.
    Unknown IDs stay unchanged, never a guessed species or false absence.
    """
    groups = defaultdict(list)
    unresolved = 0
    for candidate in candidates:
        tid = candidate["taxonId"]
        record = nodes.get(tid)
        if record is None:
            canonical = tid
            resolution = "not_in_curated_crosswalk"
            unresolved += 1
        elif record["status"] == "synonym":
            canonical = record["acceptedTaxonId"]
            resolution = "explicit_synonym_crosswalk"
        elif record["status"] == "accepted":
            canonical = tid
            resolution = "accepted_snapshot_match"
        else:
            canonical = tid
            resolution = "taxon_unresolved"
            unresolved += 1
        groups[canonical].append((candidate, resolution, record))

    resolved = []
    for canonical, entries in groups.items():
        entries.sort(key=lambda e: (-e[0]["searchPriority"], e[0]["taxonId"]))
        lead, _, _ = entries[0]
        canon = nodes.get(canonical)
        # Preserve the best source-prior score, never add probabilities,
        # source records, or visual detections across possible synonyms.
        record = {
            **lead,
            "taxonId": canonical,
            "scientificName": (canon["scientificName"]
                               if canon is not None and canon["status"] == "accepted"
                               else lead["scientificName"]),
            "taxonomy": {
                "status": ("explicit_canonical_match"
                           if canon is not None and canon["status"] == "accepted"
                           else "unresolved"),
                "canonicalTaxonId": canonical,
                "canonicalRank": canon["rank"] if canon else None,
                "sourceTaxonIds": sorted({e[0]["taxonId"] for e in entries}),
                "sourceResolution": {e[0]["taxonId"]: e[1] for e in entries},
                "source": "offline_curated_crosswalk",
                "isVideoSpeciesIdentification": False,
            },
        }
        resolved.append(record)
    resolved.sort(key=lambda c: (-c["searchPriority"], c["taxonId"]))
    return {
        "version": VERSION, "candidates": resolved,
        "sourceCandidateCount": len(candidates),
        "canonicalCandidateCount": len(resolved),
        "unresolvedSourceCandidates": unresolved,
        "limitations": [
            "Exact-ID crosswalk only; catalog taxonomy may be stale or incomplete.",
            "A resolved accepted name is not evidence the organism appears in footage.",
            "Merged aliases do not sum occurrence counts, detection evidence or confidence.",
        ],
    }

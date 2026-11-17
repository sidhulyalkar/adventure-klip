"""Append-only ledger of owner judgments across review schemas.

One record = one human judgment about one source interval. Adapters read the
frozen review exports and never modify them. Importing does not adjudicate:
overlapping judgments are reported as matches or conflicts, and a label is
never transferred to a different interval, goal or source.

Model proposals do not belong here.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import os

VERSION = "klip-owner-evidence-ledger-v1"
DECISIONS = ("keep", "skip", "unsure")
# How the judged interval was chosen. Windows a model or heuristic picked are
# biased samples: usable as ranking evidence, not for measuring recall.
SELECTIONS = ("pipeline_selected_window", "owner_marked_span")
UNASSIGNED = "unassigned"
_HEX = set("0123456789abcdef")


def _is_sha(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def file_digest(path: Path) -> str:
    return sha256(Path(path).read_bytes()).hexdigest()


def record(*, source_sha: str, start_ms: int, end_ms: int, decision: str,
           selection: str, schema: str, export_digest: str, record_id: str,
           sample_id: str | None = None, goal: str | None = None,
           outing: str | None = None, detail: dict | None = None) -> dict:
    if not _is_sha(source_sha) or not _is_sha(export_digest):
        raise ValueError("Evidence needs full source and export SHA-256 digests")
    if type(start_ms) is not int or type(end_ms) is not int or not 0 <= start_ms < end_ms:
        raise ValueError("Evidence interval must be 0 <= startMs < endMs")
    if decision not in DECISIONS or selection not in SELECTIONS:
        raise ValueError("Unknown decision or selection basis")
    body = {
        "version": VERSION, "sourceSha256": source_sha,
        "startMs": start_ms, "endMs": end_ms, "decision": decision,
        "selection": selection, "goal": goal or "unspecified",
        "outing": outing or UNASSIGNED, "reviewer": "owner",
        "schema": schema, "exportSha256": export_digest,
        "sampleId": sample_id, "recordId": record_id,
        "detail": detail or {},
    }
    # Outing is assigned later and must not change a judgment's identity.
    identity = {k: v for k, v in body.items() if k != "outing"}
    body["evidenceId"] = sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:32]
    return body


def _window_reviews(manifest: dict, reviews: list[dict], *, schema: str,
                    digest: str, detail_keys: tuple[str, ...]) -> list[dict]:
    clips = {c["recordId"]: c for c in manifest["clips"]}
    out = []
    for item in reviews:
        clip = clips[item["recordId"]]
        if item["sourceSha256"] != clip["sourceSha256"]:
            raise ValueError("Review source does not match its frozen sample")
        common = dict(source_sha=clip["sourceSha256"], decision=item["decision"],
                      schema=schema, export_digest=digest,
                      sample_id=manifest["sampleId"])
        out.append(record(
            start_ms=clip["sourceStartMs"], end_ms=clip["sourceEndMs"],
            selection="pipeline_selected_window", record_id=item["recordId"],
            detail={k: item[k] for k in detail_keys if k in item}, **common))
        a, b = item.get("eventStartMs"), item.get("eventEndMs")
        if a is not None and b is not None:
            # The owner's own boundaries inside the window are separate, stronger evidence.
            out.append(record(
                start_ms=a, end_ms=b, selection="owner_marked_span",
                record_id=item["recordId"] + "#event",
                detail={"withinRecordId": item["recordId"]}, **common))
    return out


def from_marine(manifest: dict, doc: dict, digest: str) -> list[dict]:
    """Marine owner labels v1 and scene-first v2, validated by their own module."""
    from .marine_encounter_review import RATINGS, validate_review
    from .scene_review_narrative import NARRATIVE_FIELDS
    keys = ("animalPresence", "animalType", "behavior", *RATINGS, *sorted(NARRATIVE_FIELDS))
    return _window_reviews(manifest, validate_review(manifest, doc),
                           schema=doc["version"], digest=digest, detail_keys=keys)


def from_calibration(manifest: dict, doc: dict, digest: str) -> list[dict]:
    from .calibration_v2 import validate_review
    return _window_reviews(manifest, validate_review(manifest, doc),
                           schema=doc["version"], digest=digest,
                           detail_keys=("activity", "tags", "quality"))


def from_ski_run(plan: dict, doc: dict, digest: str) -> list[dict]:
    """An owner-bounded run is a marked span; an unbounded one judges only the context."""
    from .ski_run_context import validate_owner_run
    doc = validate_owner_run(doc, plan)
    bounded = doc["runStartMs"] is not None
    return [record(
        source_sha=doc["sourceAssetSha256"],
        start_ms=doc["runStartMs"] if bounded else doc["contextSourceStartMs"],
        end_ms=doc["runEndMs"] if bounded else doc["contextSourceEndMs"],
        decision=doc["preference"],
        selection="owner_marked_span" if bounded else "pipeline_selected_window",
        schema=doc["version"], export_digest=digest,
        record_id=doc["anchorRecordId"],
        detail={"runKind": doc["runKind"], "featureMarkers": doc["featureMarkers"],
                "reviewerVerifiedContinuousRun": doc["reviewerVerifiedContinuousRun"],
                "sourceAbsoluteTimesApproximate": True},
    )]


ADAPTERS = {"marine": from_marine, "calibration": from_calibration, "ski-run": from_ski_run}


def load(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if any(r.get("version") != VERSION for r in rows):
        raise ValueError("Ledger contains records from an unknown version")
    return rows


def append(path: Path, records: list[dict]) -> dict:
    """Add unseen records. Existing lines are never rewritten or reordered."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Refusing a symlinked ledger")
    seen = {r["evidenceId"] for r in load(path)}
    fresh = []
    for r in records:
        if r["evidenceId"] not in seen:
            seen.add(r["evidenceId"])
            fresh.append(r)
    if fresh:
        with open(path, "a", encoding="utf-8") as handle:
            for r in fresh:
                handle.write(json.dumps(r, sort_keys=True, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    return {"added": len(fresh), "alreadyPresent": len(records) - len(fresh)}


def with_outings(records: list[dict], outing_by_source: dict[str, str]) -> list[dict]:
    """Attach explicit outing IDs at read time; unknown sources stay unassigned."""
    return [{**r, "outing": outing_by_source.get(r["sourceSha256"], r["outing"])}
            for r in records]


def overlap_fraction(a: dict, b: dict) -> float:
    """Shared time over the shorter interval, so a moment inside a scene scores 1."""
    shared = min(a["endMs"], b["endMs"]) - max(a["startMs"], b["startMs"])
    if shared <= 0:
        return 0.0
    return shared / min(a["endMs"] - a["startMs"], b["endMs"] - b["startMs"])


def overlapping(records: list[dict], source_sha: str, start_ms: int, end_ms: int,
                *, minimum: float = 0.5) -> list[dict]:
    """Prior judgments touching an interval, for lookup. Nothing is transferred."""
    probe = {"startMs": start_ms, "endMs": end_ms}
    hits = [{**r, "overlapFraction": round(overlap_fraction(r, probe), 4)}
            for r in records if r["sourceSha256"] == source_sha]
    return sorted((h for h in hits if h["overlapFraction"] >= minimum),
                  key=lambda h: (-h["overlapFraction"], h["startMs"], h["evidenceId"]))


def conflicts(records: list[dict], *, minimum: float = 0.5) -> list[dict]:
    """Same source and goal, overlapping time, different decisions. Reported, not resolved.

    A Skip of a wide window does not contradict a Keep of a moment inside it,
    so a pair only counts when both come from the same kind of interval.
    """
    by_key: dict[tuple, list[dict]] = {}
    for r in records:
        by_key.setdefault((r["sourceSha256"], r["goal"], r["selection"]), []).append(r)
    found = []
    for group in by_key.values():
        group.sort(key=lambda r: (r["startMs"], r["evidenceId"]))
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if b["startMs"] >= a["endMs"]:
                    break
                fraction = overlap_fraction(a, b)
                if a["decision"] != b["decision"] and fraction >= minimum:
                    found.append({
                        "sourceSha256": a["sourceSha256"], "goal": a["goal"],
                        "overlapFraction": round(fraction, 4),
                        "evidenceIds": [a["evidenceId"], b["evidenceId"]],
                        "decisions": [a["decision"], b["decision"]],
                        "resolution": "needs_owner_decision",
                    })
    return found


def summary(records: list[dict]) -> dict:
    by_outing: dict[str, int] = {}
    for r in records:
        by_outing[r["outing"]] = by_outing.get(r["outing"], 0) + 1
    return {
        "version": VERSION, "records": len(records),
        "sources": len({r["sourceSha256"] for r in records}),
        "byDecision": {d: sum(r["decision"] == d for r in records) for d in DECISIONS},
        "bySelection": {s: sum(r["selection"] == s for r in records) for s in SELECTIONS},
        "byOuting": dict(sorted(by_outing.items())),
        "conflicts": len(conflicts(records)),
        "usableForRecallMeasurement": sum(
            r["selection"] == "owner_marked_span" for r in records),
    }

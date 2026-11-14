"""Klip V2: evidence-bound moments, reversible edits and diversified reel recipes.

Pure planning. No original-video read, inference, publishing or mutation.
Caller MUST supply checksum-verified candidate rows from campaign_reels._packet_candidates.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import math
import re

VERSION = "klip-moment-edit-collections-v2"
ANNOTATION_VERSION = "klip-owner-moment-annotations-v2"
ID = re.compile(r"^[a-f0-9]{24}$")
SHA = re.compile(r"^[a-f0-9]{64}$")

TAGS = {
    "ski": frozenset({"ski_park", "ski_jump", "ski_box", "ski_rail",
                      "ski_trees", "ski_speed", "ski_creative", "ski_moguls"}),
    "snorkeling": frozenset({"marine_wildlife", "marine_ray", "marine_fish",
                            "marine_reef", "marine_seagrass", "marine_scenic",
                            "marine_underwater_passage"}),
    "bike": frozenset({"riding_general"}),
}
DEFECTS = frozenset({"waterline", "shake", "blur", "occlusion",
                     "water_drop", "low_visibility", "flat_traverse"})
COLLECTIONS = {
    "ski_park": {"sport": "ski", "tags": ("ski_park", "ski_jump", "ski_box", "ski_rail"),
                 "variant": "highlight"},
    "ski_trees": {"sport": "ski", "tags": ("ski_trees",), "variant": "immersive"},
    "ski_speed": {"sport": "ski", "tags": ("ski_speed",), "variant": "immersive"},
    "ski_creative": {"sport": "ski", "tags": ("ski_creative", "ski_moguls"), "variant": "highlight"},
    "ski_best": {"sport": "ski", "tags": (), "variant": "highlight"},
    "snorkel_wildlife": {"sport": "snorkeling",
                         "tags": ("marine_wildlife", "marine_ray", "marine_fish"),
                         "variant": "highlight"},
    "snorkel_habitats": {"sport": "snorkeling",
                         "tags": ("marine_reef", "marine_seagrass", "marine_underwater_passage"),
                         "variant": "immersive"},
    "snorkel_scenic": {"sport": "snorkeling",
                       "tags": ("marine_scenic", "marine_underwater_passage"),
                       "variant": "immersive"},
    "snorkel_best": {"sport": "snorkeling", "tags": (), "variant": "highlight"},
    "riding_general": {"sport": "bike", "tags": (), "variant": "highlight"},
}


def _integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be integer milliseconds")
    return value


def validate_annotations(document: dict | None, rows: list[dict],
                         manifest_sha: str) -> dict[str, dict]:
    """Match owner-authored labels and source-relative times to frozen packet."""
    if document is None:
        return {}
    if not isinstance(document, dict) or document.get("version") != ANNOTATION_VERSION:
        raise ValueError("Owner annotation schema/version mismatch")
    if not SHA.fullmatch(str(manifest_sha)) or document.get("manifestSha256") != manifest_sha:
        raise ValueError("Annotations do not match the frozen packet manifest SHA")
    items = document.get("moments")
    if not isinstance(items, list) or len(items) > 500:
        raise ValueError("Invalid annotation count")
    known = {r["recordId"]: r for r in rows}
    result = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {
                "recordId", "sourceAssetSha256", "tags", "eventStartMs",
                "eventEndMs", "qualitySpans", "regionLabel"}:
            raise ValueError("Annotation has missing or unexpected fields")
        rid = item["recordId"]
        if (not isinstance(rid, str) or not ID.fullmatch(rid)
                or rid in result or rid not in known):
            raise ValueError("Unknown/duplicate annotation ID")
        row = known[rid]
        if item["sourceAssetSha256"] != row["sourceAssetSha256"]:
            raise ValueError("Annotation does not match the original source identity")
        labels = item["tags"]
        if (not isinstance(labels, list) or len(labels) > 12
                or len(set(labels)) != len(labels)
                or not all(isinstance(t, str) and t in TAGS.get(row["sport"], ()) for t in labels)):
            raise ValueError("Invalid or cross-sport moment tags")
        start, end = item["eventStartMs"], item["eventEndMs"]
        if (start is None) != (end is None):
            raise ValueError("Event phase boundaries must be supplied as a pair")
        if start is not None:
            a, b = _integer(start, "eventStartMs"), _integer(end, "eventEndMs")
            if not row["sourceStartMs"] <= a < b <= row["sourceEndMs"]:
                raise ValueError("Owner event interval exceeds the verified source window")
        defects = item["qualitySpans"]
        if not isinstance(defects, list) or len(defects) > 100:
            raise ValueError("Invalid defect-span roster")
        cleaned = []
        for defect in defects:
            if (not isinstance(defect, dict) or set(defect) != {"kind", "startMs", "endMs"}
                    or defect["kind"] not in DEFECTS):
                raise ValueError("Invalid defect span")
            a = _integer(defect["startMs"], "defect startMs")
            b = _integer(defect["endMs"], "defect endMs")
            if not row["sourceStartMs"] <= a < b <= row["sourceEndMs"]:
                raise ValueError("Defect span exceeds the verified source window")
            cleaned.append((a, b, defect["kind"]))
        region = item["regionLabel"]
        if region is not None and (not isinstance(region, str)
                                   or not 1 <= len(region) <= 100
                                   or any(ord(ch) < 32 for ch in region)):
            raise ValueError("Region must be a short owner-supplied label or null")
        result[rid] = {
            "tags": sorted(labels),
            "eventStartMs": start,
            "eventEndMs": end,
            "qualitySpans": sorted(cleaned),
            "regionLabel": region,
            "basis": "owner_supplied_unindependently_adjudicated",
        }
    return result


def _clip_boundaries(row: dict, annotation: dict | None, kind: str) -> dict:
    """Preserve full event core, optionally trim owner-marked non-core defects."""
    original_a, original_b = row["sourceStartMs"], row["sourceEndMs"]
    a, b = original_a, original_b
    reasons = []
    needs_review = []
    if annotation and annotation["eventStartMs"] is not None and kind != "immersive":
        core_a, core_b = annotation["eventStartMs"], annotation["eventEndMs"]
        if row["sport"] == "snorkeling":
            before, after = (3000, 3500) if kind == "highlight" else (1200, 1200)
        else:
            before, after = (2500, 1800) if kind == "highlight" else (1000, 900)
        a, b = max(original_a, core_a - before), min(original_b, core_b + after)
        reasons.append("owner_event_boundaries_plus_context")
        for _ in range(2):
            for start, end, defect in annotation["qualitySpans"]:
                if start <= a < end <= core_a:
                    a = end
                    reasons.append(f"removed_leading_{defect}")
                if core_b <= start < b <= end:
                    b = start
                    reasons.append(f"removed_trailing_{defect}")
        if not a <= core_a < core_b <= b:
            raise ValueError("Edit would remove part of the observed event")
        if any(start < b and end > a for start, end, _ in annotation["qualitySpans"]):
            needs_review.append("remaining_quality_defect_inside_cut")
    elif kind != "immersive":
        needs_review.append("missing_owner_event_boundaries_no_automatic_retrim")
    if annotation is None or annotation["eventStartMs"] is None:
        needs_review.append("event_completeness_unverified")
    if a >= b or a < original_a or b > original_b:
        raise ValueError("Unsafe output time interval")
    return {
        "sourceStartMs": a, "sourceEndMs": b,
        "relativeStartMs": a - original_a, "relativeEndMs": b - original_a,
        "durationMs": b - a,
        "editorialVariant": kind,
        "appliedOperations": sorted(set(reasons)),
        "reviewRequired": sorted(set(needs_review)),
        "sourcePtsAligned": False,
        "reversible": True,
        "qualityCorrectionApplied": False,
    }


def make_moments(rows: list[dict], annotations: dict[str, dict]) -> list[dict]:
    moments = []
    seen = set()
    for row in rows:
        rid = row["recordId"]
        source, sport = row["sourceAssetSha256"], row["sport"]
        a, b = row["sourceStartMs"], row["sourceEndMs"]
        if (rid in seen or not ID.fullmatch(str(rid))
                or not SHA.fullmatch(str(source))
                or sport not in (*TAGS, "unknown")
                or type(a) is not int or type(b) is not int
                or not 0 <= a < b <= 86_400_000
                or row.get("verifiedProxy") is not True
                or not SHA.fullmatch(str(row.get("proxySha256")))):
            raise ValueError("Unqualified source-backed packet row")
        seen.add(rid)
        annotation = annotations.get(rid)
        moments.append({
            "momentId": rid,
            "sourceAssetSha256": source, "sourceStartMs": a, "sourceEndMs": b,
            "sportHint": sport,
            "categoryTags": annotation["tags"] if annotation else [],
            "categoryEvidence": annotation["basis"] if annotation else "not_observed",
            "regionLabel": annotation["regionLabel"] if annotation else None,
            "eventPhaseEvidence": ({
                "eventStartMs": annotation["eventStartMs"],
                "eventEndMs": annotation["eventEndMs"],
                "basis": annotation["basis"],
            } if annotation else None),
            "qualitySpanCount": len(annotation["qualitySpans"]) if annotation else 0,
            "sourceMediaRead": False,
            "sourceProxySha256": row["proxySha256"],
            "selectionRole": row["selectionRole"],
            "humanPreference": row.get("humanPreference"),
            "humanRatings": row.get("humanRatings"),
            "humanEditorialRouting": row.get("humanCurationV2"),
            "editVariants": {
                name: _clip_boundaries(row, annotation, name)
                for name in ("immersive", "highlight", "montage")
            },
            "publicationApproved": False,
            "eventTypeOwnerLabeled": bool(annotation and annotation["tags"]),
        })
    if set(annotations) - seen:
        raise ValueError("Annotations refer to rows without verified preview")
    return moments


def _rating(moment: dict) -> float:
    ratings = moment.get("humanRatings") or {}
    valid = [float(x) for x in (
        ratings.get("eventReadability"), ratings.get("aestheticQuality"),
        ratings.get("technicalQuality"))
        if type(x) in (float, int) and math.isfinite(x) and 1 <= x <= 5]
    return sum(valid) / (5 * len(valid)) if valid else 0.0


def _eligible(moment: dict, key: str, mode: str) -> bool:
    config = COLLECTIONS[key]
    if moment["sportHint"] != config["sport"]:
        return False
    if mode == "reviewed" and moment["humanPreference"] != "keep":
        return False
    if (mode == "exploratory" and moment["humanPreference"] == "skip"):
        return False
    if config.get("regionLabel") is not None and moment["regionLabel"] != config["regionLabel"]:
        return False
    required = config["tags"]
    return not required or bool(set(required) & set(moment["categoryTags"]))


def _intersects_existing(moment: dict, chosen: list[dict]) -> bool:
    for item in chosen:
        if item["sourceAssetSha256"] != moment["sourceAssetSha256"]:
            continue
        a = max(item["sourceStartMs"], moment["sourceStartMs"])
        b = min(item["sourceEndMs"], moment["sourceEndMs"])
        shorter = min(item["sourceEndMs"] - item["sourceStartMs"],
                      moment["sourceEndMs"] - moment["sourceStartMs"])
        if b > a and (b - a) / shorter > .5:
            return True
    return False


def compose(moments: list[dict], *, mode: str = "exploratory",
            seconds: int = 150, max_clips: int = 8,
            per_source: int = 2) -> dict[str, dict]:
    if (mode not in {"exploratory", "reviewed"} or type(seconds) is not int
            or not 15 <= seconds <= 600 or type(max_clips) is not int
            or not 1 <= max_clips <= 20 or type(per_source) is not int
            or not 1 <= per_source <= 5):
        raise ValueError("Invalid reel budget")
    reels = {}
    configs = dict(COLLECTIONS)
    # Region collections use only explicit owner labels; short hashed IDs do
    # not leak the region name into local filenames or Git history.
    for label in sorted({m["regionLabel"] for m in moments
                         if m["sportHint"] == "snorkeling" and m["regionLabel"]}):
        ident = "snorkel_region_" + sha256(label.casefold().encode()).hexdigest()[:12]
        configs[ident] = {"sport": "snorkeling", "tags": (),
                          "variant": "immersive", "regionLabel": label}
    for key, config in configs.items():
        eligible = [x for x in moments if _eligible(x, key, mode)]
        chosen, used_ms = [], 0
        by_source, by_tag = Counter(), Counter()
        remaining = eligible[:]
        while remaining and len(chosen) < max_clips:
            best = None
            best_score = -1e9
            for item in remaining:
                sha = item["sourceAssetSha256"]
                cut = item["editVariants"][config["variant"]]
                if (used_ms + cut["durationMs"] > seconds * 1000
                        or by_source[sha] >= per_source
                        or _intersects_existing(item, chosen)):
                    continue
                labels = item["categoryTags"]
                # Conservative human-preference tie breaker; never a calibrated score.
                score = (_rating(item)
                         - .24 * by_source[sha]
                         - .12 * sum(by_tag[t] for t in labels)
                         + .06 * sum(by_tag[t] == 0 for t in labels))
                if mode == "exploratory":
                    score += .03 if item["selectionRole"] == "uniform_timeline_control" else 0
                tie = (item["sourceAssetSha256"], item["sourceStartMs"], item["momentId"])
                if score > best_score or (score == best_score and
                                         (best is None or tie < best[1])):
                    best_score, best = score, (item, tie)
            if best is None:
                break
            item = best[0]
            chosen.append(item)
            remaining.remove(item)
            used_ms += item["editVariants"][config["variant"]]["durationMs"]
            by_source[item["sourceAssetSha256"]] += 1
            by_tag.update(item["categoryTags"])
        items = [{
            "momentId": m["momentId"],
            "sourceAssetSha256": m["sourceAssetSha256"],
            "categoryTags": m["categoryTags"],
            "regionLabel": m["regionLabel"],
            "sourceProxySha256": m["sourceProxySha256"],
            "sourceStartMs": m["sourceStartMs"], "sourceEndMs": m["sourceEndMs"],
            "variant": m["editVariants"][config["variant"]],
            "humanPreference": m["humanPreference"],
            "unjudgedRanking": mode == "exploratory",
        } for m in chosen]
        reels[key] = {
            "collectionId": key, "sport": config["sport"],
            "regionLabel": config.get("regionLabel"),
            "variantPolicy": config["variant"],
            "selected": items,
            "selectedCount": len(items),
            "durationMs": used_ms,
            "maxDurationMs": seconds * 1000,
            "distinctSources": len(by_source),
            "selectionMethod": "deterministic_diverse_human_preference_heuristic",
            "isHumanApprovedReel": False, "publicationApproved": False,
            "eventAccuracyMeasured": False,
        }
    return reels


def build_plan(rows: list[dict], document: dict | None,
               manifest_sha: str, *, mode: str = "exploratory",
               seconds: int = 150, max_clips: int = 8,
               per_source: int = 2) -> dict:
    annotations = validate_annotations(document, rows, manifest_sha)
    moments = make_moments(rows, annotations)
    reels = compose(moments, mode=mode, seconds=seconds,
                    max_clips=max_clips, per_source=per_source)
    return {
        "version": VERSION, "annotationVersion": ANNOTATION_VERSION,
        "manifestSha256": manifest_sha, "mode": mode,
        "momentCount": len(moments),
        "ownerAnnotatedMomentCount": len(annotations),
        "knownCategoryTaggedMoments": sum(bool(m["categoryTags"]) for m in moments),
        "moments": moments, "collections": reels,
        "allOriginalFootagePreserved": True, "originalMediaRead": False,
        "noUnverifiedTricksOrSpeciesAsserted": True,
        "qualityCorrectionsAutomaticallyApplied": False,
        "publicationApproved": False,
        "limitations": [
            "Proxy timings use approximate source-relative fast-seek; frame-exact master editing requires native PTS.",
            "Owner tags are unadjudicated labels, not independently verified detection.",
            "Without owner event bounds the editor preserves the full window.",
            "Intra-event blur or shake is flagged, never silently edited away.",
            "An exploratory draft does not represent proven best moments.",
        ],
    }


def annotation_template(rows: list[dict], manifest_sha: str) -> dict:
    """Private template with blank, not fabricated, owner evidence."""
    if not SHA.fullmatch(str(manifest_sha)):
        raise ValueError("Invalid packet manifest SHA")
    return {
        "version": ANNOTATION_VERSION, "manifestSha256": manifest_sha,
        "moments": [{
            "recordId": r["recordId"], "sourceAssetSha256": r["sourceAssetSha256"],
            "tags": [], "eventStartMs": None, "eventEndMs": None,
            "qualitySpans": [], "regionLabel": None,
        } for r in rows],
    }

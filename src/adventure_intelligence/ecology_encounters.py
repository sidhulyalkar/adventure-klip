"""Source-backed marine encounter *review hypotheses* from cached object boxes.

No frames decoded, no model loaded, no names inferred. A short-term box track
is not an animal identity, and a species taxon is never assigned from a box.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
import math

from .ecology_context import OBJECT_GROUPS
from .scene_atlas import frames_for_asset
from .tracking import _bbox, _iou, associate_boxes

VERSION = "ecology-encounter-queue-v1"
PROVIDERS = ("ecology-owlv2", "owlv2")


def encounter_queue(db, workspace, asset_id: str, duration_ms: int, *,
                    max_results: int = 40, max_gap_ms: int = 5000) -> dict:
    if (type(duration_ms) is not int or duration_ms <= 0 or
            type(max_results) is not int or not 1 <= max_results <= 200 or
            type(max_gap_ms) is not int or not 100 <= max_gap_ms <= 12000):
        raise ValueError("Invalid encounter queue bounds")
    window_list = []
    audited = {"cachedFrameSamples": 0, "validObjectBoxes": 0,
               "skippedInvalidBoxes": 0, "suppressedDuplicateBoxes": 0,
               "truncatedCachedFrames": 0, "cachedProviders": [],
               "partialCoverageProviders": []}
    for provider in PROVIDERS:
        # Recovery is allowed only for marine REVIEW proposals. A partial
        # cache is never silently promoted to full editorial evidence.
        frames, model_key = frames_for_asset(
            db, workspace, asset_id, provider, allow_partial=True)
        if not frames or not model_key:
            continue
        active=db.execute(
            """SELECT j.status,j.filename FROM semantic_jobs j
               JOIN semantic_active a ON a.asset_id=j.asset_id
                 AND a.config_id=j.config_id
               WHERE a.asset_id=? AND a.provider=?""",
            (asset_id,provider)).fetchone()
        if active is not None and active["status"]=="partial":
            audited["partialCoverageProviders"].append(provider)
        audited["cachedProviders"].append({
            "provider": provider,
            "modelKeySha256": sha256(model_key.encode()).hexdigest(),
        })
        indexed = []
        audited["truncatedCachedFrames"] += max(0, len(frames) - 512)
        for original in frames[:512]:
            if not isinstance(original, dict):
                continue
            requested = original.get("tMs")
            # Source PTS is not necessarily a 0-based video-relative timestamp.
            # The request is the only safe cue for relative clip boundaries.
            if type(requested) is not int or not 0 <= requested < duration_ms:
                continue
            audited["cachedFrameSamples"] += 1
            boxes = []
            objects = original.get("objects", [])
            if not isinstance(objects, list):
                continue
            for obj in objects[:100]:
                if not isinstance(obj, dict):
                    continue
                label = obj.get("label")
                group = OBJECT_GROUPS.get(label.strip().lower()) if isinstance(label, str) else None
                score = obj.get("score")
                box = _bbox(obj)
                if (group is None or box is None or
                        type(score) not in (float, int) or
                        not math.isfinite(score) or not 0 <= score <= 1):
                    audited["skippedInvalidBoxes"] += 1
                    continue
                if obj.get("coordinateSpace") not in ("sampled_image_pixels",):
                    audited["skippedInvalidBoxes"] += 1
                    continue
                if (box[0] < 0 or box[1] < 0 or box[2] > 4096 or box[3] > 4096):
                    audited["skippedInvalidBoxes"] += 1
                    continue
                boxes.append({**obj, "label": label.lower().strip(),
                              "biologicalGroup": group, "score": float(score)})
                audited["validObjectBoxes"] += 1
            # Within-frame high-overlap duplicate same-label boxes are usually
            # duplicate detection proposals, not distinct animals. This does
            # not merge overlapping boxes for different organism labels.
            dedup = []
            for obj in sorted(boxes, key=lambda b: (-b["score"], b["label"],
                                                     tuple(_bbox(b)))):
                if any(existing["label"] == obj["label"]
                       and _iou(_bbox(existing), _bbox(obj)) >= .85
                       for existing in dedup):
                    audited["suppressedDuplicateBoxes"] += 1
                    continue
                dedup.append(obj)
            if dedup:
                pts = original.get("decodedPtsMs")
                # Keep the decoded source PTS separately. Tracking works in
                # requested (video-relative) time, not source stream PTS.
                indexed.append({"tMs": requested, "decodedPtsMs": None,
                                "sourceDecodedPtsMs": (pts if type(pts) is int
                                                        else None),
                                "objects": dedup})
        if not indexed:
            continue
        # A track only bridges a small interval of nearby sampled frames.
        # New track IDs on broken continuity are expected and not counted as
        # distinct animals. Never match across providers or camera segments.
        tracked = associate_boxes(indexed, maximum_gap_ms=max_gap_ms,
                                  minimum_iou=.3, ambiguity_margin=.08)
        grouped = defaultdict(list)
        for frame in indexed:
            for obj in frame["objects"]:
                if "trackId" in obj:
                    grouped[obj["trackId"]].append((frame["tMs"], frame["sourceDecodedPtsMs"], obj))
        for track in tracked["tracks"]:
            observations = sorted(grouped[track["id"]], key=lambda x: x[0])
            if not observations:
                continue
            label = track["label"]
            group = OBJECT_GROUPS[label]
            times = sorted({t for t, _, _ in observations})
            first, last = times[0], times[-1]
            start = max(0, first - 3000)
            end = min(duration_ms, last + 5000)
            if end <= start:
                continue
            scores = [obj["score"] for _, _, obj in observations]
            # Prioritize stronger and temporally corroborated *proposals*.
            # This is not an object-detection or species probability.
            size_bonus = min(0.06, 0.015 * (len(times) - 1))
            priority = min(1., round(max(scores) * .70 + .17 + size_bonus, 4))
            window_list.append({
                "id": f"{provider}:{track['id']}",
                "version": VERSION, "provider": provider,
                "modelKeySha256": sha256(model_key.encode()).hexdigest(),
                "status": ("multi_frame_unverified_visual_track"
                           if len(times) > 1 else "single_frame_unverified_object"),
                "label": label, "biologicalGroup": group,
                "speciesTaxonId": None, "speciesVerified": False,
                "animalIdentityVerified": False, "distinctIndividualCount": None,
                "trackHypothesisOnly": True, "reviewPriority": priority,
                "startMs": start, "endMs": end,
                "firstSampleMs": first, "lastSampleMs": last,
                "sampleCount": len(times), "proposalBoxCount": len(observations),
                "peakUncalibratedDetectorScore": round(max(scores), 5),
                "evidenceRequestedTimesMs": times[:40],
                "sampledBoxEvidence": [{
                    "requestedMs": t,
                    "sourceDecodedPtsMs": pts,
                    "label": obj["label"],
                    "coordinateSpace": obj["coordinateSpace"],
                    "bboxFormat": "xyxy",
                    "bbox": list(_bbox(obj)),
                    "uncalibratedDetectorScore": round(obj["score"], 5),
                } for t, pts, obj in observations[:40]],
                "referenceSampleWidthPx": 384,
                "timingBasis": "requested_sample_timestamp",
                "notes": ["A bounded review window, not a confirmed continuous sighting",
                          "Distinct animals and species cannot be counted from these tracks"],
            })
    # Nonoverlapping review windows may have overlapping labels; do not
    # collapse tracks into one claimed unique animal. Stable IDs per run only.
    window_list.sort(key=lambda x: (-x["reviewPriority"], x["startMs"], x["id"]))
    return {
        "schemaVersion": 1, "version": VERSION, "assetId": asset_id,
        "status": ("review_proposals_available" if window_list else
                   "no_cached_broad_visual_evidence"),
        "candidateWindows": window_list[:max_results],
        "totalWindowProposals": len(window_list),
        "audit": audited,
        "limitations": [
            "No species identification, verified sighting or individual animal count.",
            "Temporal IoU association can fragment or switch tracks, especially in POV motion.",
            "Sparse or recovered partial coverage cannot rule out encounters between samples.",
            "Requested sample times are approximate; verify actual event boundaries in video.",
            "Review proposals are not added to episodes, auto-exported or used as training labels.",
        ],
    }


def build_encounter_report(workspace, asset_id: str, *, max_results: int = 40,
                           max_gap_ms: int = 5000) -> dict:
    from .catalog import connect
    if (not isinstance(asset_id, str) or len(asset_id) != 64 or
            any(c not in "0123456789abcdef" for c in asset_id)):
        raise ValueError("Expected SHA-256 asset identifier")
    with connect(workspace) as db:
        row = db.execute("SELECT duration,activity FROM assets WHERE id=?", (asset_id,)).fetchone()
        if row is None:
            raise ValueError("Asset is not indexed in this workspace")
        if row["activity"] != "snorkeling":
            raise ValueError("Encounters currently require explicitly classified snorkeling")
        duration = row["duration"]
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            raise ValueError("Asset requires positive duration")
        return encounter_queue(db, workspace, asset_id, int(duration * 1000),
                               max_results=max_results, max_gap_ms=max_gap_ms)

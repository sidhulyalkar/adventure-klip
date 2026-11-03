"""Opt-in, source-verified, bounded high-detail crops for marine review.

This only materializes evidence for already cached unverified OWLv2 boxes:
no global scanning, no species identification, uploads, or source modification.
Every saved crop has a source SHA-256, exact decoder PTS comparison, model
provenance, image dimensions, crop coordinates, and output SHA-256.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from html import escape
import json
import math
from pathlib import Path

from .catalog import connect
from .ecology_encounters import encounter_queue
from .frame_timing import extract_frame_png
from .safety import protect_source_output
from .sources import resolve_source, verified_digest

VERSION = "ecology-crop-evidence-v1"


def _contact_sheet(manifest: dict) -> str:
    """Self-contained, offline, safely escaped evidence review contact sheet."""
    cards = []
    for crop in manifest["crops"]:
        image = escape(crop["image"], quote=True)
        label = escape(crop["label"])
        group = escape(crop["biologicalGroup"])
        stamp = f'{crop["requestedMs"] / 1000:.3f}s'
        status = "Unverified object proposal"
        cards.append(
            '<article class="tile"><img loading="lazy" alt="Candidate crop" '
            f'src="{image}"><div class="body"><strong>{label}</strong>'
            f'<span>{group} · {stamp}</span><small>{status}</small></div></article>'
        )
    if not cards:
        cards.append('<p>No PTS-verified crops were available for review.</p>')
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Klip · Marine evidence review</title><style>'
        'body{font-family:system-ui,sans-serif;margin:0;background:#101820;'
        'color:#e7eff5;padding:26px;max-width:1400px}'
        'h1{font-size:1.5rem;margin:0 0 .5rem}p{color:#b7c7d1;line-height:1.5}'
        '.grid{display:grid;grid-template-columns:repeat(auto-fill,'
        'minmax(min(100%,240px),1fr));gap:16px;margin:24px 0}'
        '.tile{background:#1a2a34;border:1px solid #30424d;'
        'border-radius:12px;overflow:hidden}'
        'img{display:block;width:100%;height:220px;object-fit:contain;'
        'background:#091117}.body{padding:12px;display:grid;gap:5px}'
        '.body span,.body small{color:#b7c7d1}'
        'small{font-size:.78rem}</style></head><body>'
        '<h1>Marine evidence review</h1>'
        '<p>Local, unverified object crops. These are not confirmed sightings, '
        'species identities, or animal counts. Refer to manifest.json for '
        'detector provenance, checksums and source timestamp verification.</p>'
        '<section class="grid">' + ''.join(cards) + '</section></body></html>'
    )


def _source_row(workspace, asset_id: str):
    if not isinstance(asset_id, str) or len(asset_id) != 64 or any(
            x not in "0123456789abcdef" for x in asset_id):
        raise ValueError("Full lowercase SHA-256 asset ID required")
    with connect(workspace) as db:
        row = db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if row is None or row["activity"] != "snorkeling":
            raise ValueError("An existing snorkeling asset is required")
        source = resolve_source(db, row)
        duration = row["duration"]
        if not isinstance(duration, (float, int)) or not math.isfinite(duration) or duration <= 0:
            raise ValueError("Positive source video duration required")
        return source, int(duration * 1000)


def _select_samples(proposals: list[dict], *, max_events: int, crops_per_event: int) -> list[dict]:
    """Choose temporal coverage rather than taking the same high-score instant."""
    selected = []
    for event in proposals[:max_events]:
        observations = event.get("sampledBoxEvidence") or []
        # Bound the event payload and refuse to extrapolate boxes to nearby times.
        valid = [o for o in observations[:40] if (
            type(o.get("requestedMs")) is int and
            type(o.get("sourceDecodedPtsMs")) is int and
            o.get("coordinateSpace") == "sampled_image_pixels" and
            o.get("bboxFormat") == "xyxy" and
            isinstance(o.get("bbox"), list) and len(o["bbox"]) == 4)]
        if not valid:
            continue
        # Prefer first, middle and last for temporal context; in single-image
        # cases, just one crop. Pick the clearest box at each requested time.
        by_time = {}
        for item in valid:
            t = item["requestedMs"]
            existing = by_time.get(t)
            if existing is None or item["uncalibratedDetectorScore"] > existing["uncalibratedDetectorScore"]:
                by_time[t] = item
        times = sorted(by_time)
        if crops_per_event == 1:
            chosen = [times[(len(times) - 1) // 2]]
        elif len(times) <= crops_per_event:
            chosen = times
        else:
            chosen = sorted({times[round(i * (len(times) - 1) / (crops_per_event - 1))]
                             for i in range(crops_per_event)})
        for time in chosen:
            selected.append({"eventId": event["id"],
                             "eventStartMs": event["startMs"],
                             "eventEndMs": event["endMs"],
                             "modelKeySha256": event["modelKeySha256"],
                             "provider": event["provider"],
                             "group": event["biologicalGroup"],
                             "label": event["label"],
                             "evidence": by_time[time]})
    return selected


def _crop_png(raw: bytes, bbox: list, width: int, *, margin: float) -> tuple[bytes, dict]:
    from PIL import Image, UnidentifiedImageError
    if len(raw) > 35_000_000:
        raise ValueError("Decoded PNG exceeds crop evidence limit")
    try:
        image = Image.open(BytesIO(raw))
        image.load()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("Invalid frame PNG") from exc
    if image.format != "PNG" or image.width != width or image.height > 4096:
        raise ValueError("Unexpected decoded frame size")
    if any(not isinstance(v, (float, int)) or isinstance(v, bool)
           or not math.isfinite(v) for v in bbox):
        raise ValueError("Malformed candidate bounding box")
    x0, y0, x1, y1 = [float(v) for v in bbox]
    if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
        raise ValueError("Invalid detector box geometry")
    # Original OWL boxes refer to the 384px-wide sampled image.
    factor = width / 384.
    left, top, right, bottom = x0 * factor, y0 * factor, x1 * factor, y1 * factor
    if right > width + 1 or bottom > image.height + 1:
        raise ValueError("Detector box lies outside redecoded image dimensions")
    center_x, center_y = (left + right) / 2, (top + bottom) / 2
    half_w = (right - left) * (.5 + margin)
    half_h = (bottom - top) * (.5 + margin)
    bounds = (
        max(0, math.floor(center_x - half_w)),
        max(0, math.floor(center_y - half_h)),
        min(image.width, math.ceil(center_x + half_w)),
        min(image.height, math.ceil(center_y + half_h)),
    )
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise ValueError("Empty crop")
    crop = image.convert("RGB").crop(bounds)
    out = BytesIO()
    crop.save(out, format="PNG", optimize=False)
    result = out.getvalue()
    if len(result) > 18_000_000:
        raise ValueError("Crop exceeds storage cap")
    return result, {
        "frameSize": [image.width, image.height],
        "cropBoundsPx": list(bounds),
        "cropSize": [crop.width, crop.height],
        "candidateBoxSizeInSampledPixels": [round(x1-x0, 3), round(y1-y0, 3)],
        "candidateBoxAreaFraction": round(
            ((right-left)*(bottom-top)) / (image.width*image.height), 8),
        "visualQualityAssessed": False,
        "coordinateTransform": "bbox_in_384px_sampled_image_scaled_by_decoded_width",
        "orientationNote": "No EXIF orientation inference; source orientation unchanged",
    }


def create_crop_packet(workspace: Path, asset_id: str, output_dir: Path, *,
                       max_events: int = 6, crops_per_event: int = 3,
                       width: int = 1536, margin: float = .4) -> dict:
    """Decode at most 24 requested frames into a *new* private workspace directory."""
    if type(max_events) is not int or not 1 <= max_events <= 8:
        raise ValueError("max-events must be 1..8")
    if type(crops_per_event) is not int or not 1 <= crops_per_event <= 3:
        raise ValueError("crops-per-event must be 1..3")
    if width not in (768, 1536, 2048):
        raise ValueError("width must be 768, 1536 or 2048")
    if (not isinstance(margin, (float, int)) or isinstance(margin, bool)
            or not math.isfinite(margin) or not .0 <= margin <= 1.5):
        raise ValueError("margin must be between 0 and 1.5")
    workspace = Path(workspace).expanduser().resolve()
    target = Path(output_dir).expanduser().resolve()
    if (not target.is_relative_to(workspace)
            or target.relative_to(workspace).parts[0] not in ("reports", "exports")
            or target.exists()):
        raise ValueError("Output must be a new folder under workspace reports/ or exports/")
    # Validate both directory and prospective manifest against registered media
    # paths. All created files will be descendants of this new protected folder.
    with connect(workspace) as db:
        protect_source_output(db, target)
        protect_source_output(db, target / "manifest.json")
        asset = db.execute("SELECT id,duration,activity FROM assets WHERE id=?",
                           (asset_id,)).fetchone()
        if asset is None or asset["activity"] != "snorkeling":
            raise ValueError("Exact indexed snorkeling asset required")
        duration = int(asset["duration"] * 1000)
        queue = encounter_queue(db, workspace, asset_id, duration, max_results=max_events)
    samples = _select_samples(queue["candidateWindows"], max_events=max_events,
                              crops_per_event=crops_per_event)
    if not samples:
        return {
            "status": "abstained_no_source_pts_backed_boxes",
            "assetId": asset_id,
            "candidateWindows": len(queue["candidateWindows"]),
            "sourceDecoded": False, "outputsWritten": False,
            "note": "Create verified ecology-owlv2 caches before requesting crops",
        }
    # Never touch the original if there are no source-backed candidates.
    source, source_duration = _source_row(workspace, asset_id)
    if duration != source_duration:
        raise ValueError("Catalog duration changed during crop planning")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(mode=0o700, exist_ok=False)
    selected = []
    failures = []
    decode_count = 0
    cached_time, cached_png, cached_timing = None, None, None
    # Chronological ordering lets adjacent proposals share a single decoded
    # frame without keeping up to 24 large PNGs resident in memory.
    samples.sort(key=lambda p: (p["evidence"]["requestedMs"], p["eventId"]))
    for index, proposal in enumerate(samples):
        frame = proposal["evidence"]
        requested = frame["requestedMs"]
        original_pts = frame["sourceDecodedPtsMs"]
        try:
            if cached_time != requested:
                cached_png, cached_timing = extract_frame_png(source, requested, width=width)
                cached_time = requested
                decode_count += 1
            raw, timing = cached_png, cached_timing
            actual_pts = timing.get("decodedPtsMs")
            if type(actual_pts) is not int or abs(actual_pts - original_pts) > 2:
                raise ValueError("Source PTS changed since original cached object detection")
            crop_bytes, image_data = _crop_png(
                raw, frame["bbox"], width, margin=float(margin))
            image_path = f"crop-{index+1:03d}.png"
            with (target / image_path).open("xb") as outfile:
                outfile.write(crop_bytes)
            selected.append({
                "image": image_path, "cropSha256": sha256(crop_bytes).hexdigest(),
                "cropByteCount": len(crop_bytes), "sourceAssetSha256": asset_id,
                "encounterId": proposal["eventId"],
                "provider": proposal["provider"],
                "modelKeySha256": proposal["modelKeySha256"],
                "label": proposal["label"], "biologicalGroup": proposal["group"],
                "requestedMs": requested, "cachedSourceDecodedPtsMs": original_pts,
                "redecodedSourcePtsMs": actual_pts,
                "unverifiedDetectorScore": frame["uncalibratedDetectorScore"],
                "sampledBox": frame["bbox"], "sampleReferenceWidthPx": 384,
                **image_data,
                "status": "unverified_candidate_crop_for_expert_review",
                "speciesTaxonId": None, "confirmedSighting": False,
            })
        except (OSError, ValueError, RuntimeError) as exc:
            failures.append({"encounterId": proposal["eventId"],
                             "requestedMs": requested, "reason": str(exc)[:200]})
    # Second independent hash check catches changed removable media and other
    # source mutations, including changes during decoding or crop writes.
    if verified_digest(source) != asset_id:
        # Never leave visually plausible but provenance-invalid crop assets.
        # Remove ONLY the numbered images this newly created packet wrote.
        # The folder itself remains as an explicit failed-run signal.
        for item in selected:
            (target / item["image"]).unlink(missing_ok=True)
        raise RuntimeError("Original source bytes changed during crop extraction; packet invalid")
    manifest = {
        "schemaVersion": 1, "version": VERSION, "sourceAssetSha256": asset_id,
        "status": "review_packet_ready" if selected else "abstained_no_valid_crops",
        "sourceVerifiedBeforeAndAfter": True,
        "imageWidthRequested": width, "maxEvents": max_events,
        "cropsPerEvent": crops_per_event, "margin": float(margin),
        "sampledEncounterProposals": len(queue["candidateWindows"]),
        "requestedCropOperations": len(samples),
        "uniqueFrameDecodes": decode_count,
        "cropCount": len(selected), "crops": selected, "failures": failures,
        "limitations": [
            "No species confirmation, animal identity or abundance inference.",
            "Source crops correspond only to the cached detector boxes and source PTS.",
            "The decoded frame may be upscaled relative to the source resolution.",
            "Crop coordinates are approximate and require visual review.",
            "Results are for private review; inspect for location-sensitive metadata.",
        ],
    }
    with (target / "manifest.json").open("x", encoding="utf-8") as outfile:
        outfile.write(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n")
    with (target / "index.html").open("x", encoding="utf-8") as outfile:
        outfile.write(_contact_sheet(manifest))
    return {
        "status": manifest["status"], "manifest": str(target / "manifest.json"),
        "reviewHtml": str(target / "index.html"),
        "cropCount": len(selected), "failedCrops": len(failures),
        "uniqueFrameDecodes": decode_count,
        "sourceVerifiedBeforeAndAfter": True,
    }

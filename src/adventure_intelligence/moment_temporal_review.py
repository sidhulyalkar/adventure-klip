"""Generate a private, offline temporal review player from derived JPEG evidence.

Only opens checksum-verified derived images/receipts and static packaged
assets. Does not open camera originals, run models, infer event identity,
write catalog reviews or approve trims. The local HTML links to derived
JPEGs by a tightly validated relative path.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil

from .encoder_dataset import safe_training_workspace
from .locking import lock_workspace
from .moment_dense_decode import _read_plan, RUN_NAME
from .moment_dense_qa import (
    VERSION as QA_VERSION,
    QA_NAME,
    _read_verified_clips,
    _valid_cached,
)

VERSION = "klip-kmoment-offline-temporal-review-v1"
REVIEW_NAME = re.compile(r"^kmoment-review-[a-z0-9][a-z0-9-]{2,50}$")
CID = re.compile(r"^clip-[a-f0-9]{24}$")
SHA = re.compile(r"^[a-f0-9]{64}$")
FRAME_REL = re.compile(r"^frames/frame-[0-9]{4}\.jpg$")
MAX_CLIPS = 32
MAX_FRAMES = 3000
TEMPLATE = "temporal_review.html"
SCRIPT = "temporal_review.js"


def _packaged(name: str) -> bytes:
    if name not in (TEMPLATE, SCRIPT):
        raise ValueError("Invalid packaged review asset")
    p = Path(__file__).parent / "studio_assets" / name
    if p.is_symlink() or not p.is_file() or p.stat().st_size > 200_000:
        raise ValueError("Missing packaged review asset")
    return p.read_bytes()


def _verified_timeline(clip: dict, qa_folder: Path, qa_name: str,
                       decode_name: str) -> dict:
    cid = clip["clipId"]
    if not CID.fullmatch(cid):
        raise ValueError("Invalid generated clip ID")
    cached = _valid_cached(qa_folder, clip, "siglip2")
    if cached is None:
        raise ValueError("Dense SigLIP2 analysis missing or altered for " + cid)
    if (cached.get("version") != QA_VERSION
            or cached.get("sourceSha256") != clip["sourceSha256"]
            or cached.get("expandedStartMs") != clip["expandedStartMs"]
            or cached.get("expandedEndMs") != clip["expandedEndMs"]
            or cached.get("coreStartMs") != clip["coreStartMs"]
            or cached.get("coreEndMs") != clip["coreEndMs"]
            or not isinstance(cached.get("frozenModelKey"), str)
            or not cached["frozenModelKey"]
            or not isinstance(cached.get("temporal"), dict)):
        raise ValueError("Dense QA identity mismatch")
    if (not isinstance(cached["storyboard"], dict)
            or cached["storyboard"].get("relativePath") != "storyboard.jpg"):
        raise ValueError("Unsafe storyboard evidence path")
    frames = []
    for entry in clip["receipt"]["frames"]:
        rel = entry["relativePath"]
        t = entry["sampledFilterSourceRelativeMs"]
        if (not FRAME_REL.fullmatch(rel) or type(t) is not int
                or not 0 <= t <= 86_400_000
                or not SHA.fullmatch(entry["sha256"])):
            raise ValueError("Invalid derived frame metadata")
        frames.append({
            "atMs": t,
            "source": f"../{decode_name}/clips/{cid}/{rel}",
        })
    if not frames or any(frames[i]["atMs"] >= frames[i+1]["atMs"]
                         for i in range(len(frames)-1)):
        raise ValueError("Missing or non-monotonic frame observations")
    signals = cached["temporal"]
    peaks = signals.get("visualChangeCandidates")
    if not isinstance(peaks, list) or len(peaks) > 5:
        raise ValueError("Malformed visual-change candidates")
    out_peaks = []
    for candidate in peaks:
        t = candidate.get("atSourceRelativeMs") if isinstance(candidate, dict) else None
        delta = candidate.get("cosineDistance") if isinstance(candidate, dict) else None
        if (type(t) is not int or t < frames[0]["atMs"]
                or t > frames[-1]["atMs"]
                or type(delta) not in (int, float)
                or not 0 <= delta <= 2):
            raise ValueError("Invalid visual-change candidate")
        out_peaks.append({
            "atMs":t,"cosineDistance":round(float(delta),5),
            "interpretation":"image_appearance_change_unverified",
        })
    return {
        "clipId":cid,
        "sourceSha256":clip["sourceSha256"],
        "denseSourceReceiptSha256":clip["receiptSha256"],
        "modelKey":cached["frozenModelKey"],
        "sportHintUnverified":clip["sportHint"],
        "selectionType":clip["selectionType"],
        "coreStartMs":clip["coreStartMs"],
        "coreEndMs":clip["coreEndMs"],
        "expandedStartMs":clip["expandedStartMs"],
        "expandedEndMs":clip["expandedEndMs"],
        "frameCount":len(frames),
        "frames":frames,
        "visualChangeCandidates":out_peaks,
        "storyboard":f"../{qa_name}/{cid}/storyboard.jpg",
        "sourceVideoReadForReview":False,
        "nativeCameraPtsMeasured":False,
        "eventAndClipBoundariesVerified":False,
    }


def build_temporal_review(workspace: Path, plan_file: Path, *,
                          decode_name: str,
                          qa_name: str,
                          review_name: str = "kmoment-review-pilot-001") -> dict:
    if (not RUN_NAME.fullmatch(decode_name)
            or not QA_NAME.fullmatch(qa_name)
            or not REVIEW_NAME.fullmatch(review_name)
            or review_name in (decode_name, qa_name)):
        raise ValueError("Invalid or conflicting private report names")
    root = safe_training_workspace(workspace)
    plan, plan_sha = _read_plan(root,plan_file)
    clips = _read_verified_clips(root,plan,plan_sha,decode_name)
    if not 1 <= len(clips) <= MAX_CLIPS:
        raise ValueError("Unsupported dense review clip count")
    if sum(len(c["receipt"]["frames"]) for c in clips) > MAX_FRAMES:
        raise ValueError("Too many derived video frames for offline review")
    folder = root / "reports" / review_name
    if folder.is_symlink():
        raise ValueError("Symlinked review output")
    qa_root = root / "reports" / qa_name
    if qa_root.is_symlink() or not qa_root.is_dir():
        raise ValueError("QA results unavailable")
    qa_summary = qa_root/"summary.json"
    if (qa_summary.is_symlink() or not qa_summary.is_file()
            or qa_summary.stat().st_size > 1_000_000):
        raise ValueError("Missing or unsafe QA summary")
    qa = json.loads(qa_summary.read_bytes())
    if (not isinstance(qa,dict) or qa.get("version") != QA_VERSION
            or qa.get("backend") != "siglip2"
            or qa.get("planSha256") != plan_sha
            or qa.get("decodeName") != decode_name
            or qa.get("errors")
            or qa.get("completedClips") != len(clips)):
        raise ValueError("QA report does not match completed decoded clips")
    timelines = []
    for clip in clips:
        item = qa_root / clip["clipId"]
        if item.is_symlink() or not item.is_dir():
            raise ValueError("Unsafe per-clip QA directory")
        timelines.append(_verified_timeline(clip,item,qa_name,decode_name))
    model_keys = {item["modelKey"] for item in timelines}
    if len(model_keys) != 1:
        raise ValueError("Mixed frozen visual model revisions")
    payload = {
        "version":VERSION,
        "planSha256":plan_sha,
        "decodeName":decode_name,
        "qaName":qa_name,
        "modelKey":next(iter(model_keys)),
        "clips":timelines,
        "observedFrames":sum(c["frameCount"] for c in timelines),
        "visualChangeCandidateCount":sum(len(c["visualChangeCandidates"])
                                          for c in timelines),
        "frameTimestampBasis":"resampled_ffmpeg_fps_pts_not_native_camera_pts",
        "eventLabelsHumanSuppliedOnly":True,
        "sourceVideoRead":False,
        "automaticClipDeletionOrPublishing":False,
    }
    js = _packaged(SCRIPT)
    template = _packaged(TEMPLATE).decode("utf-8")
    if template.count("<!--REVIEW_DATA-->") != 1:
        raise ValueError("Invalid review page placeholder")
    # No external text content or absolute paths are interpolated into HTML.
    # Escape literal < to prevent any script-termination in the JSON island.
    data = json.dumps(payload, separators=(",",":"),allow_nan=False).replace("<","\\u003c")
    html = template.replace("<!--REVIEW_DATA-->",
                            '<script id="klip-review-data" type="application/json">'
                            + data + '</script>').encode("utf-8")
    hashes = {"review.html":sha256(html).hexdigest(),
              SCRIPT:sha256(js).hexdigest()}
    with lock_workspace(root):
        if folder.exists():
            if not folder.is_dir() or any(p.is_symlink() for p in
                                          (folder/"review.html",folder/SCRIPT)):
                raise ValueError("Unsafe existing review output")
            if any(not (folder/name).is_file()
                   or sha256((folder/name).read_bytes()).hexdigest() != h
                   for name,h in hashes.items()):
                raise ValueError("Existing review page differs; use a new report name")
            status="reused_unchanged_page"
        else:
            folder.mkdir()
            try:
                (folder/"review.html").write_bytes(html)
                (folder/SCRIPT).write_bytes(js)
            except Exception:
                # Output contains only derived HTML/JS, never original media.
                shutil.rmtree(folder)
                raise
            status="created"
    return {
        "version":VERSION, "status":status,
        "reviewPage":str(folder/"review.html"),
        "clips":len(timelines),
        "observedFrames":payload["observedFrames"],
        "visualChangeCandidates":payload["visualChangeCandidateCount"],
        "modelKey":payload["modelKey"],
        "sourceVideoRead":False,
        "modelInferencePerformed":False,
        "localCatalogModified":False,
        "humanReviewDownloadOnly":True,
        "note":"Visual changes are review suggestions, not verified actions or clip boundaries.",
    }

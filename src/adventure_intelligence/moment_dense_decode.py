"""Explicit, bounded dense-image extraction from a K-MOMENT inspection plan.

Run mode reads SHA-verified originals read-only and decodes one FFmpeg batch
per bounded window, never one subprocess per frame. Derived JPEG evidence and
manifest hashes live only inside workspace/reports. The 4-fps output PTS are
RESAMPLED FILTER PTS, NOT original camera frame PTS and NOT verified actions.

A complete receipt is published only after re-verifying its source content.
Resumption uses committed source-group receipts; unfinished staging is ignored.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile

from .encoder_dataset import safe_training_workspace
from .locking import lock_workspace
from .media import require_tool
from .moment_adaptive_inspection import VERSION as PLAN_VERSION
from .sources import resolve_source, verified_digest

VERSION = "klip-kmoment-dense-frame-evidence-v1"
SHA = re.compile(r"^[a-f0-9]{64}$")
RUN_NAME = re.compile(r"^kmoment-dense-[a-z0-9][a-z0-9-]{2,50}$")
MAX_PLAN_BYTES = 4_000_000
MAX_WINDOW_MS = 45_000
FPS = 4
TICK_MS = 250
MAX_IMAGES_PER_WINDOW = 190
MAX_IMAGE_BYTES = 2_000_000
MAX_TOTAL_IMAGES = 3000


def _sha(path: Path) -> str:
    h = sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_json(path: Path, payload: dict) -> None:
    tmp = path.with_name(path.name + ".tmp-" + os.urandom(6).hex())
    try:
        with tmp.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read_plan(root: Path, file: Path) -> tuple[dict,str]:
    reports = root / "reports"
    path = file.expanduser().absolute()
    if (reports.is_symlink() or not reports.is_dir() or path.is_symlink()
            or path.parent.resolve() != reports.resolve()
            or not path.is_file() or path.stat().st_size > MAX_PLAN_BYTES):
        raise ValueError("Plan must be a small regular JSON file inside workspace/reports")
    raw = path.read_bytes()
    if len(raw) > MAX_PLAN_BYTES:
        raise ValueError("Plan too large")
    try:
        doc = json.loads(raw)
    except (ValueError,UnicodeError) as exc:
        raise ValueError("Invalid inspection plan JSON") from exc
    if (not isinstance(doc,dict) or doc.get("version") != PLAN_VERSION
            or not isinstance(doc.get("inspections"),list)
            or type(doc.get("plannedClips")) is not int
            or doc["plannedClips"] != len(doc["inspections"])
            or type(doc.get("requestedFrames")) is not int
            or not 0 <= doc["requestedFrames"] <= 8000
            or len(doc["inspections"]) > 100):
        raise ValueError("Invalid inspection plan contract")
    total = 0
    ids = set()
    for i,row in enumerate(doc["inspections"]):
        if not isinstance(row,dict):
            raise ValueError("Invalid inspection row")
        aid = row.get("sourceSha256")
        start,end=row.get("expandedStartMs"),row.get("expandedEndMs")
        core_a,core_b=row.get("coreStartMs"),row.get("coreEndMs")
        times=row.get("requestedTimesMs")
        if (row.get("status") != "inspection_requested"
                or not isinstance(aid,str) or not SHA.fullmatch(aid)
                or type(start) is not int or type(end) is not int
                or type(core_a) is not int or type(core_b) is not int
                or not 0 <= start <= core_a < core_b <= end <= 86_400_000
                or end-start>MAX_WINDOW_MS
                or not isinstance(times,list) or not 1 <= len(times) <= MAX_IMAGES_PER_WINDOW
                or type(row.get("plannedFrameCount")) is not int
                or row["plannedFrameCount"] != len(times)
                or not all(type(t) is int and start <= t < end for t in times)
                or times != sorted(set(times))
                or row.get("selectionType") not in ("ranked","exploration")):
            raise ValueError("Invalid bounded frame request")
        identity=(aid,start,end)
        if identity in ids:
            raise ValueError("Duplicate dense inspection bounds")
        ids.add(identity)
        total+=len(times)
    if total != doc["requestedFrames"]:
        raise ValueError("Inconsistent total requested frame count")
    return doc, sha256(raw).hexdigest()


def _id(row: dict) -> str:
    value=f'{row["sourceSha256"]}:{row["expandedStartMs"]}:{row["expandedEndMs"]}'
    return "clip-"+sha256(value.encode()).hexdigest()[:24]


def _source_start_time(path: Path) -> float:
    result=subprocess.run([
        require_tool("ffprobe"),"-v","error","-select_streams","v:0",
        "-show_entries","stream=start_time",
        "-of","json",str(path)
    ],text=True,capture_output=True,timeout=90,check=True)
    doc=json.loads(result.stdout)
    streams=doc.get("streams") or []
    if len(streams)!=1:
        raise ValueError("Missing single primary video stream")
    raw=streams[0].get("start_time")
    try:
        seconds=float(raw)
    except (ValueError,TypeError):
        raise ValueError("Missing video PTS origin; cannot anchor output times") from None
    if not math.isfinite(seconds) or abs(seconds)>86_400:
        raise ValueError("Unsupported video PTS origin")
    return seconds


def _ffmpeg_window(path: Path, dest: Path, row: dict, *, timeout_seconds: int,
                   width: int) -> dict:
    """One bounded decode per source interval; select requested grid frames afterward.

    -copyts preserves input stream timeline through the FPS filter. Output
    frame_pts encodes RESAMPLED 4-fps grid ticks; the video's ffprobe start
    offset maps those ticks to nominal source-relative milliseconds.
    """
    start=row["expandedStartMs"]
    end=row["expandedEndMs"]
    origin=_source_start_time(path)
    duration_ms=end-start
    images=dest/"frames"
    images.mkdir()
    pattern=images/"pts-%010d.jpg"
    cmd=[
        require_tool("ffmpeg"),"-hide_banner","-nostdin","-v","error",
        "-ss",f"{start/1000:.3f}","-t",f"{duration_ms/1000:.3f}",
        "-copyts","-i",str(path),"-map","0:v:0",
        "-an","-sn","-dn",
        "-vf",f"fps={FPS}:round=near,scale={width}:-2:flags=bicubic",
        "-fps_mode","vfr","-frames:v",str(MAX_IMAGES_PER_WINDOW),
        "-q:v","5","-frame_pts","1","-f","image2",str(pattern)
    ]
    # No shell invocation and no network input. Never copy or rewrite source.
    run=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout_seconds)
    if run.returncode != 0:
        raise RuntimeError(f"FFmpeg bounded decode failed (exit {run.returncode})")
    decoded={}
    observed=[]
    for image in sorted(images.iterdir()):
        if image.is_symlink() or not image.is_file():
            raise ValueError("Unexpected derived image file")
        match=re.fullmatch(r"pts-(-?[0-9]{1,16})\.jpg",image.name)
        if not match:
            raise ValueError("Unexpected FFmpeg output filename")
        tick=int(match.group(1))
        # The 4-fps FILTER PTS is not native frame timestamp; keep explicit.
        sampled_ms=round((tick/FPS-origin)*1000)
        if not start-250 <= sampled_ms <= end+250:
            continue
        size=image.stat().st_size
        if not 0 < size <= MAX_IMAGE_BYTES:
            raise ValueError("Invalid derived JPEG size")
        if sampled_ms in decoded:
            raise ValueError("Duplicate FPS-grid output timestamps")
        decoded[sampled_ms]=image
    requested=row["requestedTimesMs"]
    used=set()
    missing=[]
    for wanted in requested:
        nearest=min((t for t in decoded if t not in used),
                    key=lambda t:(abs(t-wanted),t),default=None)
        if nearest is None or abs(nearest-wanted)>135:
            missing.append(wanted)
            continue
        used.add(nearest)
        source_image=decoded[nearest]
        target=images/f"frame-{len(observed):04d}.jpg"
        source_image.rename(target)
        observed.append({
            "requestedSourceRelativeMs":wanted,
            "sampledFilterSourceRelativeMs":nearest,
            "gridOffsetFromRequestMs":nearest-wanted,
            "relativePath":"frames/"+target.name,
            "sha256":_sha(target),
            "bytes":target.stat().st_size,
            "nativeDecodedFramePtsAvailable":False,
        })
    # Remove unrequested intermediate 4-fps context frames; this is
    # derived cache only, never an original source.
    for image in images.iterdir():
        if image.name.startswith("pts-"):
            image.unlink()
    return {
        "requestedFrames":len(requested),
        "observedFrames":len(observed),
        "unobservedRequestedTimesMs":missing,
        "frames":observed,
        "sampledTimeBasis":"resampled_ffmpeg_fps_filter_pts_minus_video_stream_start_time",
        "nativeOriginalFramePtsMeasured":False,
        "variableFrameRateAndFastEventsNotCertified":True,
        "videoStreamPtsOriginSeconds":origin,
        "sourceVideoModified":False,
    }


def _validate_receipt(folder: Path, *, plan_sha: str, row: dict) -> dict | None:
    receipt=folder/"receipt.json"
    if (folder.is_symlink() or not folder.is_dir() or receipt.is_symlink()
            or not receipt.is_file() or receipt.stat().st_size>1_000_000):
        return None
    try:
        doc=json.loads(receipt.read_bytes())
        if (doc["version"]!=VERSION or doc["planSha256"]!=plan_sha
                or doc["sourceSha256"]!=row["sourceSha256"]
                or doc["expandedStartMs"]!=row["expandedStartMs"]
                or doc["expandedEndMs"]!=row["expandedEndMs"]
                or doc["requestedFrames"]!=len(row["requestedTimesMs"])
                or doc["observedFrames"]!=len(doc["frames"])
                or not doc["sourceVerifiedBeforeAndAfter"]):
            return None
        for image in doc["frames"]:
            rel=image["relativePath"]
            if not re.fullmatch(r"frames/frame-[0-9]{4}\.jpg",rel):
                return None
            path=folder/rel
            if (path.is_symlink() or not path.is_file()
                    or path.stat().st_size != image["bytes"]
                    or _sha(path)!=image["sha256"]):
                return None
        return doc
    except (OSError,ValueError,TypeError,KeyError):
        return None


def decode_inspection(
    workspace: Path, plan_file: Path, *, run_name: str,
    max_clips: int = 8, max_frames: int = 800,
    width: int = 384, timeout_seconds: int = 360,
    run: bool = False, ack_original_reads: bool = False,
) -> dict:
    """Read-only preflight by default; --run requires explicit source-read acknowledgement."""
    if (not RUN_NAME.fullmatch(run_name)
            or type(max_clips) is not int or not 1<=max_clips<=32
            or type(max_frames) is not int or not 1<=max_frames<=MAX_TOTAL_IMAGES
            or type(width) is not int or not 160<=width<=768
            or type(timeout_seconds) is not int or not 30<=timeout_seconds<=1800):
        raise ValueError("Invalid bounded extraction parameters")
    if run and not ack_original_reads:
        raise ValueError("Run requires explicit --ack-original-reads")
    root=safe_training_workspace(workspace)
    plan, plan_sha = _read_plan(root,plan_file)
    selected=plan["inspections"][:max_clips]
    frames=sum(len(x["requestedTimesMs"]) for x in selected)
    if frames>max_frames:
        raise ValueError("Selected clips exceed frame cap; reduce --max-clips or raise --max-frames")
    grouped=defaultdict(list)
    for x in selected:
        grouped[x["sourceSha256"]].append(x)
    output=root/"reports"/run_name
    if output.is_symlink():
        raise ValueError("Symlinked derived report directory")
    existing_run=output/"run.json"
    if existing_run.exists():
        if existing_run.is_symlink():
            raise ValueError("Symlinked run receipt")
        previous=json.loads(existing_run.read_text())
        if (previous.get("planSha256")!=plan_sha
                or previous.get("maxClips")!=max_clips
                or previous.get("maxFrames")!=max_frames
                or previous.get("width")!=width):
            raise ValueError("Existing run settings differ; choose a new run name")
    summary={
        "version": VERSION, "planSha256":plan_sha,
        "runName":run_name,"maxClips":max_clips,"maxFrames":max_frames,
        "width":width,
        "selectedClips":len(selected),"distinctSourceCount":len(grouped),
        "requestedFrames":frames,
        "runRequested":run,"acknowledgedOriginalReads":ack_original_reads if run else False,
        "originalMediaNeverModified":True,
        "sourceVideoRead":False,
        "preflightOnly":not run,
        "resumptionScope":"completed_clip_receipts_with_source_verification",
        "sourceVerification":"full_sha256_before_and_after_per_source",
        "timeBasis":"4fps_resampled_grid_pts; not_native_camera_frame_pts",
        "clips":[],
        "failures":[],
    }
    if not run:
        return summary
    # The user explicitly opts into complete source hashes and FFmpeg decode.
    require_tool("ffmpeg")
    require_tool("ffprobe")
    if shutil.disk_usage(root).free < 512*1024*1024:
        raise OSError("Less than 512 MiB free in derived workspace")
    with lock_workspace(root):
        # Ensure the output exists only after the source-safe preflight.
        output.mkdir(exist_ok=True)
        clips_root=output/"clips"
        if clips_root.is_symlink():
            raise ValueError("Symlinked clip output directory")
        clips_root.mkdir(exist_ok=True)
        database=root/"catalog.sqlite3"
        if database.is_symlink():
            raise ValueError("Symlinked source catalog")
        with sqlite3.connect(database.as_uri()+"?mode=ro",uri=True) as db:
            db.row_factory=sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            for aid, source_rows in grouped.items():
                source=db.execute("SELECT * FROM assets WHERE id=?",(aid,)).fetchone()
                if source is None or source["sha256"]!=aid:
                    summary["failures"].append({"sourceSha256":aid,"reason":"catalog_identity_missing"})
                    continue
                duration=source["duration"]
                if (type(duration) not in (int,float) or not math.isfinite(duration)
                        or any(row["expandedEndMs"] > round(duration*1000)
                               for row in source_rows)):
                    summary["failures"].append({"sourceSha256":aid,"reason":"source_duration_mismatch"})
                    continue
                staged=[]
                try:
                    # Full SHA-256, before any image extraction. This can take
                    # time with large external HEVC videos; source never writes.
                    media=resolve_source(db,source)
                    source_stat=media.stat()
                    for row in source_rows:
                        cid=_id(row)
                        dest=clips_root/cid
                        if dest.is_symlink():
                            raise ValueError("Symlinked clip output")
                        old=_validate_receipt(dest,plan_sha=plan_sha,row=row)
                        if old is not None:
                            summary["clips"].append({
                                "sourceSha256":aid, "clipId":cid,
                                "status":"reused_verified_derived_cache",
                                "observedFrames":old["observedFrames"],
                                "requestedFrames":old["requestedFrames"],
                                "receiptRelativePath":"clips/"+cid+"/receipt.json",
                            })
                            continue
                        if dest.exists():
                            raise ValueError("Unverified existing clip output; refuse overwrite")
                        stage=Path(tempfile.mkdtemp(prefix=".stage-",dir=clips_root))
                        staged.append((stage,dest,row,cid))
                        evidence=_ffmpeg_window(media,stage,row,
                            timeout_seconds=timeout_seconds,width=width)
                        _atomic_json(stage/"receipt.json",{
                            **evidence,"version":VERSION,"planSha256":plan_sha,
                            "sourceSha256":aid, "clipId":cid,
                            "expandedStartMs":row["expandedStartMs"],
                            "expandedEndMs":row["expandedEndMs"],
                            "coreStartMs":row["coreStartMs"],
                            "coreEndMs":row["coreEndMs"],
                            "selectionType":row["selectionType"],
                            "sourceVerifiedBeforeAndAfter":True,
                            "eventPhasesVerified":False,
                            "completeEventVerified":False,
                            "trimReady":False,
                        })
                    # Whole source checked AGAIN after all its intervals.
                    after=media.stat()
                    stat_keys=("st_dev","st_ino","st_size","st_mtime_ns","st_ctime_ns")
                    if any(getattr(source_stat,k)!=getattr(after,k) for k in stat_keys):
                        raise OSError("Original file changed during decoding")
                    if verified_digest(media)!=aid:
                        raise OSError("Source hash changed during extraction")
                    for stage,dest,row,cid in staged:
                        doc=json.loads((stage/"receipt.json").read_text())
                        stage.rename(dest)  # atomic, same parent
                        summary["clips"].append({
                            "sourceSha256":aid,"clipId":cid,
                            "status":"decoded_source_sha_verified",
                            "observedFrames":doc["observedFrames"],
                            "requestedFrames":doc["requestedFrames"],
                            "receiptRelativePath":"clips/"+cid+"/receipt.json",
                            "missingFrames":len(doc["unobservedRequestedTimesMs"]),
                        })
                except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
                    summary["failures"].append({
                        "sourceSha256":aid,
                        "reason":type(exc).__name__,
                        # Do not emit original file names or raw exception text.
                    })
                finally:
                    for stage,_,_,_ in staged:
                        if stage.exists():
                            shutil.rmtree(stage)
        summary["sourceVideoRead"]=True
        summary["preflightOnly"]=False
        summary["sourceCountCompleted"]=len({
            c["sourceSha256"] for c in summary["clips"]
        })
        summary["clipsCompleted"]=len(summary["clips"])
        summary["framesObserved"]=sum(c["observedFrames"] for c in summary["clips"])
        _atomic_json(output/"run.json",summary)
    return summary

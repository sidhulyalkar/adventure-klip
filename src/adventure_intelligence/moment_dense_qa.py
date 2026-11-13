"""Offline QA, contact sheets and optional frozen visual-change timeline.

Consumes only previously source-verified, hashed, privately derived JPEGs
from the K-MOMENT dense-decoder. No original video, catalog writes,
action classification or editing. Frozen SigLIP2 is opt-in.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
import shutil
from statistics import median
import tempfile

from .encoder_dataset import safe_training_workspace
from .locking import lock_workspace
from .moment_dense_decode import (
    VERSION as DECODE_VERSION, RUN_NAME as DECODE_NAME,
    _id as clip_id, _read_plan, _validate_receipt, _sha, _atomic_json,
)
from .scene_atlas import similarity, unit_vector

VERSION = "klip-kmoment-derived-temporal-qa-v1"
QA_NAME = re.compile(r"^kmoment-qa-[a-z0-9][a-z0-9-]{2,50}$")
MAX_RUN_BYTES = 1_000_000
MAX_CLIPS = 32
MAX_FRAMES = 3000
MAX_DIM = 8192
THUMBNAILS = 12


def _read_decoded_run(root: Path, name: str, plan_sha: str) -> tuple[Path,dict]:
    if not DECODE_NAME.fullmatch(name):
        raise ValueError("Invalid dense decode run name")
    directory = root/"reports"/name
    manifest = directory/"run.json"
    if (directory.is_symlink() or not directory.is_dir()
            or manifest.is_symlink() or not manifest.is_file()
            or manifest.stat().st_size > MAX_RUN_BYTES):
        raise ValueError("Missing or unsafe completed dense-decode run")
    try:
        run = json.loads(manifest.read_bytes())
    except (ValueError,UnicodeError) as exc:
        raise ValueError("Malformed dense decode receipt") from exc
    if (not isinstance(run,dict) or run.get("version") != DECODE_VERSION
            or run.get("planSha256") != plan_sha
            or type(run.get("maxClips")) is not int
            or not 1 <= run["maxClips"] <= 32
            or type(run.get("requestedFrames")) is not int
            or not 0 <= run["requestedFrames"] <= 3000
            or not isinstance(run.get("clips"),list)
            or len(run["clips"]) > MAX_CLIPS
            or run.get("failures")):
        raise ValueError("Dense decode run incomplete or has mismatched plan")
    return directory, run


def _read_verified_clips(root: Path, plan: dict, plan_sha: str,
                         decode_name: str) -> list[dict]:
    directory,run = _read_decoded_run(root,decode_name,plan_sha)
    selected=plan["inspections"][:run["maxClips"]]
    expected={clip_id(row):row for row in selected}
    accepted={}
    count=0
    for item in run["clips"]:
        if not isinstance(item,dict):
            raise ValueError("Invalid decode run clip entry")
        cid=item.get("clipId")
        row=expected.get(cid)
        if (row is None or cid in accepted
                or item.get("sourceSha256") != row["sourceSha256"]
                or item.get("receiptRelativePath") != "clips/"+cid+"/receipt.json"):
            raise ValueError("Unexpected, duplicated or stale decoded clip")
        folder=directory/"clips"/cid
        if (directory/"clips").is_symlink():
            raise ValueError("Symlinked decoded clip directory")
        receipt=_validate_receipt(folder,plan_sha=plan_sha,row=row)
        if receipt is None:
            raise ValueError("A derived receipt or JPEG checksum is invalid")
        frames=receipt["frames"]
        if (receipt["observedFrames"] != len(frames)
                or receipt["requestedFrames"] != len(row["requestedTimesMs"])
                or not isinstance(receipt.get("unobservedRequestedTimesMs"),list)
                or len(frames)+len(receipt["unobservedRequestedTimesMs"])
                   != receipt["requestedFrames"]):
            raise ValueError("Inconsistent decoded-frame coverage")
        observed=[]
        for frame in frames:
            t=frame.get("sampledFilterSourceRelativeMs")
            r=frame.get("requestedSourceRelativeMs")
            if (type(t) is not int or type(r) is not int
                    or abs(t-r)>135
                    or not row["expandedStartMs"]-250 <= t <= row["expandedEndMs"]+250):
                raise ValueError("Frame source-relative time inconsistent")
            observed.append(t)
        if observed != sorted(set(observed)):
            raise ValueError("Non-monotone or duplicate sampled timestamps")
        count+=len(frames)
        accepted[cid]={
            "clipId":cid,
            "sourceSha256":row["sourceSha256"],
            "selectionType":row["selectionType"],
            "sportHint":row.get("sportHint","unknown"),
            "expandedStartMs":row["expandedStartMs"],
            "expandedEndMs":row["expandedEndMs"],
            "coreStartMs":row["coreStartMs"],
            "coreEndMs":row["coreEndMs"],
            "folder":folder,"receipt":receipt,
            "receiptSha256":_sha(folder/"receipt.json"),
        }
    if (not accepted or count>MAX_FRAMES
            or len(accepted)!=len(selected)):
        raise ValueError("Expected fully completed selected decode intervals")
    return [accepted[clip_id(row)] for row in selected]


def _visual_change(times: list[int], vectors: list[list[float]],
                   *, min_gap_ms: int = 7000,
                   min_peak_distance_ms: int = 1500) -> dict:
    """Observed pairwise image changes, NOT physical action or event boundaries."""
    if (len(times)!=len(vectors) or len(times)<1
            or any(type(t) is not int or t<0 for t in times)
            or times != sorted(set(times))):
        raise ValueError("Invalid temporal vector timeline")
    clean=[unit_vector(v) for v in vectors]
    if (any(v is None for v in clean)
            or len({len(v) for v in clean})!=1):
        raise ValueError("Incompatible or degenerate visual embeddings")
    gaps=[b-a for a,b in zip(times,times[1:])]
    pairs=[]
    for i,gap in enumerate(gaps):
        if 0<gap<=min_gap_ms:
            sim=similarity(clean[i],clean[i+1])
            if sim is None:
                raise ValueError("Mismatched frozen image embeddings")
            pairs.append({
                "atSourceRelativeMs":times[i+1],
                "deltaMs":gap,
                "cosineDistance":round(max(0,1-sim),6),
            })
    dist=[p["cosineDistance"] for p in pairs]
    med=median(dist) if dist else None
    mad=median(abs(x-med) for x in dist) if dist else None
    # An absolute floor avoids calling tiny normal embedding jitter an event.
    threshold=max(.08,med+2.5*mad) if dist else None
    peaks=[]
    for item in sorted(pairs,key=lambda p:(
        -p["cosineDistance"],p["atSourceRelativeMs"])):
        if item["cosineDistance"]<threshold:
            break
        if all(abs(item["atSourceRelativeMs"]-p["atSourceRelativeMs"])
               >= min_peak_distance_ms for p in peaks):
            peaks.append(item)
            if len(peaks)>=5:
                break
    peaks.sort(key=lambda p:p["atSourceRelativeMs"])
    return {
        "frames":len(times),
        "adjacentPairs":len(gaps),
        "connectedPairsAt7000Ms":len(pairs),
        "medianAdjacentGapMs":round(median(gaps),3) if gaps else None,
        "medianAdjacentEmbeddingDistance":round(med,6) if med is not None else None,
        "relativeContrastThreshold":round(threshold,6) if threshold is not None else None,
        "visualChangeCandidates":peaks,
        "candidateMeaning":"relative_image_appearance_change_not_an_action",
        "actionAndEventPhasesVerified":False,
        "clipBoundaryReady":False,
        "sourceTimestampBasis":"ffmpeg_resampled_filter_pts_not_native_camera_pts",
    }


class FrozenSiglipBatch:
    """Reuse the existing model configuration; load once and infer small batches."""
    def __init__(self):
        from .semantic import SiglipProvider
        self.model=SiglipProvider()
        self.key=None

    def infer(self, paths: list[Path]) -> list[list[float]]:
        from PIL import Image
        from contextlib import ExitStack
        import torch
        with ExitStack() as stack:
            imgs=[stack.enter_context(Image.open(p)).convert("RGB") for p in paths]
            inputs=self.model.processor(images=imgs,return_tensors="pt").to(
                self.model.device)
            with torch.inference_mode():
                value=self.model.model.get_image_features(**inputs)
                if hasattr(value,"pooler_output"):
                    value=value.pooler_output
                value=torch.nn.functional.normalize(value,dim=-1)
                values=value.detach().float().cpu().tolist()
        if (not isinstance(values,list) or len(values)!=len(paths)
                or any(unit_vector(v) is None for v in values)):
            raise ValueError("Invalid frozen image encoder output")
        return values

    def model_key(self, dimension: int) -> str:
        config=self.model.cache_config
        key={
            "provider":"siglip2",
            "modelVersion":self.model.version,
            "resolvedRevision":config.get("resolvedRevision"),
            "imageProcessor":config.get("imageProcessor"),
            "dimensions":dimension,
        }
        return sha256(json.dumps(key,sort_keys=True,
                   separators=(",",":"),default=str).encode()).hexdigest()[:20]


def _storyboard(clip: dict, dest: Path) -> dict:
    """Twelve evenly spread views, not model-selected high-intensity frames."""
    from PIL import Image, ImageDraw
    frames=clip["receipt"]["frames"]
    count=min(THUMBNAILS,len(frames))
    indices=sorted({round(i*(len(frames)-1)/max(1,count-1))
                    for i in range(count)})
    width,height=240,150
    sheet=Image.new("RGB",(4*width,3*height),(17,23,30))
    draw=ImageDraw.Draw(sheet)
    for n,index in enumerate(indices):
        frame=frames[index]
        image_path=clip["folder"]/frame["relativePath"]
        with Image.open(image_path) as source:
            im=source.convert("RGB")
            im.thumbnail((width,height-24))
            col,row=n%4,n//4
            x=col*width+(width-im.width)//2
            y=row*height
            sheet.paste(im,(x,y))
        label=f'{frame["sampledFilterSourceRelativeMs"]/1000:.2f}s'
        draw.text((col*width+7,row*height+height-20),label,fill=(245,245,245))
    path=dest/"storyboard.jpg"
    sheet.save(path,format="JPEG",quality=85)
    return {
        "relativePath":"storyboard.jpg",
        "sha256":_sha(path),
        "framesDisplayed":len(indices),
        "selection":"uniform_temporal_coverage_not_visual_peak",
    }


def _build_one(clip: dict, dest: Path, *, backend: str,
               encoder, batch_size: int) -> dict:
    sheet=_storyboard(clip,dest)
    frames=clip["receipt"]["frames"]
    dates=[f["sampledFilterSourceRelativeMs"] for f in frames]
    features=None
    if backend=="siglip2":
        vectors=[]
        for offset in range(0,len(frames),batch_size):
            batch=frames[offset:offset+batch_size]
            paths=[clip["folder"]/f["relativePath"] for f in batch]
            vectors.extend(encoder.infer(paths))
        dimension=len(vectors[0])
        if not 8<=dimension<=MAX_DIM or len(vectors)!=len(frames):
            raise ValueError("Unexpected frozen embedding dimensions")
        key=encoder.model_key(dimension)
        features={
            "version":VERSION,"backend":"frozen_siglip2",
            "modelKey":key,
            "sourceSha256":clip["sourceSha256"],
            "denseInputReceiptSha256":clip["receiptSha256"],
            "frameCount":len(frames),
            "observations":[{
                "sampledSourceRelativeMs":t,
                "imageSha256":f["sha256"],
                "embedding":v,
            } for t,f,v in zip(dates,frames,vectors)],
        }
        _atomic_json(dest/"frozen_features.json",features)
        analysis=_visual_change(dates,vectors)
    else:
        key=None
        analysis={
            "frames":len(frames),
            "connectedPairsAt7000Ms":sum(
                0<b-a<=7000 for a,b in zip(dates,dates[1:])),
            "medianAdjacentGapMs":round(median([
                b-a for a,b in zip(dates,dates[1:])]),3)
                if len(dates)>1 else None,
            "visualChangeCandidates":None,
            "actionAndEventPhasesVerified":False,
            "clipBoundaryReady":False,
            "reason":"no_visual_encoder_run_storyboard_only",
        }
    return {
        "version":VERSION,
        "clipId":clip["clipId"],
        "sourceSha256":clip["sourceSha256"],
        "selectionType":clip["selectionType"],
        "sportHint":clip["sportHint"],
        "expandedStartMs":clip["expandedStartMs"],
        "expandedEndMs":clip["expandedEndMs"],
        "coreStartMs":clip["coreStartMs"],
        "coreEndMs":clip["coreEndMs"],
        "inputReceiptSha256":clip["receiptSha256"],
        "backend":backend,
        "frozenModelKey":key,
        "observedFrames":len(frames),
        "storyboard":sheet,
        "featuresSha256":_sha(dest/"frozen_features.json")
                         if features is not None else None,
        "temporal":analysis,
        "eventIdentityVerified":False,
        "humanReviewRequired":True,
        "sourceVideoRead":False,
    }


def _valid_cached(folder:Path, clip:dict, backend:str) -> dict | None:
    target=folder/"analysis.json"
    if (folder.is_symlink() or not folder.is_dir() or target.is_symlink()
            or not target.is_file() or target.stat().st_size>200_000):
        return None
    try:
        doc=json.loads(target.read_bytes())
        if (doc.get("version")!=VERSION or doc.get("backend")!=backend
                or doc.get("clipId")!=clip["clipId"]
                or doc.get("inputReceiptSha256")!=clip["receiptSha256"]
                or doc.get("observedFrames")!=len(clip["receipt"]["frames"])):
            return None
        image=folder/"storyboard.jpg"
        if image.is_symlink() or not image.is_file() or _sha(image)!=doc["storyboard"]["sha256"]:
            return None
        if backend=="siglip2":
            path=folder/"frozen_features.json"
            if path.is_symlink() or not path.is_file() or _sha(path)!=doc["featuresSha256"]:
                return None
        return doc
    except (ValueError,OSError,KeyError,TypeError):
        return None


def analyze_dense(
    workspace:Path,plan_file:Path,*,decode_name:str,
    analysis_name:str,backend:str="storyboards",
    batch_size:int=6,run:bool=False,
    encoder=None,
) -> dict:
    """Validate derived receipts; optional batch model, offline, no original reads."""
    if (not QA_NAME.fullmatch(analysis_name)
            or backend not in ("storyboards","siglip2")
            or type(batch_size) is not int or not 1<=batch_size<=16):
        raise ValueError("Invalid dense QA parameters")
    root=safe_training_workspace(workspace)
    plan,plan_sha=_read_plan(root,plan_file)
    clips=_read_verified_clips(root,plan,plan_sha,decode_name)
    frame_total=sum(len(c["receipt"]["frames"]) for c in clips)
    target=root/"reports"/analysis_name
    if target.is_symlink():
        raise ValueError("Symlinked analysis directory")
    settings={
        "version":VERSION,
        "planSha256":plan_sha,
        "decodeName":decode_name,
        "analysisName":analysis_name,
        "backend":backend,
        "batchSize":batch_size,
    }
    previous=target/"summary.json"
    if previous.exists():
        if previous.is_symlink() or previous.stat().st_size>1_000_000:
            raise ValueError("Unsafe existing QA summary")
        other=json.loads(previous.read_bytes())
        if any(other.get(k)!=v for k,v in settings.items()):
            raise ValueError("Analysis settings differ; choose a new name")
    report={
        **settings,
        "plannedClips":len(clips),
        "decodedFrames":frame_total,
        "sourceVideoRead":False,
        "sourceVideoModified":False,
        "modelInferencePerformed":bool(run and backend=="siglip2"),
        "preflightOnly":not run,
        "clippedEventBoundaryClaims":False,
        "results":[],
        "errors":[],
    }
    if not run:
        return report
    # The originals are not opened, even for integrity: SHA-backed derived
    # frames have already been checked on this local private read-only path.
    with lock_workspace(root):
        if encoder is None and backend=="siglip2":
            encoder=FrozenSiglipBatch()
        target.mkdir(exist_ok=True)
        for c in clips:
            dest=target/c["clipId"]
            cached=_valid_cached(dest,c,backend)
            if cached is not None:
                report["results"].append({
                    "clipId":c["clipId"],"status":"reused_verified_analysis",
                    "observedFrames":cached["observedFrames"],
                    "modelKey":cached["frozenModelKey"],
                    "connectedPairsAt7000Ms":cached["temporal"]["connectedPairsAt7000Ms"],
                })
                continue
            if dest.exists() or dest.is_symlink():
                report["errors"].append({
                    "clipId":c["clipId"],"reason":"existing_derived_artifact_invalid_refuse_overwrite"})
                continue
            stage=Path(tempfile.mkdtemp(prefix=".stage-qa-",dir=target))
            try:
                doc=_build_one(c,stage,backend=backend,
                               encoder=encoder,batch_size=batch_size)
                _atomic_json(stage/"analysis.json",doc)
                stage.rename(dest)
                report["results"].append({
                    "clipId":c["clipId"],"status":"created",
                    "observedFrames":doc["observedFrames"],
                    "modelKey":doc["frozenModelKey"],
                    "connectedPairsAt7000Ms":doc["temporal"]["connectedPairsAt7000Ms"],
                    "visualChangeCandidateCount":(
                        len(doc["temporal"]["visualChangeCandidates"])
                        if doc["temporal"]["visualChangeCandidates"] is not None
                        else None),
                })
            except (OSError,ValueError,RuntimeError,TypeError) as exc:
                report["errors"].append({
                    "clipId":c["clipId"],
                    "reason":type(exc).__name__,
                })
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        report["completedClips"]=len(report["results"])
        report["connectedPairsAt7000Ms"]=sum(
            x["connectedPairsAt7000Ms"] for x in report["results"])
        _atomic_json(target/"summary.json",report)
    return report

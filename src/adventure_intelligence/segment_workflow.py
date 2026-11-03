"""Opt-in source-verified, per-recording temporal segment investigation.

Default scope is ONE explicit asset. No edits to the source, catalog labels,
or existing first-pass or ecological reports. Uses pinned local SigLIP2,
bounded sparse sampling and at most one additional boundary refinement pass.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import math

from .activity_intelligence import _hash, _thumbnail
from .catalog import connect
from .frame_timing import extract_frame_png
from .safety import protect_source_output
from .scene_atlas import frames_for_asset
from .sources import resolve_source
from .segment_intelligence import (
    VERSION,PROMPTS,MAX_FRAME_COUNT,SegmentModel,coarse_timestamps,
    score_segment_frame,refinement_times,build_segments,overlay_events,
    html_review,
)


def _sample_frame(path:Path,requested_ms:int)->tuple[bytes,dict]:
    png,timing=extract_frame_png(path,requested_ms,width=384)
    if (not isinstance(timing,dict)
            or timing.get("timestampBasis")!="decoded_frame_pts"
            or type(timing.get("decodedPtsMs")) is not int):
        raise ValueError("Frame lacks measured source PTS")
    return png,timing


def scan_segments(workspace:Path,asset_id:str,output_dir:Path,*,
                  activity:str="auto",step_seconds:int=15,
                  max_frames:int=64,refine_frames:int=16,
                  allow_model_download:bool=False) ->dict:
    if (not isinstance(asset_id,str) or len(asset_id)!=64 or
            any(c not in "0123456789abcdef" for c in asset_id)):
        raise ValueError("Full lowercase asset SHA-256 required")
    if activity not in {"auto",*PROMPTS}:
        raise ValueError("Supported activity: auto, snorkeling, ski or bike")
    if type(step_seconds) is not int or not 3<=step_seconds<=120:
        raise ValueError("step-seconds must be 3..120")
    if (type(max_frames) is not int or not 6<=max_frames<=MAX_FRAME_COUNT
        or type(refine_frames) is not int or not 0<=refine_frames<=max_frames-3):
        raise ValueError("Invalid max-frames and refinement budget")
    workspace=Path(workspace).expanduser().resolve()
    target=Path(output_dir).expanduser().resolve()
    if (not target.is_relative_to(workspace) or
            target.relative_to(workspace).parts[0] not in ("reports","exports")):
        raise ValueError("Segment packet must be under workspace reports/ or exports/")
    with connect(workspace) as db:
        for f in ("config.json","report.json","review.html"):
            protect_source_output(db,target/f)
        row=db.execute("SELECT * FROM assets WHERE id=?",(asset_id,)).fetchone()
        if row is None or not isinstance(row["duration"],(float,int)):
            raise ValueError("Indexed source and duration required")
        duration_ms=round(row["duration"]*1000)
        if duration_ms<=0:
            raise ValueError("Positive indexed source duration required")
        known_activity=row["activity"]
        resolved_activity=known_activity if activity=="auto" else activity
        if resolved_activity not in PROMPTS:
            raise ValueError("Unknown catalog activity: specify --activity for a review-only hypothesis")
        if known_activity not in ("unknown",resolved_activity):
            raise ValueError("Selected sport conflicts with a previously confirmed activity")
        config={
            "version":VERSION,"assetId":asset_id,"durationMs":duration_ms,
            "catalogActivity":known_activity,
            "activity":resolved_activity,
            "hypothesisOnly":known_activity=="unknown",
            "stepSeconds":step_seconds,"maxFrames":max_frames,
            "refineFrames":refine_frames,"prompts":PROMPTS[resolved_activity],
            "sourceTimeline":"requested_video_relative_ms",
            "decodedSourcePts":"preserved_separately_not_zero_normalized",
        }
        config_hash=_hash(config)
        if target.exists():
            if not target.is_dir() or not (target/"config.json").is_file():
                raise ValueError("Existing segment folder is incomplete or incompatible")
            previous=json.loads((target/"config.json").read_text())
            if previous!=config or not (target/"report.json").is_file():
                raise ValueError("Existing segment folder cannot be overwritten")
            report=json.loads((target/"report.json").read_text())
            if report.get("configHash")!=config_hash:
                raise ValueError("Existing segment report provenance mismatch")
            return {"cached":True,"assetId":asset_id,
                    "review":str(target/"review.html"),
                    "report":str(target/"report.json"),
                    "segments":len(report["segments"])}
        # Hash the actual source before using any preexisting semantic evidence.
        source=resolve_source(db,row)
        object_frames={}
        if resolved_activity=="snorkeling":
            for provider in ("ecology-owlv2","owlv2"):
                frames,model_key=frames_for_asset(db,workspace,asset_id,provider)
                if model_key and frames:
                    object_frames[provider]=frames
    stat_before=source.stat()
    model=SegmentModel(allow_model_download=allow_model_download)
    errors=[]
    collected={}
    def decode_stage(times,stage):
        new=[]
        for t in times:
            if t in collected:
                continue
            try:
                raw,stamp=_sample_frame(source,t)
                new.append((t,raw,stamp))
            except (OSError,RuntimeError,ValueError) as exc:
                errors.append({"requestedMs":t,"stage":stage,"reason":str(exc)[:180]})
        if not new:
            return
        # Images are embedded in small internal batches in the shared model.
        embeddings=model.image_vectors([x[1] for x in new])
        if len(embeddings)!=len(new):
            raise ValueError("Model returned incorrect number of frame embeddings")
        for (t,png,timing),vector in zip(new,embeddings):
            result=score_segment_frame(vector,resolved_activity,
                                       model.text_vectors[resolved_activity])
            collected[t]={"requestedMs":t,"decodedPtsMs":timing["decodedPtsMs"],
                          "timestampBasis":"decoded_frame_pts",
                          "stage":stage,"png":png,**result}

    coarse=coarse_timestamps(duration_ms,step_ms=step_seconds*1000,
                             max_frames=max_frames,refine_budget=refine_frames)
    decode_stage(coarse,"coarse")
    initial=[{k:v for k,v in x.items() if k!="png"}
             for t,x in sorted(collected.items())]
    targeted=refinement_times(initial,budget=max_frames-len(coarse))
    targeted=[t for t in targeted if 0<=t<duration_ms and t not in collected]
    decode_stage(targeted[:refine_frames],"refine")
    after=source.stat()
    fields=("st_dev","st_ino","st_size","st_mtime_ns","st_ctime_ns")
    if any(getattr(stat_before,k)!=getattr(after,k) for k in fields):
        raise RuntimeError("Source changed during segment analysis; no report saved")
    valid=[{k:v for k,v in x.items() if k!="png"}
           for t,x in sorted(collected.items())]
    if not valid:
        raise RuntimeError("No PTS-backed source frames could be decoded")
    coverage=build_segments(valid,duration_ms,
        max_support_radius_ms=min(12000,max(3000,step_seconds*1000)))
    segments=overlay_events(coverage["segments"],object_frames)
    # Small preview only. Each gallery image is a sampled source frame,
    # not a confirmed sport state or organism identification.
    preview=[]
    selected_times=[]
    valid_times=sorted(collected)
    if len(valid_times)<=12:
        selected_times=valid_times
    else:
        selected_times=sorted({valid_times[round(i*(len(valid_times)-1)/11)]
                               for i in range(12)})
    target.mkdir(parents=True,mode=0o700,exist_ok=False)
    (target/"thumbnails").mkdir()
    for index,t in enumerate(selected_times):
        image=f"thumbnails/scene-{index+1:02d}.jpg"
        (target/image).write_bytes(_thumbnail(collected[t]["png"]))
        preview.append({"image":image,"requestedMs":t,
                        "label":collected[t]["label"],
                        "unverified":True})
    report={
        "schemaVersion":1,"version":VERSION,
        "configHash":config_hash,
        "modelRevision":model.revision,
        "processorHash":model.processor_hash,
        "assetId":asset_id,"activity":resolved_activity,
        "catalogActivity":known_activity,
        "activityHypothesisNotConfirmed":known_activity=="unknown",
        "durationMs":duration_ms,
        "samples":valid,
        "segments":segments,
        "coverage":{k:v for k,v in coverage.items() if k!="segments"},
        "previewFrames":preview,
        "frameDecodeErrors":errors,
        "budget":{
            "maxFrameInference":max_frames,
            "coarsePlanned":len(coarse),
            "refinementPlanned":len(targeted[:refine_frames]),
            "actualSuccessful":len(valid),
            "skipped":len(errors),
            "sourceVideoHashes":1,
        },
        "overlayProviders":sorted(object_frames),
        "sourceVerifiedBeforeRun":True,
        "sourceStatUnchangedAfterRun":True,
        "limitations":[
            "Uncalibrated image-text similarities, not sport ground truth.",
            "Sparse frames cannot prove movement, jump execution or terrain difficulty.",
            "Marine object boxes are unverified proposals, not species or counts.",
            "No sample means unknown, not that no event occurred.",
            "Timeline uses requested video-relative times; decoded source PTS is separate.",
            "Interval boundaries are provisional navigation hints, not verified event cuts.",
        ],
    }
    with (target/"config.json").open("x",encoding="utf-8") as file:
        file.write(json.dumps(config,indent=2,sort_keys=True,allow_nan=False)+"\n")
    with (target/"report.json").open("x",encoding="utf-8") as file:
        file.write(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+"\n")
    with (target/"review.html").open("x",encoding="utf-8") as file:
        file.write(html_review(report))
    return {"cached":False,"assetId":asset_id,
            "activity":resolved_activity,
            "activityHypothesisOnly":known_activity=="unknown",
            "sampledFrames":len(valid),"failedFrames":len(errors),
            "segmentCount":len(segments),
            "coveredFraction":coverage["coveredFraction"],
            "detectorOverlays":sorted(object_frames),
            "review":str(target/"review.html"),
            "report":str(target/"report.json")}

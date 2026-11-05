"""Bounded, local temporal measurements for the Social Lens raw-footage study.

A verified Studio H.264 derivative is the only video input to this analyzer.
Originals are untouched unless the user explicitly opts in to preparing a
new checksum-verified preview. These metrics are numerical observations, not
validated human Social Lens rubric values, athletic speed or event identity.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from hashlib import sha256
from html import escape
from pathlib import Path
from statistics import median
from urllib.parse import quote
import json
import os
import math
import subprocess
import sys

from .social_temporal_checkpoint import TemporalCheckpoint, _atomic_json

from .acoustics import AUDIO_VERSION
from .archive_pilot import _open_catalog
from .artifacts import read_artifact
from .editorial_quality import raw_motion_for_asset
from .media import require_tool, probe
from .scene_atlas import frames_for_asset, unit_vector, similarity
from .social_action_lens import (
    USER_VERSION, FEATURE_VERSION, FEATURES, SPORTS, _json, _new_folder,
)
from .studio_preview_judge import preview_paths, _check_cached

VERSION="social-temporal-evidence-v1"
FPS=4
WIDTH=128
HEIGHT=72
MAX_FRAMES=240
MAX_DECODE_BYTES=WIDTH*HEIGHT*MAX_FRAMES
MIN_FRAMES=5


def _finite(v):
    return type(v) in (int,float) and math.isfinite(v)


def _summ(v):
    return round(median(v),6) if v else None


def _quantile(v,ratio:float):
    if not v:
        return None
    seq=sorted(v)
    return round(seq[min(len(seq)-1,int((len(seq)-1)*ratio))],6)


def summarize_gray_frames(raw:bytes,*,width:int=WIDTH,height:int=HEIGHT,
                          fps:int=FPS)->dict:
    """Pure Python deterministic measurements from bounded gray frame bytes.

    Luminance and spatial gradient energy are *not* direct exposure accuracy,
    focus or composition quality. Consecutive grayscale difference cannot
    distinguish intentional camera movement from subject movement or jitter.
    """
    if (type(width) is not int or type(height) is not int
            or not 16<=width<=320 or not 16<=height<=240
            or type(fps) is not int or not 1<=fps<=12):
        raise ValueError("Invalid raw frame geometry")
    area=width*height
    if (len(raw)%area or not MIN_FRAMES*area<=len(raw)<=MAX_FRAMES*area):
        raise ValueError("Temporal frames absent, incomplete or too numerous")
    n=len(raw)//area
    luminance=[]
    dark=[]
    bright=[]
    spatial_gradient=[]
    differences=[]
    previous=None
    for i in range(n):
        frame=memoryview(raw)[i*area:(i+1)*area]
        luma=sum(frame)/(area*255)
        luminance.append(luma)
        dark.append(sum(x<=15 for x in frame)/area)
        bright.append(sum(x>=240 for x in frame)/area)
        # Relative horizontal/vertical edge energy at a fixed preview scale.
        # It is affected by lighting, water particles and foliage texture.
        gradient=0
        samples=0
        for y in range(1,height-1,2):
            offset=y*width
            for x in range(1,width-1,2):
                p=offset+x
                gradient+=abs(frame[p]-frame[p-1])+abs(frame[p]-frame[p-width])
                samples+=2
        spatial_gradient.append(gradient/(samples*255) if samples else 0.)
        if previous is not None:
            step=sum(abs(a-b) for a,b in zip(frame,previous))/(area*255)
            differences.append(step)
        previous=bytes(frame)
    # One second is 4 frames. We report continuity of an *image-change cue*,
    # not verified continuous downhill, turning, or animal motion.
    threshold=.08
    active=[x>=threshold for x in differences]
    longest=0
    current=0
    for flag in active:
        current=current+1 if flag else 0
        longest=max(longest,current)
    thirds=[]
    for i in range(3):
        start=(len(differences)*i)//3
        stop=(len(differences)*(i+1))//3
        third=differences[start:stop]
        thirds.append(_summ(third))
    return {
        "method":"local_ffmpeg_gray_128x72_4fps_v1",
        "frameCount":n,"nominalSampleFps":fps,
        "sampledDurationSec":round(n/fps,3),
        "luminanceMedian":_summ(luminance),
        "darkPixelFractionMedian":_summ(dark),
        "brightPixelFractionMedian":_summ(bright),
        "spatialGradientEnergyMedian":_summ(spatial_gradient),
        "frameChangeMedian":_summ(differences),
        "frameChangeP90":_quantile(differences,.90),
        "frameChangeMax":round(max(differences),6) if differences else None,
        "fractionTransitionsAbove08":round(sum(active)/len(active),6) if active else None,
        "longestHighChangeRunSec":round(longest/fps,3),
        "changeMedianEarlyMiddleLate":thirds,
        "cameraMotionType":None,
        "subjectOccupancy":None,
        "horizonRollDegrees":None,
        "sharpnessIsHumanVerified":False,
        "temporalActionContinuityVerified":False,
        "absoluteSpeed":None,
        "interpretation":"Grayscale temporal variation and texture proxies; neither action quality nor physical speed",
    }


def decode_proxy_frames(path:Path)->dict:
    """Read at most 60 seconds from one preverified, bounded H.264 preview."""
    details=probe(path)
    duration=details.get("duration")
    if not _finite(duration) or not 1<=duration<=61:
        raise ValueError("Verified preview duration outside bounded window")
    command=[require_tool("ffmpeg"),"-hide_banner","-loglevel","error",
             "-nostdin","-i",str(path),"-t","60",
             "-vf",f"fps={FPS},scale={WIDTH}:{HEIGHT},format=gray",
             "-frames:v",str(MAX_FRAMES),
             "-an","-sn","-f","rawvideo","-pix_fmt","gray","pipe:1"]
    proc=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                        timeout=90,check=False)
    if proc.returncode!=0:
        raise RuntimeError("Bounded H.264 grayscale decode failed")
    if len(proc.stdout)>MAX_DECODE_BYTES:
        raise ValueError("Decoded frame buffer exceeded hard memory limit")
    return summarize_gray_frames(proc.stdout)


def _series_window(series:list[dict],start:int,end:int,key:str)->list[float]:
    out=[]
    for row in series:
        if not isinstance(row,dict):
            continue
        t=row.get("t_ms")
        value=row.get(key)
        if (_finite(t) and start<=t<end and _finite(value)
                and 0<=value):
            out.append(float(value))
    return out


def cached_motion_and_audio(db,workspace:Path,aid:str,start:int,end:int)->dict:
    motion=raw_motion_for_asset(db,workspace,aid)
    audio_doc=read_artifact(db,workspace,aid,AUDIO_VERSION)
    audio=audio_doc["series"] if audio_doc else []
    change=_series_window(motion,start,end,"change")
    rms=_series_window(audio,start,end,"rms")
    clip=_series_window(audio,start,end,"clippingFraction")
    lows=_series_window(audio,start,end,"lowBandFraction")
    # A single audio spike is not reliably wind, a crash, or breathing.
    return {
        "method":"checksum_verified_cached_motion_and_audio",
        "motionSamples":len(change),"motionChangeMedian":_summ(change)
            if len(change)>=4 else None,
        "motionChangeP90":_quantile(change,.90) if len(change)>=4 else None,
        "audioSamples":len(rms),"audioRmsMedian":_summ(rms)
            if len(rms)>=4 else None,
        "audioClippingMedian":_summ(clip) if len(clip)>=4 else None,
        "lowBandFractionMedian":_summ(lows) if len(lows)>=4 else None,
        "audioRepresentsOriginalRecording":audio_doc is not None,
        "windOrTravelSpeedVerified":False,
    }


def semantic_temporal_evidence(db,workspace:Path,aid:str,start:int,end:int)->dict:
    frames,key=frames_for_asset(db,workspace,aid,provider="siglip2")
    if key is None:
        return {"status":"unknown","modelKey":None,"samples":0,
                "reason":"no_verified_completed_siglip2_cache"}
    samples=[]
    dims=None
    for frame in frames:
        stamp=frame.get("decodedPtsMs")
        if type(stamp) is not int or stamp<0:
            stamp=frame.get("tMs")
        if type(stamp) is not int or not start<=stamp<end:
            continue
        vector=unit_vector(frame.get("embedding"))
        if vector is None:
            continue
        if dims is None:
            dims=len(vector)
        if len(vector)!=dims:
            return {"status":"unknown","modelKey":key,"samples":0,
                    "reason":"mixed_semantic_dimensions"}
        samples.append((stamp,vector))
    samples.sort(key=lambda x:x[0])
    unique=[]
    for stamp,vec in samples:
        if not unique or stamp!=unique[-1][0]:
            unique.append((stamp,vec))
    if len(unique)<2:
        return {"status":"insufficient_temporal_samples",
                "modelKey":key,"samples":len(unique),
                "reason":"need_two_distinct_frames"}
    adjacent=[similarity(a[1],b[1]) for a,b in zip(unique,unique[1:])]
    gaps=[b[0]-a[0] for a,b in zip(unique,unique[1:])]
    return {
        "status":"uncalibrated_temporal_embedding_evidence",
        "modelKey":key,"samples":len(unique),
        "firstSampleMs":unique[0][0],"lastSampleMs":unique[-1][0],
        "largestObservedSampleGapMs":max(gaps),
        "medianAdjacentCosineSimilarity":_summ(adjacent),
        "minimumAdjacentCosineSimilarity":round(min(adjacent),6),
        "embeddingNoveltyOrWildlifeVerified":False,
        "interpretation":"Cross-frame image similarity, not event identity, animal rarity or motion speed",
    }


def _validate_candidate(doc:dict)->list[dict]:
    if (doc.get("version")!=USER_VERSION
            or doc.get("featureVersion")!=FEATURE_VERSION
            or not isinstance(doc.get("items"),list)
            or len(doc["items"])>5000):
        raise ValueError("Expected versioned personal social-rubric feature template")
    rows=[]
    seen=set()
    for row in doc["items"]:
        if not isinstance(row,dict):
            raise ValueError("Invalid candidate record")
        aid,start,end=(row.get(key) for key in ("assetId","startMs","endMs"))
        if (not isinstance(aid,str) or len(aid)!=64 or
                any(ch not in "0123456789abcdef" for ch in aid)
                or type(start) is not int or type(end) is not int
                or not 0<=start<end or not 1000<=end-start<=60000):
            raise ValueError("Invalid bounded source-backed candidate interval")
        key=(aid,start,end)
        if key in seen:
            raise ValueError("Duplicated candidate in social-rubric template")
        seen.add(key)
        if row.get("featureVersion")!=FEATURE_VERSION:
            raise ValueError("Candidate raw-rubric version mismatch")
        if row.get("sport") not in SPORTS:
            raise ValueError("Unrecognized candidate activity group")
        # This run never overwrites human annotation or marks any rubric
        # feature as observed.
        rows.append({"assetId":aid,"startMs":start,"endMs":end,
                     "sport":row.get("sport")})
    return rows


def _analysis_row(db,root:Path,candidate:dict,*,run:bool,
                  prepare_previews:bool,streamer)->dict:
    aid,start,end=(candidate[k] for k in ("assetId","startMs","endMs"))
    row=db.execute("SELECT id,duration FROM assets WHERE id=?",(aid,)).fetchone()
    if row is None or row["duration"] is None or not _finite(row["duration"]):
        raise ValueError("Referenced asset missing or invalid in catalog")
    if end>int(round(row["duration"]*1000))+1000:
        raise ValueError("Candidate extends beyond indexed source duration")
    path,receipt=preview_paths(root,aid,start,end)
    verified=_check_cached(path,receipt,aid=aid,start_ms=start,end_ms=end)
    prepared=False
    source_verified=False
    if run and prepare_previews and not verified:
        from .studio_preview_judge import generate_preview
        generate_preview(root,streamer,{"assetId":aid,"startMs":start,"endMs":end})
        prepared=True
        source_verified=True
        verified=_check_cached(path,receipt,aid=aid,start_ms=start,end_ms=end)
    evidence={
        "assetId":aid,"sport":candidate["sport"],
        "startMs":start,"endMs":end,
        "previewVerified":verified,"previewPreparedThisRun":prepared,
        "originalSourceChecksumVerifiedThisRun":source_verified,
        "sourceOriginalRead":source_verified,
        "visualTemporal":None,
        "cachedMotionAcoustics":cached_motion_and_audio(db,root,aid,start,end),
        "semanticTemporal":semantic_temporal_evidence(db,root,aid,start,end),
        "featureVersion":FEATURE_VERSION,
        "humanRubricValuesWritten":0,
        "humanRubricFieldsStillUnassessed":list(FEATURES),
        "trueSubjectVisibility":None,
        "physicalSkiSpeed":None,
        "animalSpecies":None,
    }
    if not run:
        evidence["status"]="planned_cached_preview_available" if verified else "missing_verified_preview"
    elif verified:
        evidence["visualTemporal"]=decode_proxy_frames(path)
        evidence["status"]="temporal_visual_metrics_observed"
    else:
        evidence["status"]="missing_verified_preview"
    return evidence


class _SourceReadProgress:
    """Throttled, honest byte throughput reports for full original hashing."""
    def __init__(self):
        self._last = {}

    def bytes(self, phase, done, total, filename):
        key = (phase, filename)
        previous = self._last.get(key, 0)
        if done == total or done - previous >= 256 * 1024 * 1024:
            self._last[key] = done
            print(f"[temporal] source SHA-256 {filename}: "
                  f"{done / (1024 ** 3):.2f}/{total / (1024 ** 3):.2f} GiB",
                  file=sys.stderr, flush=True)


def _progress(index:int,total:int,candidate:dict,event:str)->None:
    print(f"[temporal] {index + 1}/{total} "
          f"{candidate['sport']} {candidate['assetId'][:12]} "
          f"{candidate['startMs']}..{candidate['endMs']}ms {event}",
          file=sys.stderr, flush=True)


def analyze_temporal_evidence(workspace:Path,feature_file:Path,*,limit:int=40,
                              run:bool=False,prepare_previews:bool=False,
                              checkpoint:TemporalCheckpoint|None=None,
                              retry_interrupted:bool=False,
                              retry_failures:bool=False,
                              log_progress:bool=False)->dict:
    if type(limit) is not int or not 1<=limit<=300:
        raise ValueError("Temporal evidence limit must be 1..300")
    if prepare_previews and not run:
        raise ValueError("--prepare-previews requires --run; originals are read only by explicit opt-in")
    root=Path(workspace).expanduser().resolve()
    path=Path(feature_file).expanduser().resolve()
    if (not path.is_relative_to(root) or path==root
            or path.relative_to(root).parts[0] not in ("reports","exports")):
        raise ValueError("Personal template must be inside workspace reports/ or exports/")
    candidates=_validate_candidate(_json(path))[:limit]
    if not candidates:
        raise ValueError("No candidate intervals in social-rubric template")
    # Imported lazily: SourceStream uses full source SHA validation when new
    # derivatives are explicitly authorized, with no remote data transfer.
    from .studio import SourceStream
    streamer=SourceStream(root, progress=_SourceReadProgress()
                          if log_progress else None) if prepare_previews else None
    successes=[]
    errors=[]
    original_reads=0
    original_read_attempts=0
    fresh_previews=0
    resumed_successes=0
    skipped_prior_errors=0
    skipped_interrupted=0
    quarantined_source_candidates=0
    # A blocked full-file read may affect every interval from that source.
    # After restart, never silently open another uncached window from the
    # same source, even when that window had no previous checkpoint.
    quarantined_assets=set()
    if checkpoint is not None and checkpoint.resumed and not retry_interrupted:
        for prior_index,prior_candidate in enumerate(candidates):
            prior=checkpoint.read(prior_index,prior_candidate)
            if (prior is not None and prior["state"]=="in_progress"
                    and prior.get("originalSourceReadAttempted",False)):
                quarantined_assets.add(prior_candidate["assetId"])
    with closing(_open_catalog(root)) as db:
        for index,c in enumerate(candidates):
            previous=checkpoint.read(index,c) if checkpoint is not None else None
            if previous is not None:
                state=previous["state"]
                original_read_attempts+=int(
                    previous.get("originalSourceReadAttempted",False))
                if state=="success" and checkpoint.may_reuse_success(previous,c):
                    entry=previous["result"]
                    successes.append(entry)
                    original_reads+=int(entry["sourceOriginalRead"])
                    fresh_previews+=int(entry["previewPreparedThisRun"])
                    resumed_successes+=1
                    if log_progress:
                        _progress(index,len(candidates),c,"reused verified checkpoint")
                    continue
                if state=="failure" and not retry_failures:
                    errors.append(previous["result"])
                    skipped_prior_errors+=1
                    if log_progress:
                        _progress(index,len(candidates),c,"prior error retained; --retry-failures to retry")
                    continue
                if state=="in_progress" and not retry_interrupted:
                    errors.append({
                        "assetId":c["assetId"],"startMs":c["startMs"],
                        "endMs":c["endMs"],"status":"failed_not_classified",
                        "errorType":"InterruptedBeforeCheckpoint",
                        "error":"An earlier attempt stopped during this clip. "
                                "Skipped for source safety; use --retry-interrupted "
                                "only after checking removable storage.",
                    })
                    skipped_interrupted+=1
                    if log_progress:
                        _progress(index,len(candidates),c,"interrupted; safely skipped")
                    continue
                # If a formerly valid derivative was tampered with or removed,
                # reconstruct rather than trusting its previous visual report.
                original_read_attempts-=int(
                    previous.get("originalSourceReadAttempted",False))
            preview,receipt=preview_paths(root,c["assetId"],
                                           c["startMs"],c["endMs"])
            source_attempt=bool(run and prepare_previews and
                not _check_cached(preview,receipt,aid=c["assetId"],
                                  start_ms=c["startMs"],end_ms=c["endMs"]))
            if source_attempt and c["assetId"] in quarantined_assets:
                errors.append({
                    "assetId":c["assetId"],"startMs":c["startMs"],
                    "endMs":c["endMs"],"status":"failed_not_classified",
                    "errorType":"QuarantinedSourceAfterInterruption",
                    "error":"Another interval from this source previously stalled. "
                            "Skipped to avoid reopening removable media. "
                            "Fix storage and use a new report, or explicitly "
                            "resume with --retry-interrupted before completion.",
                })
                quarantined_source_candidates+=1
                if log_progress:
                    _progress(index,len(candidates),c,"quarantined source; safely skipped")
                continue
            if source_attempt:
                original_read_attempts+=1
            if log_progress:
                _progress(index,len(candidates),c,
                          "verifying original source then preparing preview"
                          if source_attempt else "analyzing cached preview")
            if checkpoint is not None:
                # Written BEFORE any external-card read, surviving SIGINT.
                checkpoint.store(index,c,"in_progress",
                                 original_attempt=source_attempt)
            try:
                entry=_analysis_row(db,root,c,run=run,
                                    prepare_previews=prepare_previews,
                                    streamer=streamer)
                if checkpoint is not None:
                    checkpoint.store(index,c,"success",entry,
                                     original_attempt=source_attempt)
                successes.append(entry)
                original_reads+=int(entry["sourceOriginalRead"])
                fresh_previews+=int(entry["previewPreparedThisRun"])
                if log_progress:
                    _progress(index,len(candidates),c,entry["status"])
            except (ValueError,RuntimeError,OSError,subprocess.TimeoutExpired) as exc:
                failure={
                    "assetId":c["assetId"],"startMs":c["startMs"],
                    "endMs":c["endMs"],"status":"failed_not_classified",
                    "errorType":type(exc).__name__,
                    "error":str(exc)[:200],
                }
                if checkpoint is not None:
                    checkpoint.store(index,c,"failure",failure,
                                     original_attempt=source_attempt)
                errors.append(failure)
                if log_progress:
                    _progress(index,len(candidates),c,
                              "failure recorded: "+type(exc).__name__)
    by_status=Counter(x["status"] for x in successes)
    return {
        "version":VERSION,
        "sourceFeatureTemplate":str(path),
        "sourceTemplateSha256":sha256(path.read_bytes()).hexdigest(),
        "mode":("local_preview_temporal_extraction" if run
                else "cached_evidence_plan"),
        "requested":len(candidates),"reported":len(successes),
        "failed":len(errors),"statusCounts":dict(sorted(by_status.items())),
        "checkpoint": {
            "enabled":checkpoint is not None,
            "resumed":checkpoint.resumed if checkpoint is not None else False,
            "reusedSuccessfulCandidates":resumed_successes,
            "retainedPreviousFailures":skipped_prior_errors,
            "interruptedCandidatesSkipped":skipped_interrupted,
            "quarantinedUncachedSourceCandidates":quarantined_source_candidates,
        },
        "sourceOriginalReads":original_reads,
        "sourceOriginalReadAttempts":original_read_attempts,
        "generatedVerifiedPreviews":fresh_previews,
        "ffmpegVideoDecodeRequests":(sum(x["status"]==
            "temporal_visual_metrics_observed" for x in successes) if run else 0),
        "networkRequests":0,"externalUploads":0,
        "humanRubricFeaturesAutoFilled":0,"originalSourcesModified":False,
        "speedEstimatesGenerated":0,"sceneOrSpeciesAssertions":0,
        "candidates":successes,"failures":errors,
        "limits":[
            "Proxy grayscale temporal change cannot establish smooth carving, animal behavior or camera roll",
            "Embedding similarity is uncalibrated and not a verified novelty score",
            "Image texture depends on water, snow, exposure and scaling; it is not focus accuracy",
            "Original audio cache is independently verified when available; short Studio proxy is muted",
            "Human raw-footage rubric remains missing until separately validated",
            "Failed clips remain unknown rather than treated as negative examples",
            "Only --run --prepare-previews opt-in reads and checksum-verifies original video files",
        ],
    }


def render_html(report:dict,*,root:Path,dest:Path)->str:
    rows=[]
    for item in report["candidates"]:
        visual=item.get("visualTemporal") or {}
        semantic=item.get("semanticTemporal") or {}
        motion=item.get("cachedMotionAcoustics") or {}
        cells=(
            item["sport"],item["assetId"][:12],
            f'{item["startMs"]/1000:.1f}–{item["endMs"]/1000:.1f}s',
            item["status"],visual.get("frameCount"),
            visual.get("frameChangeMedian"),visual.get("luminanceMedian"),
            motion.get("motionChangeMedian"),
            motion.get("audioRmsMedian"),
            semantic.get("samples"),semantic.get("medianAdjacentCosineSimilarity"),
        )
        playback="Unavailable; no verified H.264 preview"
        if item["previewVerified"]:
            cached,_=preview_paths(root,item["assetId"],
                                    item["startMs"],item["endMs"])
            # Render only a derived workspace-local media URL. This isn't
            # a trusted server endpoint; browsers can restrict file:// video.
            relative=os.path.relpath(cached,dest)
            if cached.is_relative_to(root/"reports"/"studio-preview-cache-v1"):
                playback=(
                    '<details><summary>Play verified interval</summary>'
                    '<video controls preload="none" playsinline width="280" '
                    'src="'+escape(quote(relative,safe="/."),quote=True)+'">'
                    'Your browser cannot play this local preview.</video>'
                    '</details>')
        rows.append("<tr>"+"".join(
            "<td>"+escape("unknown" if value is None else str(value))+"</td>"
            for value in cells)+"<td>"+playback+"</td></tr>")
    heads=("Sport","Asset SHA","Window","Coverage","Decoded frames",
           "Proxy change","Median luma","Motion cache","Audio RMS",
           "Semantic samples","Adjacent similarity","Playback")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Klip · Temporal evidence</title><style>'
        'body{background:#101826;color:#e9eef4;font:14px system-ui;'
        'max-width:1250px;margin:35px auto;padding:0 18px;line-height:1.55}'
        'h1{font-size:1.7rem}p{color:#b8c9d7}table{border-collapse:collapse;'
        'width:100%}th,td{text-align:left;padding:9px;'
        'border-bottom:1px solid #344554}th{color:#f1aa98}</style>'
        '</head><body><h1>Social Lens · temporal evidence pilot</h1>'
        '<p>Private, proxy-only frame measurements and verified semantic caches. '
        'These are NOT calibrated shot quality, athletic speed, wildlife identities, '
        'or completed human rubric entries. Missing evidence remains unknown.</p>'
        '<p>Analyzed '+str(report["requested"])+' windows · '+
        str(report["failed"])+' failures · '+
        str(report["sourceOriginalReadAttempts"])+' attempted source reads · '+
        str(report["sourceOriginalReads"])+' successfully completed source reads</p>'
        '<p>Video links use local verified derivatives only. If a browser '
        'restricts local file playback, use Klip Studio instead.</p>'
        '<table><thead><tr>'+
        "".join("<th>"+escape(x)+"</th>" for x in heads)+
        "</tr></thead><tbody>"+"".join(rows)+"</tbody></table></body></html>"
    )


def save_temporal_evidence(workspace:Path,feature_file:Path,output_dir:Path,*,
                           limit:int=40,run:bool=False,
                           prepare_previews:bool=False,
                           resume:bool=False,
                           retry_interrupted:bool=False,
                           retry_failures:bool=False)->dict:
    root=Path(workspace).expanduser().resolve()
    if (retry_interrupted or retry_failures) and not resume:
        raise ValueError("--retry-interrupted and --retry-failures require --resume")
    if type(limit) is not int or not 1<=limit<=300:
        raise ValueError("Temporal evidence limit must be 1..300")
    if prepare_previews and not run:
        raise ValueError("--prepare-previews requires --run")
    path=Path(feature_file).expanduser().resolve()
    if (path==root or not path.is_relative_to(root)
            or path.relative_to(root).parts[0] not in ("reports","exports")):
        raise ValueError("Feature template must be inside workspace reports/ or exports/")
    fingerprint=sha256(path.read_bytes()).hexdigest()
    candidates=_validate_candidate(_json(path))[:limit]
    if not candidates:
        raise ValueError("No candidate intervals in social-rubric template")
    dest=Path(output_dir).expanduser().resolve() if resume else _new_folder(root,output_dir)
    journal=TemporalCheckpoint(root,dest,fingerprint,candidates,run=run,
                               prepare_previews=prepare_previews,resume=resume)
    with journal.exclusive():
        report=analyze_temporal_evidence(root,path,limit=limit,run=run,
                                         prepare_previews=prepare_previews,
                                         checkpoint=journal,
                                         retry_interrupted=retry_interrupted,
                                         retry_failures=retry_failures,
                                         log_progress=True)
        if sha256(path.read_bytes()).hexdigest()!=fingerprint:
            raise ValueError("Feature template changed during analysis; checkpoint retained, no final report")
        # JSON is the completion signal. Publish HTML first, then JSON atomically.
        # An interrupt can never leave a misleading half-written final JSON.
        html_tmp=dest/"index.html.tmp"
        html_tmp.write_text(render_html(report,root=root,dest=dest),encoding="utf-8")
        os.replace(html_tmp,dest/"index.html")
        _atomic_json(dest/"temporal-evidence.json",report)
    return {"status":"temporal_evidence_report_created",
            "json":str(dest/"temporal-evidence.json"),
            "html":str(dest/"index.html"),
            "mode":report["mode"],
            "requested":report["requested"],
            "measured":report["statusCounts"].get(
                "temporal_visual_metrics_observed",0),
            "failed":report["failed"],
            "sourceOriginalReads":report["sourceOriginalReads"],
            "sourceOriginalReadAttempts":report["sourceOriginalReadAttempts"],
            "generatedVerifiedPreviews":report["generatedVerifiedPreviews"],
            "checkpoint":report["checkpoint"],
            "humanRubricFeaturesAutoFilled":0,"externalUploads":0,
            "originalSourcesModified":False}

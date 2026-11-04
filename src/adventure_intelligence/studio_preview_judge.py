"""On-demand private H.264 micro-previews and opt-in localhost-only visual triage.

Never upload DJI media. Source identity verified by the existing Studio
resolver before FFmpeg opens any original. Preview derivatives are bounded,
atomic, and remain private to the workspace. Ollama sees three preview
JPEGs through a literal loopback address only, and cannot write human labels.
"""
from __future__ import annotations

import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .media import require_tool

VERSION="studio-preview-local-judge-v1"
_MODEL=re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,100}$")


def valid_local_model(model):
    # Allow Ollama namespace/model:tag identifiers but reject endpoint URLs.
    return (isinstance(model,str) and bool(_MODEL.fullmatch(model))
            and "://" not in model and ".." not in model)


_CACHE_LOCK=threading.RLock()
MAX_PREVIEW_BYTES=45*1024*1024


def _interval_key(aid:str,start_ms:int,end_ms:int):
    if (not isinstance(aid,str) or len(aid)!=64 or
            any(ch not in "0123456789abcdef" for ch in aid) or
            type(start_ms) is not int or type(end_ms) is not int or
            start_ms<0 or not start_ms+1000<=end_ms<=start_ms+60000):
        raise ValueError("Invalid bounded preview identity or duration")
    payload=f"{VERSION}:{aid}:{start_ms}:{end_ms}:h264-crf29-960p-noaudio"
    return sha256(payload.encode()).hexdigest()


def preview_paths(workspace:Path,aid:str,start_ms:int,end_ms:int):
    key=_interval_key(aid,start_ms,end_ms)
    base=Path(workspace).resolve()/"reports"/"studio-preview-cache-v1"
    return base/(key+".mp4"),base/(key+".json")


def _check_cached(path:Path,manifest:Path,*,aid:str,start_ms:int,end_ms:int):
    if not path.is_file() or not manifest.is_file():
        return False
    if path.stat().st_size>MAX_PREVIEW_BYTES or path.stat().st_size<64:
        return False
    try:
        j=json.loads(manifest.read_text())
        return (j.get("assetId")==aid and j.get("startMs")==start_ms and
                j.get("endMs")==end_ms and
                j.get("previewSha256")==sha256(path.read_bytes()).hexdigest())
    except (OSError,ValueError,TypeError):
        return False


def generate_preview(workspace:Path,streamer,assignment:dict,*,timeout:int=150)->dict:
    aid=assignment["assetId"]
    start,end=assignment["startMs"],assignment["endMs"]
    path,manifest=preview_paths(workspace,aid,start,end)
    with _CACHE_LOCK:
        # A successful original source check is compulsory even if cached;
        # SourceStream memoizes a fully verified identity within this server.
        src,original=streamer.verified_path(aid)
        if _check_cached(path,manifest,aid=aid,start_ms=start,end_ms=end):
            return {"status":"cached","path":str(path),
                    "bytes":path.stat().st_size,"videoSourceModified":False}
        ffmpeg=require_tool("ffmpeg")
        path.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix="klip-preview-",suffix=".mp4",dir=path.parent)
        os.close(fd)
        try:
            command=[ffmpeg,"-hide_banner","-loglevel","error","-nostdin","-y",
                     "-ss",f"{start/1000:.3f}","-i",str(src),
                     "-t",f"{(end-start)/1000:.3f}",
                     "-map","0:v:0","-an","-sn",
                     "-vf","scale='min(960,iw)':-2",
                     "-c:v","libx264","-preset","veryfast","-crf","29",
                     "-pix_fmt","yuv420p","-movflags","+faststart",
                     "-f","mp4",tmp]
            job=subprocess.run(command,stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE,timeout=timeout,check=False)
            if job.returncode or not Path(tmp).is_file() or Path(tmp).stat().st_size<64:
                raise RuntimeError("Preview transcoding failed; inspect original video or codec")
            if Path(tmp).stat().st_size>MAX_PREVIEW_BYTES:
                raise RuntimeError("Preview exceeds the configured safe size limit")
            st=src.stat()
            fields=(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
            if fields!=original:
                raise RuntimeError("Original source changed during preview generation")
            data_sha=sha256(Path(tmp).read_bytes()).hexdigest()
            record={"version":VERSION,"assetId":aid,"startMs":start,
                    "endMs":end,"previewSha256":data_sha,
                    "originalSha256Verified":True,
                    "codec":"h264","generatedFromOriginal":True}
            # Atomic derivative and receipt; crash leaves no valid cached pair.
            os.replace(tmp,path)
            temp_manifest=manifest.with_suffix(".json.tmp")
            temp_manifest.write_text(json.dumps(record,sort_keys=True))
            os.replace(temp_manifest,manifest)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return {"status":"prepared","path":str(path),
                "bytes":path.stat().st_size,"videoSourceModified":False}


def _jpeg_samples(path:Path,*,limit:int=3)->list[str]:
    ffmpeg=require_tool("ffmpeg")
    # Extract a few independently decoded stills from the *small proxy*, not
    # the multi-gigabyte original. The video model must not be presented
    # as having inspected all original frames, action dynamics, or audio.
    samples=[]
    with tempfile.TemporaryDirectory(prefix="klip-local-judge-") as folder:
        for idx,fraction in enumerate((.18,.50,.82)[:limit]):
            # The preview is <=60 sec and we read duration with ffprobe.
            from .media import probe
            duration=float(probe(path)["duration"])
            t=max(0.0,duration*fraction)
            out=Path(folder)/(f"frame-{idx}.jpg")
            result=subprocess.run(
                [ffmpeg,"-hide_banner","-loglevel","error","-nostdin",
                 "-ss",f"{t:.3f}","-i",str(path),
                 "-frames:v","1","-vf","scale='min(512,iw)':-2",
                 "-q:v","6","-y",str(out)],
                stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                timeout=25,check=False)
            if result.returncode or not out.is_file() or not 100<out.stat().st_size<220000:
                raise RuntimeError("Cannot sample an inspectable local judge frame")
            samples.append(base64.b64encode(out.read_bytes()).decode("ascii"))
    return samples


def _validate_judgment(result:dict)->dict:
    if not isinstance(result,dict):
        raise ValueError("AI judge response must be a JSON object")
    recommendation=result.get("recommendation")
    if recommendation not in ("keep","skip","unsure"):
        raise ValueError("AI judge must give keep, skip or unsure")
    event=result.get("observedEvent")
    if event not in ("yes","no","uncertain"):
        raise ValueError("AI judge event assessment is invalid")
    summary=result.get("summary")
    why=result.get("reason")
    if (not isinstance(summary,str) or len(summary)>240 or
            not isinstance(why,str) or len(why)>480):
        raise ValueError("AI judge text exceeds review limits")
    limitations=result.get("limitations")
    if not isinstance(limitations,list) or len(limitations)>5 or any(
            not isinstance(v,str) or len(v)>150 for v in limitations):
        raise ValueError("AI judge evidence limitations invalid")
    return {
        "recommendation":recommendation,"observedEvent":event,
        "summary":summary,"reason":why,"limitations":limitations,
        "reviewedByHuman":False,"speciesVerified":False,
        "athleteTechniqueVerified":False,
        "sourceFramesSeen":3,"videoOrAudioReviewed":False,
        "interpretation":"Provisional triage based on three frames only, not clip ground truth",
    }


def judge_preview(path:Path,*,model:str,transport=None,timeout:int=150)->dict:
    if not valid_local_model(model):
        raise ValueError("Explicit installed local vision model is required")
    images=_jpeg_samples(path)
    schema={
        "type":"object","additionalProperties":False,
        "required":["recommendation","observedEvent","summary","reason","limitations"],
        "properties":{
            "recommendation":{"type":"string","enum":["keep","skip","unsure"]},
            "observedEvent":{"type":"string","enum":["yes","no","uncertain"]},
            "summary":{"type":"string"},"reason":{"type":"string"},
            "limitations":{"type":"array","items":{"type":"string"}},
        }}
    instructions=(
        "You are a *provisional* clip triage assistant for snorkeling, skiing and "
        "mountain biking. These are three still images from one short video "
        "interval ordered chronologically. Use only observable evidence. "
        "A plausible rare animal, hard jump, or impressive technique is NOT "
        "verified from sparse images. Never invent animal species, location, "
        "animal count, achieved tricks, continuous behavior, or performance. "
        "Separate subject visibility from ambient clarity: a striking ray "
        "can be more interesting than clear empty sand. Prioritize possible "
        "story, visibility, novelty and evidence of an event. Err toward "
        "'unsure' when the frames cannot establish an event; do not eliminate "
        "potentially important control intervals merely because they look dull. "
        "Return concise, nontechnical JSON with one evidence limitation."
    )
    body={"model":model,"stream":False,"format":schema,
          "options":{"temperature":0},
          "messages":[{"role":"user","content":instructions,"images":images}]}
    data=json.dumps(body).encode()
    if len(data)>1_500_000:
        raise ValueError("Judge request exceeds size limit")
    if transport:
        response=transport(body)
    else:
        req=Request("http://127.0.0.1:11434/api/chat",data=data,
                    headers={"Content-Type":"application/json"},method="POST")
        try:
            with urlopen(req,timeout=timeout) as res:
                raw=res.read(131073)
                if len(raw)>131072:
                    raise ValueError("Local judge response exceeds size limit")
                response=json.loads(raw)
        except (URLError,TimeoutError,HTTPError) as exc:
            raise RuntimeError(
                "Local Ollama is unavailable. Start Ollama and install an image-capable model."
            ) from None
    try:
        raw_content=response["message"]["content"]
        parsed=json.loads(raw_content)
    except (KeyError,TypeError,ValueError) as exc:
        raise ValueError("Local model returned invalid JSON") from exc
    return {**_validate_judgment(parsed),"model":model,
            "backend":"ollama_loopback_only",
            "totalDurationNs":response.get("total_duration"),
            "networkDestination":"127.0.0.1",
            "humanApprovalRequired":True}



def _judge_record_path(path:Path,model:str)->Path:
    if not valid_local_model(model):
        raise ValueError("Invalid local vision model name")
    return path.with_name(path.stem+"-judge-"+
                          sha256(model.encode()).hexdigest()[:16]+".json")


def cached_judgment(preview:Path,*,model:str)->dict|None:
    record=_judge_record_path(preview,model)
    if not record.is_file() or not preview.is_file() or record.stat().st_size>12000:
        return None
    try:
        cached=json.loads(record.read_text(encoding="utf-8"))
        if (cached.get("model")!=model or
                cached.get("previewSha256")!=sha256(preview.read_bytes()).hexdigest()):
            return None
        answer=_validate_judgment(cached["suggestion"])
        return {**answer,"model":model,"backend":"ollama_loopback_only",
                "cached":True,"humanApprovalRequired":True}
    except (KeyError,TypeError,ValueError,OSError):
        return None


def local_judgment(preview:Path,*,model:str,transport=None)->dict:
    with _CACHE_LOCK:
        previous=cached_judgment(preview,model=model)
        if previous is not None:
            return previous
        result=judge_preview(preview,model=model,transport=transport)
        record={
            "model":model,"previewSha256":sha256(preview.read_bytes()).hexdigest(),
            "suggestion":{k:result[k] for k in ("recommendation","observedEvent",
                "summary","reason","limitations")}
        }
        path=_judge_record_path(preview,model)
        path.write_text(json.dumps(record,allow_nan=False,sort_keys=True),
                        encoding="utf-8")
        return {**result,"cached":False}

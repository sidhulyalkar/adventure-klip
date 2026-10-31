"""Create an independently sampled local benchmark, then optionally extract frames.

No media extraction without explicit --decode-frames. Do not run this alongside
an active long-running SSD processing pass. A random interval is NOT a negative.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import random
import subprocess

from .catalog import connect
from .media import require_tool
from .sources import resolve_source

VERSION="nim-benchmark-sampler-v1"
MAX_SAMPLE=80
FRAMES_PER_CLIP=6
MAX_PER_ASSET=2

def _clip_id(asset_id: str, start: int, end: int, pool: str) -> str:
    return "judge-"+hashlib.sha256(
        f"{asset_id}:{start}:{end}:{pool}".encode()).hexdigest()[:20]

def _times(start: int, end: int) -> list[int]:
    # Requested sparse seek times, not verified frame PTS. Leave a small buffer.
    duration=end-start
    return [start+max(0,min(duration-1,round(duration*f)))
            for f in (.06,.23,.40,.57,.74,.91)]

def plan_benchmark(assets: list[dict], episodes: list[dict], *,
                   sample_count: int = 12, seed: int = 20261031,
                   window_ms: int = 14000) -> dict:
    """Select from candidate episodes and duration-weighted random footage.

    We retain the sampling pool locally but do not pass it to the vision judges.
    Sample distribution is quota-limited and is NOT a perfectly uniform sample.
    """
    if (type(sample_count) is not int or not 2<=sample_count<=MAX_SAMPLE or
        type(seed) is not int or not 0<=seed<=2**31 or
        type(window_ms) is not int or not 3000<=window_ms<=30000):
        raise ValueError("Invalid sampling parameters")
    rng=random.Random(seed)
    eligible={a["id"]:a for a in assets if
              isinstance(a.get("duration"),(float,int)) and
              a["duration"]*1000>=max(3000,window_ms)}
    candidates=[]
    for ep in episodes:
        aid=ep.get("asset_id")
        if aid not in eligible or ep.get("kind")=="recording_summary":
            continue
        a,b=ep.get("start_ms"),ep.get("end_ms")
        if type(a) is not int or type(b) is not int or a<0 or b<=a:
            continue
        if b-a<3000 or b-a>30000 or b>round(eligible[aid]["duration"]*1000):
            continue
        # An episode already represents a bounded candidate, not a guaranteed event.
        candidates.append((aid,a,b))
    rng.shuffle(candidates)
    need_candidate=sample_count//2
    chosen=[]
    counts={}
    keys=set()
    for aid,start,end in candidates:
        if counts.get(aid,0)>=MAX_PER_ASSET:
            continue
        key=(aid,start,end)
        if key in keys:
            continue
        chosen.append((aid,start,end,"candidate"))
        keys.add(key)
        counts[aid]=counts.get(aid,0)+1
        if len(chosen)>=need_candidate:
            break
    # Independent random windows weighted by available video seconds.
    # Do NOT filter for absence of model candidates, as this biases the
    # random pool and invalidates missed-highlight estimates.
    asset_list=list(eligible.values())
    capacities=[max(1,round(a["duration"]*1000)-window_ms+1) for a in asset_list]
    target=sample_count-len(chosen)
    attempts=0
    while len(chosen)<sample_count and asset_list and attempts<sample_count*100:
        attempts+=1
        asset=rng.choices(asset_list,weights=capacities,k=1)[0]
        aid=asset["id"]
        if counts.get(aid,0)>=MAX_PER_ASSET:
            continue
        max_start=round(asset["duration"]*1000)-window_ms
        start=rng.randrange(max_start+1)
        end=start+window_ms
        key=(aid,start,end)
        if key in keys:
            continue
        keys.add(key)
        chosen.append((aid,start,end,"random"))
        counts[aid]=counts.get(aid,0)+1
    if len(chosen)<sample_count:
        raise ValueError("Insufficient eligible independent video intervals")
    rng.shuffle(chosen)
    clips=[]
    for aid,start,end,pool in chosen:
        cid=_clip_id(aid,start,end,pool)
        clips.append({"clipId":cid,"sourceAssetId":aid,"pool":pool,
                      "startMs":start,"endMs":end,
                      "frames":[{"tMs":t,"path":f"{cid}/frame-{index:02}.jpg"}
                                for index,t in enumerate(_times(start,end))]})
    return {"schemaVersion":1,"samplerVersion":VERSION,"seed":seed,
            "requestedSamples":sample_count,"windowMs":window_ms,
            "selectionPolicy":"candidate episodes plus duration-weighted random windows; max 2 per asset",
            "candidateCount":sum(x["pool"]=="candidate" for x in clips),
            "randomCount":sum(x["pool"]=="random" for x in clips),
            "limitations":["Random windows are not guaranteed negatives.",
                           "Quota-limited duration-weighted sampling is not unbiased uniform footage sampling.",
                           "Sparse still images do not show continuous motion or audio."],
            "clips":clips}

def prepare_benchmark(workspace: Path, *, sample_count=12, seed=20261031,
                      window_ms=14000, decode_frames=False, output_dir: Path) -> dict:
    workspace=Path(workspace).expanduser().resolve()
    output_dir=Path(output_dir).expanduser().resolve()
    if not output_dir.is_relative_to(workspace) or output_dir==workspace:
        raise ValueError("Benchmark output directory must be inside the Klip workspace")
    if output_dir.exists():
        raise ValueError("Output directory exists; use a new benchmark run directory")
    with connect(workspace) as db:
        assets=[dict(x) for x in db.execute(
            "SELECT id,duration,stored_path FROM assets ORDER BY id")]
        episodes=[dict(x) for x in db.execute(
            "SELECT asset_id,kind,start_ms,end_ms FROM episodes ORDER BY asset_id,start_ms")]
        manifest=plan_benchmark(assets,episodes,sample_count=sample_count,
                                seed=seed,window_ms=window_ms)
        selected=set(x["sourceAssetId"] for x in manifest["clips"])
        selected_assets={x["id"]:x for x in assets if x["id"] in selected}
        # Never put generated benchmark frames anywhere under the source tree.
        for a in selected_assets.values():
            parent=Path(a["stored_path"]).resolve().parent
            if output_dir.is_relative_to(parent):
                raise ValueError("Benchmark output cannot be created under source footage")
        source_map={}
        if decode_frames:
            for aid,asset in selected_assets.items():
                source_map[aid]=resolve_source(db,asset)
    output_dir.mkdir(parents=True,exist_ok=False)
    (output_dir/"frames").mkdir()
    (output_dir/"manifest.json").write_text(json.dumps(
        manifest,indent=2)+"\n",encoding="utf-8")
    if not decode_frames:
        return {"mode":"plan_only","manifest":str(output_dir/"manifest.json"),
                "framesPresent":False,"clipsPlanned":len(manifest["clips"])}
    ffmpeg=require_tool("ffmpeg")
    generated=0
    # Strictly sequential so processing cost and SSD I/O stay controllable.
    for clip in manifest["clips"]:
        aid=clip["sourceAssetId"]
        folder=output_dir/"frames"/clip["clipId"]
        folder.mkdir()
        for frame in clip["frames"]:
            target=output_dir/"frames"/frame["path"]
            cmd=[ffmpeg,"-hide_banner","-loglevel","error","-nostdin",
                 "-ss",f'{frame["tMs"]/1000:.3f}',"-i",str(source_map[aid]),
                 "-map","0:v:0","-an","-sn","-frames:v","1",
                 "-vf","scale=448:-2","-q:v","5",
                 "-f","image2pipe","-vcodec","mjpeg","pipe:1"]
            decoded=subprocess.run(cmd,capture_output=True,check=False,timeout=120)
            if decoded.returncode or not decoded.stdout.startswith(b"\xff\xd8\xff"):
                raise RuntimeError("FFmpeg could not decode an approved preview frame")
            if len(decoded.stdout)>320_000:
                raise ValueError("Frame exceeds the safe preview upload size")
            target.write_bytes(decoded.stdout)
            generated+=1
    return {"mode":"locally_prepared","manifest":str(output_dir/"manifest.json"),
            "framesDir":str(output_dir/"frames"),
            "clipsPrepared":len(manifest["clips"]),"framesPrepared":generated,
            "networkRequests":0}

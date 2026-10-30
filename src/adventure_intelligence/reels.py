"""Deterministic non-overlapping episode selection and FFmpeg edit rendering."""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import tempfile
from .catalog import connect
from .media import require_tool
from .sources import resolve_source, verified_digest
from .safety import protect_source_output

RECIPES={
    "motion-review":{"activity":None,"kinds":{"camera_rhythm_candidate":1.0,"sustained_motion_candidate":0.7,"active_sequence":0.4}},
    "ski-highlights":{"activity":"ski","kinds":{"jump_chain":1.0,"carving_flow":0.9,"tree_weaving":0.9,"technical_feature_chain":0.85,"active_sequence":0.25,"camera_rhythm_candidate":0.17,"sustained_motion_candidate":0.12}},
    "bike-highlights":{"activity":"bike","kinds":{"technical_feature_chain":1.0,"active_sequence":0.35,"camera_rhythm_candidate":0.17,"sustained_motion_candidate":0.12}},
    "snorkel-discoveries":{"activity":"snorkeling","kinds":{"wildlife_encounter":1.0,"active_sequence":0.2,"sustained_motion_candidate":0.10}},
    "best-sequences":{"activity":None,"kinds":{"jump_chain":1.0,"carving_flow":1.0,"tree_weaving":1.0,"technical_feature_chain":1.0,"wildlife_encounter":1.0,"active_sequence":0.5,"camera_rhythm_candidate":0.18,"sustained_motion_candidate":0.12}},
}


def plan_reel(workspace: Path, recipe: str, max_seconds: int=75) -> dict:
    if recipe not in RECIPES:
        raise ValueError(f"Unknown recipe: {recipe}. Options: {', '.join(RECIPES)}")
    if max_seconds<1:
        raise ValueError("max_seconds must be positive")
    config=RECIPES[recipe]
    with connect(workspace) as db:
        rows=[dict(r) for r in db.execute("""SELECT e.*,a.activity,a.stored_path,
          a.metadata_json FROM episodes e JOIN assets a ON a.id=e.asset_id""")]
    candidates=[]
    for r in rows:
        if config["activity"] and r["activity"]!=config["activity"]:
            continue
        bonus=config["kinds"].get(r["kind"])
        if bonus is None:
            continue
        dur=(r["end_ms"]-r["start_ms"])/1000
        if dur<0.6:
            continue
        candidates.append({"episodeId":r["id"],"sourceAssetId":r["asset_id"],
                           "kind":r["kind"],"startMs":r["start_ms"],"endMs":r["end_ms"],
                           "duration":dur,"score":round(r["score"]*bonus,4)})
    # Greedy: rank quality, suppress temporal overlap on the same recording.
    # Plan contains no local paths, suitable for review and portable serialization.
    candidates.sort(key=lambda x:(-x["score"],x["sourceAssetId"],x["startMs"]))
    chosen=[]
    total=0
    for e in candidates:
        if total+e["duration"]>max_seconds:
            continue
        if any(c["sourceAssetId"]==e["sourceAssetId"] and
               e["startMs"]<c["endMs"] and c["startMs"]<e["endMs"] for c in chosen):
            continue
        chosen.append(e)
        total+=e["duration"]
    # Narrative assembly rule for MVP: chronological order within individual source assets.
    chosen.sort(key=lambda e:(e["sourceAssetId"],e["startMs"]))
    return {"schemaVersion":1,"recipe":recipe,"maxSeconds":max_seconds,
            "totalSeconds":round(total,3),"segments":chosen,
            "status":"draft","disclaimer":"Unreviewed camera motion is not a confirmed athletic maneuver"}


def render_reel(workspace: Path, plan: dict, output: Path, height: int=720,
                transition: str="cut", fade_seconds: float=0.35) -> dict:
    if not plan.get("segments"):
        raise ValueError("No segments to render")
    if plan.get("schemaVersion")!=1:
        raise ValueError("Unsupported plan schemaVersion")
    if height not in {480,720,1080}:
        raise ValueError("height must be 480, 720 or 1080")
    if transition not in {"cut", "fade"}:
        raise ValueError("Unsupported transition")
    if not 0.1 <= fade_seconds <= 1.0:
        raise ValueError("fade_seconds must be between 0.1 and 1.0")
    ffmpeg=require_tool("ffmpeg")
    output=output.expanduser().resolve()
    with connect(workspace) as db:
        protect_source_output(db, output)
        sources={row["id"]:dict(row) for row in db.execute("SELECT * FROM assets")}
        verified = {}
        for segment in plan['segments']:
            aid = segment['sourceAssetId']
            if aid not in sources:
                raise ValueError(f'Unknown asset {aid}')
            if aid not in verified:
                verified[aid] = resolve_source(db,sources[aid])
        protected = [Path(row['stored_path']) for row in sources.values()]
        protected.extend(Path(row[0]) for row in db.execute('SELECT original_path FROM imports'))
        if any(output == path.resolve() or
               (output.exists() and path.exists() and output.samefile(path))
               for path in protected):
            raise ValueError('Output cannot overwrite a source video')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="adventure-reel-") as tmp:
        folder=Path(tmp)
        clips=[]
        durations=[]
        for i,s in enumerate(plan["segments"]):
            asset=sources.get(s["sourceAssetId"])
            if not asset:
                raise ValueError(f"Unknown asset {s['sourceAssetId']}")
            start=int(s["startMs"])
            end=int(s["endMs"])
            duration=(end-start)/1000
            if start<0 or duration<=0 or end>int((asset["duration"] or 0)*1000):
                raise ValueError("Invalid segment boundaries")
            source=verified[asset["id"]]
            if not source.is_file():
                raise FileNotFoundError(source)
            has_audio=json.loads(asset["metadata_json"]).get("audio",False)
            clip=folder/f"clip-{i:04}.mp4"
            cmd=[ffmpeg,"-hide_banner","-loglevel","error","-y", "-ss",f"{start/1000:.3f}","-i",str(source)]
            if not has_audio:
                cmd.extend(["-f","lavfi","-i","anullsrc=channel_layout=stereo:sample_rate=48000"])
            fade_filter = (f",fade=t=in:st=0:d={fade_seconds:.3f},"
                           f"fade=t=out:st={max(0.,duration-fade_seconds):.3f}:d={fade_seconds:.3f}"
                           if transition=="fade" else "")
            audio_filter = (f"afade=t=in:st=0:d={fade_seconds:.3f},"
                            f"afade=t=out:st={max(0.,duration-fade_seconds):.3f}:d={fade_seconds:.3f}"
                            if transition=="fade" else "anull")
            cmd.extend(["-t",f"{duration:.3f}","-map","0:v:0","-map","0:a:0" if has_audio else "1:a:0",
                "-vf",f"scale=-2:{height}:flags=lanczos,pad=ceil(iw/2)*2:ceil(ih/2)*2{fade_filter}","-af",audio_filter,
                "-r","30","-c:v","libx264","-preset","veryfast","-crf","21",
                "-pix_fmt","yuv420p","-c:a","aac","-ar","48000","-ac","2",
                "-movflags","+faststart",str(clip)])
            subprocess.run(cmd,check=True)
            clips.append(clip)
            durations.append(duration)
        temp_out=folder/"result.mp4"
        # Fade-through-black is intentionally used for broad FFmpeg compatibility.
        # A cross-dissolve needs runtime filter support plus normalized timelines.
        concat=folder/"list.ffconcat"
        concat.write_text("ffconcat version 1.0\n"+"".join(f"file '{p.name}'\n" for p in clips))
        subprocess.run([ffmpeg,"-hide_banner","-loglevel","error","-y","-safe","0",
                        "-f","concat","-i",str(concat),"-c","copy", "-movflags","+faststart",
                        str(temp_out)],check=True)
        for aid,path in verified.items():
            if verified_digest(path) != aid:
                raise OSError('Source changed during rendering')
        import shutil
        shutil.copy2(temp_out,output)
    return {"path":str(output),"segments":len(clips),"transition":transition,
            "transitionStyle":"fade-through-black" if transition=="fade" else "hard-cut"}

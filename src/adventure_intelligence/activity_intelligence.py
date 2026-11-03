"""Sparse, checkpointed, opt-in activity proposals for a mixed POV catalog.

No color-only sport classification. SigLIP2 zero-shot comparisons are
uncalibrated proposals; apply is separate, bounded and explicit.
Never upload frames, mutate video, infer precise location or use filename dates
as ground truth. Source SHA-256 is checked once per newly scanned asset.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from html import escape
from io import BytesIO
import json
import math
import os
import sys
from pathlib import Path
from statistics import median

from .catalog import connect
from .scene_atlas import unit_vector
from .semantic_jobs import completed_document
from .sources import resolve_source
from .frame_timing import extract_frame_png
from .safety import protect_source_output

VERSION = "activity-sparse-zeroshot-v1"
MODEL_ID = "google/siglip2-base-patch16-224"
PROMPTS = {
    "snorkeling": [
        "First person camera underwater while snorkeling through a colorful coral reef.",
        "Underwater action camera swimming above a rocky reef with marine life and algae.",
        "Underwater view of kelp forest, seagrass, fish and the ocean floor.",
        "First person snorkeling on the ocean surface, partially submerged, looking at fish below.",
    ],
    "ski": [
        "First person skiing downhill on a snow-covered mountain with skis visible.",
        "Action camera skiing through snow and trees on a winter mountain slope.",
        "Ski chairlift above snowy trails with mountains and ski equipment visible.",
        "Skier standing on a groomed snowy ski trail on a winter mountain.",
    ],
    "bike": [
        "First person mountain biking on a forest singletrack dirt trail.",
        "Mountain bike handlebars visible riding downhill across rocks and roots.",
        "Action camera riding a mountain bike on a narrow desert or rocky trail.",
        "Person standing next to a mountain bike by a dirt trail.",
    ],
    "other": [
        "First person hiking or walking on a trail without a bicycle.",
        "A person driving a car on a road with windshield and dashboard.",
        "Above-water view of the beach, sky, sunlight and waves, not snorkeling.",
        "Indoor room or urban environment recorded with an action camera.",
    ],
}
ACTIVITIES = ("snorkeling", "ski", "bike", "other")
_EVIDENCE_MARGIN = .025
_STRONG_MEDIAN_MARGIN = .050


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value) -> str:
    return sha256(_canonical(value).encode()).hexdigest()


def _sample_times(duration_ms: int, count: int) -> list[int]:
    if type(duration_ms) is not int or duration_ms <= 0:
        return []
    if type(count) is not int or not 3 <= count <= 9:
        raise ValueError("Sample count must be 3..9")
    # Avoid edge fades / startup occlusions while evenly covering the trip.
    return sorted(set(min(duration_ms-1, max(0, round(duration_ms*f)))
                      for f in [(.06 + .88*i/(count-1)) for i in range(count)]))


def _blue_cue(png: bytes) -> dict:
    """Low-cost cue ONLY; ocean/snow/sky cannot be separated by RGB alone."""
    try:
        from PIL import Image
        image = Image.open(BytesIO(png)).convert("RGB").resize((64, 48))
    except Exception as exc:
        raise ValueError("Invalid PNG frame") from exc
    w, h = image.size
    px = image.load()
    selected = 0
    blue = 0
    lower_blue = 0
    lower = 0
    for y in range(h):
        for x in range(w):
            r, g, b = px[x,y]
            # Reject nearly white snow and very dark pixels.
            colored = (b > r*1.20 and b > 35 and g > r*.78
                       and max(r,g,b)-min(r,g,b) >= 22)
            selected += 1
            blue += bool(colored)
            if y >= h//2:
                lower += 1
                lower_blue += bool(colored)
    return {"bluePixelFraction": round(blue/selected, 4),
            "lowerHalfBlueFraction": round(lower_blue/lower, 4),
            "interpretation": "color_only_not_underwater_evidence"}


def _frame_scores(text_vectors: dict, image_vectors: list[list[float]]) -> list[dict]:
    """Pure cosine scoring shared by cached and newly decoded image vectors."""
    results = []
    for vector in image_vectors:
        v = unit_vector(vector)
        if v is None:
            continue
        group_scores = {}
        for activity, refs in text_vectors.items():
            if not refs or any(len(v)!=len(ref) for ref in refs):
                raise ValueError("Incompatible SigLIP2 text-image embedding dimensions")
            similarities = [sum(x*y for x,y in zip(v,ref)) for ref in refs]
            group_scores[activity] = max(similarities)
        ranked = sorted(group_scores, key=lambda x:(-group_scores[x],x))
        first, second = ranked[:2]
        gap = group_scores[first] - group_scores[second]
        results.append({
            "scores": {k:round(val, 5) for k,val in group_scores.items()},
            "top": first, "runnerUp": second,
            "margin": round(gap, 5),
            "vote": first if gap >= _EVIDENCE_MARGIN else "uncertain",
        })
    return results


def _decision(observations: list[dict], duration_ms: int) -> dict:
    usable = [o for o in observations if isinstance(o.get("scores"), dict)]
    votes = Counter(o["vote"] for o in usable if o["vote"] != "uncertain")
    winner = sorted(ACTIVITIES, key=lambda c:(-votes[c],c))[0]
    margin = median([o["margin"] for o in usable
                     if o["vote"]==winner]) if votes[winner] else 0.
    n = len(usable)
    span = ((max(o["tMs"] for o in usable)-min(o["tMs"] for o in usable))/duration_ms
            if n>1 and duration_ms else 0.)
    support = votes[winner]
    competing = max([votes[x] for x in ACTIVITIES if x!=winner],default=0)
    # This is a *conservative engineering gate*, not a calibrated posterior.
    # Require five meaningful temporally distributed observations, >80% vote,
    # no competing sport votes, and a strong median relative cosine margin.
    strong = (n>=5 and duration_ms>=30_000 and span>=.6
              and support>=max(4, math.ceil(.80*n))
              and competing==0 and margin>=_STRONG_MEDIAN_MARGIN)
    status = ("strong_candidate" if strong and winner!="other"
              else "likely_other" if strong and winner=="other"
              else "ambiguous_needs_review")
    return {"status":status, "proposedActivity":winner if support else "unknown",
            "validSamples":n, "winnerVotes":support,
            "otherStrongVotes":competing,
            "medianWinningMargin":round(margin,5),
            "spanFraction":round(span,4),
            "relativeScoresNotProbabilities":True,
            "readyForAutoApply":False,  # always requires separate explicit command
            "limitations":["Single-image text similarity is uncalibrated",
                           "Strong consensus can still be wrong",
                           "Blue pixels do not prove snorkeling",
                           "This is a whole-recording label, not segment-level segmentation"]}


class SiglipActivityClassifier:
    def __init__(self, *, allow_model_download: bool = False):
        try:
            import torch
            from transformers import AutoModel, AutoProcessor
        except ImportError as exc:
            raise RuntimeError("Install dependencies with pip install -e '.[semantic,images]'") from exc
        self.torch=torch
        try:
            self.processor=AutoProcessor.from_pretrained(
                MODEL_ID,local_files_only=not allow_model_download)
            self.model=AutoModel.from_pretrained(
                MODEL_ID,local_files_only=not allow_model_download)
        except Exception as exc:
            raise RuntimeError("Matching SigLIP2 weights unavailable; use --allow-model-download "
                               "for explicit model setup") from exc
        self.model.eval()
        self.device=("mps" if torch.backends.mps.is_available()
                     else "cuda" if torch.cuda.is_available() else "cpu")
        self.model.to(self.device)
        self.revision=getattr(self.model.config,"_commit_hash",None)
        if not self.revision:
            raise RuntimeError("Cannot verify SigLIP2 model revision")
        self.processor_config=self.processor.image_processor.to_dict()
        self.text_vectors={}
        with torch.inference_mode():
            for activity,prompts in PROMPTS.items():
                inputs=self.processor(text=prompts,padding="max_length",
                                      truncation=True,return_tensors="pt").to(self.device)
                vec=self.model.get_text_features(**inputs)
                if hasattr(vec,"pooler_output"):
                    vec=vec.pooler_output
                vec=torch.nn.functional.normalize(vec,dim=-1)
                self.text_vectors[activity]=vec.cpu().float().tolist()

    def image_vectors(self, images: list[bytes]) -> list[list[float]]:
        if not images:
            return []
        from PIL import Image
        vectors=[]
        with self.torch.inference_mode():
            for start in range(0,len(images),4):
                opened=[Image.open(BytesIO(x)).convert("RGB")
                        for x in images[start:start+4]]
                inputs=self.processor(images=opened,return_tensors="pt").to(self.device)
                vec=self.model.get_image_features(**inputs)
                if hasattr(vec,"pooler_output"):
                    vec=vec.pooler_output
                vec=self.torch.nn.functional.normalize(vec,dim=-1)
                vectors.extend(vec.cpu().float().tolist())
        return vectors


def _verified_cached_embeddings(db,workspace,aid,model:SiglipActivityClassifier,
                                count:int) -> list[tuple[int,list[float]]]:
    """Use checksum-verified completed semantic cache only on exact model match."""
    row=db.execute("""SELECT j.* FROM semantic_jobs j JOIN semantic_active a
       ON j.asset_id=a.asset_id AND j.config_id=a.config_id
       WHERE a.asset_id=? AND a.provider='siglip2' AND j.status='completed'""",
       (aid,)).fetchone()
    if row is None:
        return []
    doc=completed_document(workspace,row)
    if doc is None or doc.get("provider")!="siglip2" or doc.get("modelVersion")!=MODEL_ID:
        return []
    conf=doc.get("config",{}).get("providerConfig",{})
    if (conf.get("resolvedRevision")!=model.revision
            or conf.get("imageProcessor")!=model.processor_config):
        return []
    chosen={}
    for frame in doc.get("frames",[]):
        t=frame.get("tMs")
        v=unit_vector(frame.get("embedding"))
        if type(t) is int and t>=0 and v:
            chosen[t]=v
    times=sorted(chosen)
    if len(times)<count:
        return []
    indices=sorted(set(round(i*(len(times)-1)/(count-1)) for i in range(count)))
    return [(times[i],chosen[times[i]]) for i in indices]


def _source_media(row,db):
    from .sources import resolve_source
    return resolve_source(db,row)  # one full SHA-256 check per asset


def _thumbnail(png: bytes) -> bytes:
    from PIL import Image
    image=Image.open(BytesIO(png)).convert("RGB")
    image.thumbnail((384,240))
    output=BytesIO()
    image.save(output,format="JPEG",quality=77,optimize=True)
    return output.getvalue()


def _run_config(frames:int) -> dict:
    return {"version":VERSION,"modelId":MODEL_ID,"prompts":PROMPTS,
            "sampleCount":frames,"width":384,"requestedTimePolicy":"06_to_94_pct_v1",
            "voteMargin":_EVIDENCE_MARGIN,"strongMedianMargin":_STRONG_MEDIAN_MARGIN,
            "minimumDurationMs":30000}


def _load_records(path:Path, config_hash:str) -> dict:
    records={}
    if path.exists():
        for number,line in enumerate(path.read_text(encoding="utf-8").splitlines(),1):
            if not line.strip():
                continue
            try:
                envelope=json.loads(line)
                item=envelope["record"]
                if (envelope.get("sha256")!=_hash(item)
                        or item.get("configHash")!=config_hash
                        or len(item.get("assetId",""))!=64):
                    raise ValueError("Corrupt or incompatible checkpoint")
                records[item["assetId"]]=item
            except (ValueError,TypeError,KeyError) as exc:
                raise ValueError(f"Corrupt activity checkpoint line {number}: {exc}") from exc
    return records


def _safe_dir(workspace:Path,target:Path) -> None:
    if (not target.is_relative_to(workspace) or
            not target.relative_to(workspace).parts or
            target.relative_to(workspace).parts[0] not in {"reports","exports"}):
        raise ValueError("Activity report directory must be under workspace reports/ or exports/")
    with connect(workspace) as db:
        # protect_source_output guards file paths, not existing directories.
        # Validate prospective files so this remains resumable and cannot
        # write inside registered original media directories.
        protect_source_output(db,target/"config.json")
        protect_source_output(db,target/"records.jsonl")


def _review_html(records:list[dict]) -> str:
    cards=[]
    for item in sorted(records,key=lambda x:(
            x["decision"]["status"]!="strong_candidate",
            -x["decision"]["medianWinningMargin"],x["assetId"])):
        decision=item["decision"]
        proposed=escape(decision["proposedActivity"])
        basename=escape(item.get("basename",""))
        aid=escape(item["assetId"])
        thumbnail=f'thumbnails/{aid}.jpg'
        cards.append(
            f'<article><img loading="lazy" src="{escape(thumbnail,quote=True)}" '
            'alt="Sparse video sample or no available thumbnail">'
            f'<div><strong>{basename}</strong><p>{proposed} · '
            f'{escape(decision["status"])} · {decision["winnerVotes"]}/'
            f'{decision["validSamples"]} votes · median margin '
            f'{decision["medianWinningMargin"]:.3f}</p>'
            f'<small>SHA-256 {aid[:16]} · uncalibrated proposal</small></div></article>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>Klip activity review</title><style>'
            'body{font:15px system-ui;margin:24px;background:#101820;color:#f0f3f6}'
            'h1{font-size:1.6rem}p{color:#c5cfd7}main{display:grid;gap:12px;'
            'grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}'
            'article{background:#1a2b36;border-radius:12px;overflow:hidden}'
            'img{width:100%;height:170px;object-fit:contain;background:#0d161d}'
            'article div{padding:12px}small{color:#99abba}'
            '</style></head><body><h1>Activity classification review</h1>'
            '<p>These are uncalibrated video-level model proposals. Blue pixels '
            'are never sufficient to classify snorkeling. Verify before promoting '
            'activity labels; ambiguous clips remain unknown.</p><main>'
            + ''.join(cards) + '</main></body></html>')


def run_activity_scan(workspace:Path,output_dir:Path,*,frames:int=5,
                      limit:int=0,allow_model_download:bool=False) -> dict:
    """Checkpoints each source; rerun unchanged command to resume safely."""
    if type(frames) is not int or not 5<=frames<=9:
        raise ValueError("For robust classification use 5..9 sparse frames")
    if type(limit) is not int or limit<0:
        raise ValueError("--limit must be >=0 (0 means all assets)")
    workspace=Path(workspace).expanduser().resolve()
    target=Path(output_dir).expanduser().resolve()
    _safe_dir(workspace,target)
    config=_run_config(frames)
    config_hash=_hash(config)
    if target.exists():
        if not target.is_dir() or (target/"config.json").read_text().strip()!=_canonical(config):
            raise ValueError("Existing report directory has a different classification config")
    else:
        target.mkdir(parents=True,mode=0o700)
        (target/"config.json").write_text(_canonical(config),encoding="utf-8")
    (target/"thumbnails").mkdir(exist_ok=True)
    records=_load_records(target/"records.jsonl",config_hash)
    with connect(workspace) as db:
        assets=[dict(r) for r in db.execute(
            "SELECT * FROM assets ORDER BY created_at,id")]
    todo=[a for a in assets if a["id"] not in records]
    if limit:
        todo=todo[:limit]
    model=None
    errors=[]
    processed=0
    # Abort immediately if the required semantic model cannot be loaded.
    # Per-video errors should be recoverable, but a missing model is global.
    if any(a.get("duration") and a["duration"]>0 for a in todo):
        model=SiglipActivityClassifier(allow_model_download=allow_model_download)
    with (target/"records.jsonl").open("a",encoding="utf-8") as checkpoint:
        for i,asset in enumerate(todo,1):
            aid=asset["id"]
            duration=asset["duration"]
            if not duration or duration<=0:
                errors.append({"assetId":aid,"reason":"invalid_duration"})
                continue
            try:
                source=None
                with connect(workspace) as db:
                    cached=_verified_cached_embeddings(db,workspace,aid,model,frames)
                    source=_source_media(asset,db)  # checked before using any cache
                source_stat=source.stat()
                observations=[]
                preview=None
                if cached:
                    tms=[t for t,_ in cached]
                    vectors=[v for _,v in cached]
                    source_kind="verified_cached_siglip2"
                else:
                    tms=_sample_times(int(duration*1000),frames)
                    raw=[]
                    for t in tms:
                        png,timing=extract_frame_png(source,t,width=384)
                        if timing.get("timestampBasis")!="decoded_frame_pts":
                            raise ValueError("No actual decoded PTS for sparse classification")
                        raw.append(png)
                    vectors=model.image_vectors(raw)
                    preview=_thumbnail(raw[len(raw)//2])
                    source_kind="verified_sparse_decodes"
                for t,scores in zip(tms,_frame_scores(model.text_vectors,vectors)):
                    observations.append({"tMs":t,**scores})
                if len(observations)!=len(tms):
                    raise ValueError("Semantic encoder did not return one vector per frame")
                end_stat=source.stat()
                fields=("st_dev","st_ino","st_size","st_mtime_ns","st_ctime_ns")
                if any(getattr(source_stat,k)!=getattr(end_stat,k) for k in fields):
                    raise OSError("Video changed during sampling")
                decision=_decision(observations,int(duration*1000))
                record={
                    "assetId":aid,"basename":Path(asset["stored_path"]).name,
                    "durationMs":int(duration*1000),
                    "previousActivity":asset["activity"],
                    "configHash":config_hash,
                    "modelRevision":model.revision,
                    "processorHash":_hash(model.processor_config),
                    "evidenceSource":source_kind,
                    "sourceVerifiedSha256":aid,
                    "sourceStat":{k:getattr(end_stat,k) for k in fields},
                    "observations":observations,"decision":decision,
                    "thumbnailBlueCue":(_blue_cue(raw[len(raw)//2])
                                        if not cached else None),
                }
                envelope={"sha256":_hash(record),"record":record}
                checkpoint.write(_canonical(envelope)+"\n")
                checkpoint.flush()
                os.fsync(checkpoint.fileno())
                records[aid]=record
                processed+=1
                if preview:
                    (target/"thumbnails"/f"{aid}.jpg").write_bytes(preview)
                print(f"[activity] {i}/{len(todo)} {aid[:12]} "
                      f"{decision['proposedActivity']}: {decision['status']} "
                      f"({decision['winnerVotes']}/{decision['validSamples']})",
                      flush=True, file=sys.stderr)
            except Exception as exc:
                errors.append({"assetId":aid,"reason":str(exc)[:240]})
                print(f"[activity] {i}/{len(todo)} {aid[:12]} error: {exc}",
                      flush=True, file=sys.stderr)
    counts=Counter((r["decision"]["proposedActivity"],r["decision"]["status"])
                   for r in records.values())
    report={
        "schemaVersion":1,"version":VERSION,"configHash":config_hash,
        "workspaceCatalogAssetCount":len(assets),
        "checkpointedAssets":len(records),"processedThisRun":processed,
        "errorsThisRun":errors,"summaries":[
            {"activity":a,"status":s,"count":n}
            for (a,s),n in sorted(counts.items())],
        "modelRevision":model.revision if model else None,
        "limitations":["Not validated on your DJI recordings",
                       "Uncalibrated zero-shot model agreement is not ground truth",
                       "Color fraction is review metadata only",
                       "Per-video activity may hide multi-activity sections",
                       "Missing/changed media are not classified"],
    }
    tmp=target/"summary.tmp"
    tmp.write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+"\n")
    tmp.replace(target/"summary.json")
    tmp=target/"review.tmp"
    tmp.write_text(_review_html(list(records.values())),encoding="utf-8")
    tmp.replace(target/"review.html")
    return {"report":str(target/"summary.json"),"reviewHtml":str(target/"review.html"),
            "checkpoint":str(target/"records.jsonl"),"processedThisRun":processed,
            "totalCompleted":len(records),"errorsThisRun":len(errors),"summaries":report["summaries"]}

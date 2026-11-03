"""Targeted, conservative activity refinement for low-contrast sandy ocean footage.

Never modify the first-pass JSONL or catalog. Optional low-cost thumbnail mode
can revisit first-pass imagery without reading the source media; sparse mode
re-decodes only unresolved assets with SHA verification and PTS evidence.
All revised scene labels are uncalibrated review proposals, never autopromoted.
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

from .activity_intelligence import (
    MODEL_ID, SiglipActivityClassifier, _canonical, _hash, _load_records,
    _safe_dir, _sample_times, _thumbnail,
)
from .catalog import connect
from .frame_timing import extract_frame_png
from .sources import resolve_source

VERSION = "activity-sandbar-refinement-v1"

# Comparison prompts intentionally describe WHITE sand, submerged reef,
# above-water kayaking, surface activities, snow and dirt separately.
# Relative cosine scores are never converted into probabilities.
PROMPTS = {
    "underwater": [
        "Underwater action camera looking down at pale white sand on a shallow tropical sea floor.",
        "First person snorkeling underwater above a bright white sandbar with rippled sunlight on sand.",
        "Underwater video looking at a stingray or manta ray swimming over a sandy ocean bottom.",
        "Underwater camera exploring a coral reef and schools of fish in the ocean.",
    ],
    "snorkel_surface": [
        "A swimmer snorkeling facedown looking into the water at a shallow coral reef.",
        "First person swimming in the sea with a half submerged camera, waves and ocean floor visible.",
        "An action camera held by a person floating in the ocean snorkeling above shallow sand.",
        "POV swimming through clear shallow tropical water with a submerged camera.",
    ],
    "kayak_boat": [
        "View from sitting in a blue kayak, feet visible in the bow above the ocean surface.",
        "POV paddling a kayak on the sea with the sky, coastline and kayak bow visible.",
        "View above the water from the bow of a boat with blue sky and sea.",
        "A person riding a paddleboard or small boat at the water surface.",
    ],
    "beach_wading": [
        "A person standing on a tropical sandbar in shallow water looking at their feet.",
        "Camera filming a white sand beach and turquoise water from dry land.",
        "POV walking into the shallow ocean from a white sandy beach.",
        "Sunlight and waves from the shore of a sandy beach, camera above water.",
    ],
    "ski": [
        "POV skiing on a snowy mountain with skis and snow-covered trees visible.",
        "Riding a ski chairlift over groomed winter mountain slopes.",
        "Skiing down a steep white snow-covered hill with blue sky.",
        "Standing with skis in a snowy winter mountain resort.",
    ],
    "bike": [
        "POV mountain biking down a forest dirt trail with bike handlebars.",
        "Riding a mountain bike through rocky desert singletrack.",
        "POV cycling on a wooded trail with tree roots and berms.",
        "Bike wheels and handlebars on a forest trail.",
    ],
    "other": [
        "Walking on a road through a town or city.",
        "Inside a vehicle looking through a windshield.",
        "Indoor action camera footage of a room and people.",
        "Flying above the landscape through clouds and sky.",
    ],
}
WATER_REVIEW = {"underwater", "snorkel_surface"}
NON_SNORKEL = {"kayak_boat", "beach_wading", "ski", "bike", "other"}


class RefineModel:
    def __init__(self, *, allow_model_download=False):
        base=SiglipActivityClassifier(allow_model_download=allow_model_download)
        self.base=base
        self.revision=base.revision
        self.processor_hash=_hash(base.processor_config)
        self.text_vectors={}
        torch=base.torch
        with torch.inference_mode():
            for name,prompts in PROMPTS.items():
                data=base.processor(
                    text=prompts,padding="max_length",truncation=True,
                    return_tensors="pt").to(base.device)
                vec=base.model.get_text_features(**data)
                if hasattr(vec,"pooler_output"):
                    vec=vec.pooler_output
                vec=torch.nn.functional.normalize(vec,dim=-1)
                self.text_vectors[name]=vec.cpu().float().tolist()

    def image_vectors(self, images):
        return self.base.image_vectors(images)


def _scores(vector, banks: dict[str,list[list[float]]]) -> dict:
    from .scene_atlas import unit_vector
    v=unit_vector(vector)
    if v is None:
        raise ValueError("Degenerate image embedding")
    scores={}
    for group,reference in banks.items():
        if not reference or any(len(v)!=len(ref) for ref in reference):
            raise ValueError("Image/text embedding dimensional mismatch")
        scores[group]=max(sum(a*b for a,b in zip(v,ref)) for ref in reference)
    names=sorted(scores,key=lambda k:(-scores[k],k))
    top,runner=names[:2]
    gap=scores[top]-scores[runner]
    # Small gaps must be reported as uncertain, not a fabricated 0% probability.
    vote=top if gap>=.008 else "uncertain"
    return {"vote":vote,"top":top,"runnerUp":runner,"relativeMargin":round(gap,6),
            "cosineByScene":{k:round(v,6) for k,v in sorted(scores.items())}}


def _summarize(observations):
    votes=Counter(x["vote"] for x in observations)
    n=len(observations)
    underwater=votes["underwater"]
    water=votes["underwater"]+votes["snorkel_surface"]
    surface_non_snorkel=votes["kayak_boat"]+votes["beach_wading"]
    land=votes["ski"]+votes["bike"]+votes["other"]
    if n<3:
        status="insufficient_evidence"
    elif underwater>=max(2,math.ceil(n*.6)) and land==0:
        status="underwater_candidate_needs_review"
    elif water>=max(2,math.ceil(n*.6)) and land==0 and surface_non_snorkel<=1:
        status="possible_snorkeling_needs_review"
    elif surface_non_snorkel>=max(2,math.ceil(n*.6)):
        status="surface_water_activity_needs_review"
    elif land>=max(2,math.ceil(n*.6)):
        status="possible_non_snorkeling_needs_review"
    else:
        status="ambiguous_needs_review"
    return {"status":status,"validFrames":n,"votes":dict(sorted(votes.items())),
            "underwaterVotes":underwater,"waterVotes":water,
            "surfaceNonSnorkelingVotes":surface_non_snorkel,
            "landVotes":land,"readyForCatalogApply":False,
            "confidence":None,"confidenceCalibrated":False,
            "reason":"new prompt bank proposes visual scene states; review required"}


def _candidates(original_records:dict,summary:dict,*,include_snorkeling_ambiguous:bool,
                include_other_ambiguous:bool,include_failed:bool):
    chosen={}
    for aid,r in original_records.items():
        dec=r["decision"]
        status=dec["status"]
        activity=dec["proposedActivity"]
        reason=None
        if activity=="unknown":
            reason="no_decisive_first_pass_votes"
        elif status=="ambiguous_needs_review" and activity=="snorkeling" and include_snorkeling_ambiguous:
            reason="ambiguous_snorkeling"
        elif status=="ambiguous_needs_review" and activity=="other" and include_other_ambiguous:
            reason="ambiguous_other"
        if reason:
            chosen[aid]={"assetId":aid,"reason":reason,"original":r}
    if include_failed:
        for row in summary.get("errorsThisRun",[]):
            aid=row.get("assetId") if isinstance(row,dict) else None
            if isinstance(aid,str) and len(aid)==64 and aid not in chosen:
                chosen[aid]={"assetId":aid,"reason":"first_pass_decode_failed","original":None}
    # Prioritize unresolved underwater scenes, then broken previous decodes.
    order={"no_decisive_first_pass_votes":0,"first_pass_decode_failed":1,
           "ambiguous_snorkeling":2,"ambiguous_other":3}
    return sorted(chosen.values(),key=lambda x:(order[x["reason"]],x["assetId"]))


def _review_html(records:list[dict]):
    entries=[]
    for r in sorted(records,key=lambda x:(x["decision"]["status"],x["basename"])):
        images=r["images"]
        photos="".join(
            f'<img loading="lazy" alt="Sampled frame, visual review required" '
            f'src="{escape(p,quote=True)}">' for p in images)
        badges=" ".join(f"{escape(str(k))}:{v}" for k,v in r["decision"]["votes"].items())
        entries.append(
            '<article><div class="photos">'+photos+'</div><div class="copy">'
            f'<strong>{escape(r["basename"])}</strong>'
            f'<p>{escape(r["decision"]["status"])} · {r["decision"]["validFrames"]}'
            ' sampled frame(s)</p>'
            f'<small>{badges} · {escape(r["reason"])} · '
            f'SHA {escape(r["assetId"][:16])}</small></div></article>')
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Klip · Shallow water refinement</title><style>'
        'body{font:14px system-ui;margin:25px;background:#101820;color:#e6eff4}'
        'header{max-width:850px;margin-bottom:24px;line-height:1.5}'
        'h1{font-size:1.5rem}p{color:#bacbd4}'
        'main{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px}'
        'article{background:#1b2b35;border:1px solid #324451;border-radius:12px;overflow:hidden}'
        '.photos{display:flex;overflow-x:auto;background:#0b161d}'
        'img{width:33.33%;height:160px;min-width:100px;object-fit:contain}'
        '.copy{padding:13px}small{color:#9cb1bd}</style></head>'
        '<body><header><h1>Shallow-water classification review</h1>'
        '<p>Uncalibrated scene proposals only. Sandy seabeds are not necessarily '
        'visible blue, and blue water alone is not evidence of snorkeling. '
        'Distinguish underwater footage from kayaking, wading and beach scenes '
        'before changing an activity label. Original classifier results are '
        'preserved; no catalog changes were made.</p></header><main>'
        +''.join(entries)+'</main></body></html>')


def _read_original(input_dir:Path,workspace:Path):
    if not input_dir.is_relative_to(workspace):
        raise ValueError("First-pass report must be within workspace")
    config=json.loads((input_dir/"config.json").read_text())
    summary=json.loads((input_dir/"summary.json").read_text())
    if summary.get("configHash")!=_hash(config):
        raise ValueError("First-pass report config checksum mismatch")
    records=_load_records(input_dir/"records.jsonl",_hash(config))
    if len(records)!=summary["checkpointedAssets"]:
        raise ValueError("First-pass checkpoint count does not match summary")
    lock=json.loads((input_dir/"model-lock.json").read_text())
    if any(r.get("modelRevision")!=lock["modelRevision"]
           or r.get("processorHash")!=lock["processorHash"]
           for r in records.values()):
        raise ValueError("First-pass data contains incompatible model provenance")
    return summary,records,lock


def refine_activities(workspace:Path,input_dir:Path,output_dir:Path,*,
                      mode:str="sampled",frames:int=5,limit:int=0,
                      include_snorkeling_ambiguous:bool=False,
                      include_other_ambiguous:bool=False,
                      include_failed:bool=True,
                      allow_model_download:bool=False)->dict:
    if mode not in {"sampled","thumbnail"}:
        raise ValueError("Mode must be sampled or thumbnail")
    if type(frames) is not int or not 3<=frames<=7:
        raise ValueError("--frames must be 3..7")
    if type(limit) is not int or limit<0:
        raise ValueError("--limit must be nonnegative")
    workspace=Path(workspace).expanduser().resolve()
    input_dir=Path(input_dir).expanduser().resolve()
    target=Path(output_dir).expanduser().resolve()
    if target==input_dir or target.is_relative_to(input_dir):
        raise ValueError("Refinement output must not modify first-pass report")
    _safe_dir(workspace,target)
    summary,original,old_lock=_read_original(input_dir,workspace)
    settings={"version":VERSION,"prompts":PROMPTS,"modelId":MODEL_ID,
              "mode":mode,"frames":frames,
              "includeSnorkelingAmbiguous":include_snorkeling_ambiguous,
              "includeOtherAmbiguous":include_other_ambiguous,
              "includeFailed":include_failed,
              "firstPassConfigHash":summary["configHash"]}
    config_hash=_hash(settings)
    if target.exists():
        if not target.is_dir() or (target/"config.json").read_text().strip()!=_canonical(settings):
            raise ValueError("Refinement output exists with incompatible settings")
    else:
        target.mkdir(parents=True,mode=0o700)
        (target/"config.json").write_text(_canonical(settings)+"\n",encoding="utf-8")
    (target/"thumbnails").mkdir(exist_ok=True)
    checkpoint=target/"records.jsonl"
    old=_load_records(checkpoint,config_hash)
    tasks=[x for x in _candidates(original,summary,
        include_snorkeling_ambiguous=include_snorkeling_ambiguous,
        include_other_ambiguous=include_other_ambiguous,
        include_failed=include_failed) if x["assetId"] not in old]
    if limit:
        tasks=tasks[:limit]
    with connect(workspace) as db:
        assets={r["id"]:dict(r) for r in db.execute("SELECT * FROM assets")}
    model=None
    if tasks:
        model=RefineModel(allow_model_download=allow_model_download)
        if model.revision!=old_lock["modelRevision"] or model.processor_hash!=old_lock["processorHash"]:
            raise ValueError("Refinement model revision differs from first-pass model")
    failures=[]
    processed=0
    with checkpoint.open("a",encoding="utf-8") as out:
        for index,item in enumerate(tasks,1):
            aid=item["assetId"]
            asset=assets.get(aid)
            if (not asset or not asset["duration"] or asset["duration"]<=0
                    or asset["activity"]!="unknown"):
                failures.append({"assetId":aid,"reason":"catalog_asset_unavailable_or_already_labeled"})
                continue
            try:
                source_kind=mode
                images=[]
                times=[]
                source_pts=[]
                frame_errors=[]
                raw=[]
                if mode=="thumbnail":
                    file=input_dir/"thumbnails"/f"{aid}.jpg"
                    if item["original"] is None or not file.is_file():
                        raise ValueError("No source-verified first-pass thumbnail to reuse")
                    if file.stat().st_size>4_000_000:
                        raise ValueError("Oversized cached thumbnail")
                    raw=[file.read_bytes()]
                    times=[int(asset["duration"]*500)]
                    source_pts=[None]  # cached thumbnail has no fresh PTS proof
                    images=[f"thumbnails/{aid}-000.jpg"]
                    (target/images[0]).write_bytes(_thumbnail(raw[0]))
                    source_kind="first_pass_single_thumbnail_only"
                else:
                    with connect(workspace) as db:
                        source=resolve_source(db,asset)  # full content SHA before inference
                    start_stat=source.stat()
                    wanted=_sample_times(int(asset["duration"]*1000),frames)
                    for t in wanted:
                        try:
                            png,timing=extract_frame_png(source,t,width=384)
                            if (timing.get("timestampBasis")!="decoded_frame_pts"
                                    or type(timing.get("decodedPtsMs")) is not int):
                                raise ValueError("Frame lacks source PTS")
                            raw.append(png)
                            times.append(t)
                            source_pts.append(timing["decodedPtsMs"])
                        except (OSError,ValueError,RuntimeError) as exc:
                            frame_errors.append({"requestedMs":t,"reason":str(exc)[:120]})
                    if len(raw)<3:
                        raise RuntimeError(f"Only {len(raw)} decoded frames; "
                                           f"{len(frame_errors)} frame errors; abstaining")
                    after=source.stat()
                    fields=("st_dev","st_ino","st_size","st_mtime_ns","st_ctime_ns")
                    if any(getattr(after,f)!=getattr(start_stat,f) for f in fields):
                        raise RuntimeError("Source file changed during refinement")
                    # Retain three evenly spaced review images, not every decoded PNG.
                    idxs=sorted({0,(len(raw)-1)//2,len(raw)-1})
                    for t_index in idxs:
                        rel=f"thumbnails/{aid}-{times[t_index]:09d}.jpg"
                        (target/rel).write_bytes(_thumbnail(raw[t_index]))
                        images.append(rel)
                vectors=model.image_vectors(raw)
                if len(vectors)!=len(raw):
                    raise ValueError("Refinement encoder returned wrong frame count")
                observations=[{"requestedMs":t,"sourceDecodedPtsMs":pts,
                               **_scores(v,model.text_vectors)}
                              for t,pts,v in zip(times,source_pts,vectors)]
                decision=_summarize(observations)
                record={"configHash":config_hash,"assetId":aid,
                        "basename":Path(asset["stored_path"]).name,
                        "reason":item["reason"],"evidenceSource":source_kind,
                        "sourceAssetSha256":aid,
                        "modelRevision":model.revision,
                        "processorHash":model.processor_hash,
                        "firstPassStatus":(item["original"] or {}).get("decision",{}).get("status"),
                        "observations":observations,"decision":decision,"images":images,
                        "frameDecodeErrors":frame_errors,
                        "sourceMediaRechecked":mode=="sampled",
                        "limitations":["Zero-shot cosine is not calibrated",
                                       "Snorkeling requires human verification",
                                       "Proposals do not modify catalog labels"]}
                out.write(_canonical({"record":record,"sha256":_hash(record)})+"\n")
                out.flush()
                os.fsync(out.fileno())
                old[aid]=record
                processed+=1
                print(f"[refine] {index}/{len(tasks)} {aid[:12]} "
                      f"{decision['status']} ({decision['waterVotes']}/{decision['validFrames']} water votes)",
                      file=sys.stderr,flush=True)
            except (OSError,ValueError,RuntimeError) as exc:
                failures.append({"assetId":aid,"reason":str(exc)[:220]})
                print(f"[refine] {index}/{len(tasks)} {aid[:12]} error: {exc}",
                      file=sys.stderr,flush=True)
    counts=Counter(x["decision"]["status"] for x in old.values())
    report={"version":VERSION,"schemaVersion":1,
            "firstPassDirectory":str(input_dir),
            "firstPassAssetCount":summary["checkpointedAssets"],
            "firstPassFailures":len(summary.get("errorsThisRun",[])),
            "newlyProcessed":processed,"checkpointed":len(old),
            "errorsThisRun":failures,"statusCounts":dict(sorted(counts.items())),
            "catalogActivityLabelsChanged":False,
            "limitations":["No catalog labels are changed",
                           "Thumbnail-only uses one previously verified image, not new source verification",
                           "Sparse mode rehashes selected source recordings once",
                           "Relative scene similarities require manual validation",
                           "No media is uploaded or modified"]}
    (target/"summary.json").write_text(json.dumps(report,indent=2,sort_keys=True)+"\n")
    (target/"review.html").write_text(_review_html(list(old.values())),encoding="utf-8")
    return {"processedThisRun":processed,"checkpointed":len(old),
            "errorsThisRun":len(failures),"summary":str(target/"summary.json"),
            "review":str(target/"review.html"),"statusCounts":report["statusCounts"],
            "catalogActivityLabelsChanged":False}

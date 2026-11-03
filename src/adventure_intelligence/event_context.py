"""Opt-in, bounded before/during/after visual embedding packets for candidate moments.

Candidate generation is cached-only and scientifically unverified. Exactly
three small source images per proposal (subject to edge cases) are decoded
with independently recorded source PTS. No source modifications, external
frame uploads, species labels, performance measurements or auto-export.
"""
from __future__ import annotations

from hashlib import sha256
from html import escape
from pathlib import Path
import json
import math

from .activity_intelligence import SiglipActivityClassifier, _hash, _thumbnail
from .catalog import connect
from .ecology_encounters import encounter_queue
from .frame_timing import extract_frame_png
from .safety import protect_source_output
from .scene_atlas import unit_vector, similarity
from .sources import resolve_source, verified_digest

VERSION="event-context-embedding-v1"


def _context_windows(proposals:list[dict],duration_ms:int,*,max_events:int)->list[dict]:
    """Collapse overlapping *review windows*, not biological individuals."""
    by_group={}
    for p in proposals:
        a,b=p.get("startMs"),p.get("endMs")
        if (type(a) is not int or type(b) is not int
                or not 0<=a<b<=duration_ms):
            continue
        group=p.get("biologicalGroup")
        if not isinstance(group,str) or not group:
            continue
        by_group.setdefault(group,[]).append(p)
    merged=[]
    for group,items in sorted(by_group.items()):
        items.sort(key=lambda p:(p["startMs"],p["endMs"],p["id"]))
        for p in items:
            if (merged and merged[-1]["group"]==group
                    and p["startMs"]<=merged[-1]["endMs"]):
                m=merged[-1]
                m["startMs"]=min(m["startMs"],p["startMs"])
                m["endMs"]=max(m["endMs"],p["endMs"])
                m["sourceProposalIds"].append(p["id"])
                m["providerNames"].add(p["provider"])
                m["reviewPriority"]=max(m["reviewPriority"],p["reviewPriority"])
                m["rawCandidates"].append(p)
            else:
                merged.append({
                    "group":group,"startMs":p["startMs"],"endMs":p["endMs"],
                    "sourceProposalIds":[p["id"]],
                    "providerNames":{p["provider"]},
                    "reviewPriority":p["reviewPriority"],
                    "rawCandidates":[p],
                })
    merged.sort(key=lambda m:(-m["reviewPriority"],m["startMs"],m["group"]))
    # A single review card may cover several overlapping objects. This
    # asserts NEITHER one animal nor several unique animals.
    return merged[:max_events]


def _phase_times(candidate:dict,duration_ms:int)->dict:
    times=sorted({t for p in candidate["rawCandidates"]
                  for t in p.get("evidenceRequestedTimesMs",[])
                  if type(t) is int and 0<=t<duration_ms})
    if not times:
        return {}
    first,last=times[0],times[-1]
    middle=times[len(times)//2]
    targets={
        "before":max(0,first-2200),
        "during":middle,
        "after":min(duration_ms-1,last+2200),
    }
    # A phase with the same frame cannot create independent temporal proof.
    seen=set()
    clean={}
    for phase in ("before","during","after"):
        time=targets[phase]
        if time in seen or (phase=="before" and time>=middle) or \
                (phase=="after" and time<=middle):
            continue
        clean[phase]=time
        seen.add(time)
    return clean


def _phase_pooled(phase_vectors:dict)->dict:
    """Two separately interpretable outputs: central event and optional context."""
    valid={k:unit_vector(v) for k,v in phase_vectors.items()}
    valid={k:v for k,v in valid.items() if v is not None}
    if "during" not in valid:
        return {"available":False,"reason":"central_event_image_missing"}
    center=valid["during"]
    weights={"before":.25,"during":.5,"after":.25}
    total=sum(weights[k] for k in valid)
    combined=unit_vector([sum(weights[k]*v[i] for k,v in valid.items())/total
                          for i in range(len(center))])
    if combined is None:
        return {"available":False,"reason":"degenerate_pooled_vector"}
    changes={f"{phase}ToEventCosineSimilarity":round(similarity(center,valid[phase]),6)
             for phase in ("before","after") if phase in valid}
    return {
        "available":True,
        "method":"normalized_weighted_phase_pool_v1",
        "eventVector":center,"contextVector":combined,
        "phaseWeightsApplied":{k:weights[k]/total for k in valid},
        "visualChangeOnlyNotActionProof":changes,
        "interpretation":"unverified image similarity, not a measure of achievement or species",
    }


def _contact_sheet(cards:list[dict],*,asset_id:str)->str:
    entries=[]
    for c in cards:
        photos=[]
        for phase in c["phases"]:
            name=escape(phase["image"],quote=True)
            photos.append(
                f'<figure><img loading="lazy" src="{name}" alt="Candidate {phase["phase"]}">'
                f'<figcaption>{escape(phase["phase"])} · '
                f'{phase["requestedMs"]/1000:.1f}s</figcaption></figure>')
        entries.append(
            '<article><h2>'+escape(c["group"])+
            f' · {c["startMs"]/1000:.1f}–{c["endMs"]/1000:.1f}s</h2>'
            '<p>Unverified broad object detection. Inspect the complete '
            'source clip before recording the animal, activity or highlight value.</p>'
            '<div class="photos">'+''.join(photos)+'</div>'
            f'<small>{len(c["sourceProposalIds"])} overlapping detector proposal(s) '
            'grouped for review, NOT counted as individual animals.</small></article>')
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Klip · Before, During, After</title><style>'
            'body{font:14px system-ui;background:#101923;color:#eaf0f4;'
            'max-width:1150px;margin:30px auto;padding:0 18px}'
            'h1{font-size:1.7rem}p,small{color:#b1c2cd;line-height:1.5}'
            'article{background:#1b2b37;border:1px solid #34475a;'
            'border-radius:12px;padding:18px;margin-bottom:18px}'
            'h2{font-size:1.1rem;color:#f0bdb2}'
            '.photos{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));'
            'gap:12px}.photos figure{margin:0;background:#0d1820;border-radius:8px;'
            'overflow:hidden}.photos img{height:180px;object-fit:contain;'
            'width:100%}.photos figcaption{padding:8px;color:#b1c2cd}'
            '@media(max-width:650px){.photos{grid-template-columns:1fr}}'
            '</style></head><body><h1>Event context evidence</h1>'
            '<p>All images are local. Before / during / after embeddings measure '
            'visual changes only. They cannot prove performance, identify '
            'a species, or establish unique animal counts.</p><main>'
            +''.join(entries)+'</main></body></html>')


def build_event_context(workspace:Path,asset_id:str,output_dir:Path,*,
                        max_events:int=4,allow_model_download:bool=False)->dict:
    if (not isinstance(asset_id,str) or len(asset_id)!=64 or
            any(c not in "0123456789abcdef" for c in asset_id)):
        raise ValueError("Full lowercase SHA-256 source asset required")
    if type(max_events) is not int or not 1<=max_events<=6:
        raise ValueError("max-events must be 1..6")
    workspace=Path(workspace).expanduser().resolve()
    target=Path(output_dir).expanduser().resolve()
    if (not target.is_relative_to(workspace) or target==workspace or
            target.relative_to(workspace).parts[0] not in ("reports","exports") or
            target.exists()):
        raise ValueError("Output must be a new private reports/ or exports/ directory")
    with connect(workspace) as db:
        protect_source_output(db,target/"manifest.json")
        row=db.execute("SELECT * FROM assets WHERE id=?",(asset_id,)).fetchone()
        if row is None or row["activity"]!="snorkeling" or \
                not isinstance(row["duration"],(int,float)) or row["duration"]<=0:
            raise ValueError("An indexed confirmed snorkeling asset with duration is required")
        duration=round(row["duration"]*1000)
        queue=encounter_queue(db,workspace,asset_id,duration,
                              max_results=100,max_gap_ms=5000)
        selected=_context_windows(queue["candidateWindows"],duration,
                                  max_events=max_events)
        if not selected:
            return {
                "status":"abstained_no_cached_marine_candidate_events",
                "assetId":asset_id,"newFrameDecodes":0,
                "newEmbeddingCalls":0,"outputWritten":False,
                "note":"Recover or complete your cached marine OWLv2 evidence first.",
            }
        source=resolve_source(db,row)
    model=SiglipActivityClassifier(allow_model_download=allow_model_download)
    stat_before=source.stat()
    target.parent.mkdir(parents=True,exist_ok=True)
    target.mkdir(mode=0o700)
    (target/"images").mkdir()
    cached={}
    cards=[]
    errors=[]
    for index,event in enumerate(selected,1):
        targets=_phase_times(event,duration)
        if "during" not in targets:
            errors.append({"group":event["group"],"reason":"No central detector timestamp"})
            continue
        sample=[]
        for phase,t in targets.items():
            try:
                if t not in cached:
                    png,timing=extract_frame_png(source,t,width=384)
                    if (timing.get("timestampBasis")!="decoded_frame_pts"
                            or type(timing.get("decodedPtsMs")) is not int):
                        raise ValueError("Decoded frame has no source PTS")
                    cached[t]=(png,timing)
                png,timing=cached[t]
                # A central event must be identical to its original model
                # evidence if the detector preserved source PTS.
                if phase=="during":
                    source_pts={e.get("sourceDecodedPtsMs") for p in event["rawCandidates"]
                                for e in p.get("sampledBoxEvidence",[])
                                if e.get("requestedMs")==t
                                and type(e.get("sourceDecodedPtsMs")) is int}
                    if source_pts and all(abs(timing["decodedPtsMs"]-v)>2
                                          for v in source_pts):
                        raise ValueError("Central source PTS differs from detector frame")
                sample.append((phase,t,png,timing))
            except (OSError,ValueError,RuntimeError) as exc:
                errors.append({"group":event["group"],"phase":phase,
                               "requestedMs":t,"reason":str(exc)[:170]})
        if not any(x[0]=="during" for x in sample):
            continue
        embeddings=model.image_vectors([x[2] for x in sample])
        if len(embeddings)!=len(sample):
            raise ValueError("Context model returned a different number of embeddings")
        phase_vectors={}
        phases=[]
        for phase,t,png,timing,vec in [
                (*info,vector) for info,vector in zip(sample,embeddings)]:
            normalized=unit_vector(vec)
            if normalized is None:
                raise ValueError("Invalid phase embedding")
            image=f"images/event-{index:02d}-{phase}.jpg"
            data=_thumbnail(png)
            (target/image).write_bytes(data)
            phases.append({
                "phase":phase,"requestedMs":t,
                "decodedSourcePtsMs":timing["decodedPtsMs"],
                "image":image,"thumbnailSha256":sha256(data).hexdigest(),
                "embedding":normalized,
                "embeddingL2Normalized":True,
                "status":"unverified_video_sample",
            })
            phase_vectors[phase]=normalized
        representation=_phase_pooled(phase_vectors)
        cards.append({
            "id":f"event-context-{index:02d}",
            "group":event["group"],
            "startMs":event["startMs"],"endMs":event["endMs"],
            "sourceProposalIds":event["sourceProposalIds"],
            "detectorProviders":sorted(event["providerNames"]),
            "sourceModelEvidenceOnly":True,"speciesVerified":False,
            "animalCount":None,"actionConfirmed":False,
            "contextEmbedding":representation,
            "phases":phases,
            "reviewQuestions":[
                "What actually happens in the entire source window?",
                "Is the subject visible and framed clearly throughout the event?",
                "What changes between approach, encounter and aftermath?",
                "Would this clip be meaningful to revisit or show another person?",
            ],
        })
    if verified_digest(source)!=asset_id:
        for p in (target/"images").glob("*.jpg"):
            p.unlink()
        raise RuntimeError("Original source changed; context packet cannot be trusted")
    if not cards:
        status="abstained_no_complete_central_event_frames"
    else:
        status="unverified_context_cards_for_review"
    manifest={
        "schemaVersion":1,"version":VERSION,
        "status":status,"assetId":asset_id,
        "sourceIdentityVerifiedBeforeAndAfter":True,
        "modelId":"google/siglip2-base-patch16-224",
        "modelRevision":model.revision,
        "processorHash":_hash(model.processor_config),
        "requestedMaximumEvents":max_events,
        "decodedFrameCount":len(cached),
        "events":cards,"errors":errors,
        "sourceReviewStatus":"unverified",
        "limitations":[
            "Embedding comparisons are relative image similarities, not semantic event verification.",
            "A camera movement or lighting change can produce large embedding differences.",
            "No biological identification, abundance, maneuver quality or athletic skill inference.",
            "Only sparse temporal samples; review full source for accurate action boundaries.",
            "A phase with a failed decode is omitted and never replaced with fabricated evidence.",
        ],
    }
    (target/"manifest.json").write_text(
        json.dumps(manifest,indent=2,sort_keys=True,allow_nan=False)+"\n")
    (target/"review.html").write_text(
        _contact_sheet(cards,asset_id=asset_id),encoding="utf-8")
    return {
        "status":status,"assetId":asset_id,
        "events":len(cards),"decodedFrames":len(cached),
        "phaseErrors":len(errors),
        "manifest":str(target/"manifest.json"),
        "reviewHtml":str(target/"review.html"),
        "warning":"Review evidence only; no source clip has been independently judged or exported",
    }

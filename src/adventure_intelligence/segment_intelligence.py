"""Conservative, sport-specific time segments and independent event overlays.

A segment is a hypothesis from sparse images, not evidence of swimming
physiology, technique, athlete skill, wildlife identity, or exact boundaries.
Requested seek times form the video-local timeline. Source PTS is preserved
separately and MUST NEVER be treated as video-relative time without an explicit
start-time normalization.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from html import escape
from io import BytesIO
import json
import math
from pathlib import Path

from .scene_atlas import unit_vector
from .activity_intelligence import SiglipActivityClassifier, _hash
from .ecology_encounters import OBJECT_GROUPS

VERSION="temporal-segments-v1"
PROMPTS={
    "snorkeling": {
        "underwater_sand": [
            "Underwater action camera filming a very pale white sandy seafloor with rippled sunlight.",
            "POV snorkeling submerged above a shallow white sandbar and clear water.",
            "Underwater camera looking down at sand and a stingray swimming across the ocean floor."],
        "underwater_reef": [
            "Underwater POV passing over a coral reef with rocks, tropical fish and marine life.",
            "Snorkeling camera underwater exploring rocks, coral and algae on the seabed.",
            "Underwater action video above a kelp bed and marine plants."],
        "underwater_open": [
            "Submerged POV looking through open blue water with distant ocean floor.",
            "Underwater camera swimming in open water with deep blue ocean scenery."],
        "surface_swim": [
            "First person snorkeling at the ocean surface, camera at waterline with waves.",
            "Swimming on the ocean surface with the camera partly underwater.",
            "First person floating in the sea with hands in water and surf visible."],
        "kayak_boat": [
            "Above water first person view of feet inside the bow of a kayak at sea.",
            "POV paddling a kayak with blue sky, shoreline and kayak bow visible.",
            "Sitting on a small boat or paddleboard looking over the ocean."],
        "shore_wading": [
            "A person wading on a shallow sandbar with their feet visible in water.",
            "Walking on a white sand beach with shallow turquoise sea beside it.",
            "Above the water surface, looking down at feet and sandy beach."],
        "other": [
            "Indoor camera view of a room, without a visible ocean.",
            "Hiking on a forest trail with trees and rocky ground."],
    },
    "ski": {
        "ski_lift": [
            "POV seated on a ski chairlift with snow slopes far below.",
            "Riding a ski gondola up the mountain above snowy trees."],
        "ski_downhill": [
            "First-person skiing downhill on a snowy slope, skis and trail ahead.",
            "POV skiing and carving turns down a steep open winter mountain slope."],
        "ski_technical_terrain_candidate": [
            "First-person skiing through tightly spaced trees and variable snow terrain.",
            "POV skiing on a steep ungroomed slope with moguls and obstacles."],
        "ski_flat_traverse": [
            "POV slowly traversing a broad flat groomed snow trail with skis.",
            "First-person cross mountain ski traverse on nearly level snow."],
        "ski_stopped": [
            "Skier standing still next to a snowy slope with skis on.",
            "Waiting on a ski slope, stationary with snow and people ahead."],
        "other": [
            "Driving a car on a paved road without skis.",
            "Walking indoors without snowy outdoor scenery."],
    },
    "bike": {
        "bike_technical_terrain_candidate": [
            "POV mountain biking on a steep rocky singletrack with roots and narrow obstacles.",
            "Mountain bike handlebars descending a rock garden and tight berm trail."],
        "bike_riding": [
            "First-person mountain biking along a forest dirt trail with handlebars visible.",
            "POV cycling on a mountain bike on a smooth flowing trail."],
        "bike_climbing_or_flat": [
            "Mountain biking slowly uphill on a wide dirt forest road.",
            "POV mountain bike riding a flat gentle broad dirt trail."],
        "bike_stopped": [
            "A parked mountain bike on the side of a dirt trail.",
            "POV cyclist stopped beside a dirt path with handlebars motionless."],
        "bike_walking": [
            "Person pushing a mountain bike on a steep walking trail.",
            "POV walking uphill carrying or pushing a bicycle."],
        "other": [
            "First-person walking without a bicycle.",
            "Driving on a paved road from inside a vehicle."],
    },
}
FAMILIES={
    "snorkeling":{
        "underwater_sand":"underwater", "underwater_reef":"underwater",
        "underwater_open":"underwater", "surface_swim":"surface",
        "kayak_boat":"above_water", "shore_wading":"above_water",
        "other":"other"},
    "ski":{
        "ski_lift":"transport", "ski_downhill":"active_ski",
        "ski_technical_terrain_candidate":"active_ski",
        "ski_flat_traverse":"traverse", "ski_stopped":"stopped",
        "other":"other"},
    "bike":{
        "bike_technical_terrain_candidate":"riding",
        "bike_riding":"riding", "bike_climbing_or_flat":"riding",
        "bike_stopped":"stopped", "bike_walking":"walking",
        "other":"other"},
}
BROAD_FALLBACK={
    "underwater":"underwater_habitat_unresolved",
    "active_ski":"ski_active_unresolved",
    "riding":"bike_riding_unresolved",
}
EVIDENCE_MARGIN=.012
FAMILY_MARGIN=.016
MAX_FRAME_COUNT=96


class SegmentModel:
    """One pinned, local SigLIP2 image model shared across all sport states."""
    def __init__(self, *, allow_model_download:bool=False):
        base=SiglipActivityClassifier(allow_model_download=allow_model_download)
        self.base=base
        self.revision=base.revision
        self.processor_hash=_hash(base.processor_config)
        self.text_vectors={}
        with base.torch.inference_mode():
            for activity,states in PROMPTS.items():
                self.text_vectors[activity]={}
                for state,prompts in states.items():
                    tokens=base.processor(text=prompts,padding="max_length",
                        truncation=True,return_tensors="pt").to(base.device)
                    vec=base.model.get_text_features(**tokens)
                    if hasattr(vec,"pooler_output"):
                        vec=vec.pooler_output
                    vec=base.torch.nn.functional.normalize(vec,dim=-1)
                    self.text_vectors[activity][state]=vec.cpu().float().tolist()

    def image_vectors(self, images:list[bytes]) ->list[list[float]]:
        return self.base.image_vectors(images)


def score_segment_frame(vector,activity:str,reference:dict) ->dict:
    if activity not in PROMPTS:
        raise ValueError("Segment activity must be snorkeling, ski or bike")
    normalized=unit_vector(vector)
    if normalized is None:
        raise ValueError("Invalid image embedding")
    scores={}
    for state,examples in reference.items():
        if not examples or any(len(x)!=len(normalized) for x in examples):
            raise ValueError("Incompatible segment text and image embeddings")
        scores[state]=max(sum(a*b for a,b in zip(normalized,e)) for e in examples)
    if set(scores)!=set(PROMPTS[activity]):
        raise ValueError("Incomplete sport-specific prompt bank")
    ranked=sorted(scores,key=lambda state:(-scores[state],state))
    best,second=ranked[:2]
    state_margin=scores[best]-scores[second]
    families=FAMILIES[activity]
    family_scores={}
    for state,value in scores.items():
        family=families[state]
        family_scores[family]=max(value,family_scores.get(family,-math.inf))
    family_ranks=sorted(family_scores,key=lambda family:(-family_scores[family],family))
    winning_family=family_ranks[0]
    family_margin=(family_scores[winning_family]-family_scores[family_ranks[1]]
                   if len(family_ranks)>1 else 0.)
    if state_margin>=EVIDENCE_MARGIN:
        label=best
        resolution="state"
    elif family_margin>=FAMILY_MARGIN and winning_family in BROAD_FALLBACK:
        label=BROAD_FALLBACK[winning_family]
        resolution="family_only"
    else:
        label="unknown"
        resolution="abstained"
    return {"label":label,"family":(winning_family if label!="unknown" else "unknown"),
            "resolution":resolution,
            "topState":best,"runnerUp":second,
            "relativeStateMargin":round(state_margin,5),
            "relativeFamilyMargin":round(family_margin,5),
            "relativeCosineByState":{k:round(v,5) for k,v in sorted(scores.items())},
            "calibratedProbability":None,
            "status":"unverified_scene_similarity"}


def coarse_timestamps(duration_ms:int, *, step_ms:int, max_frames:int,
                      refine_budget:int) ->list[int]:
    if (type(duration_ms) is not int or duration_ms<=0 or
        type(step_ms) is not int or not 3000<=step_ms<=120000 or
        type(max_frames) is not int or not 6<=max_frames<=MAX_FRAME_COUNT or
        type(refine_budget) is not int or not 0<=refine_budget<=max_frames-3):
        raise ValueError("Invalid bounded sampling budget")
    budget=max_frames-refine_budget
    if duration_ms<1000:
        return [0]
    spacing=max(step_ms,math.ceil(duration_ms/budget))
    times=list(range(0,duration_ms,spacing))[:budget]
    # Inspect near the end when there is a capacity slot, without going past
    # the actual indexed media duration. Never replace an existing probe.
    final=max(0,duration_ms-min(1000,duration_ms//20))
    if len(times)<budget and final not in times:
        times.append(final)
    return sorted(set(times))


def refinement_times(observations:list[dict], *, budget:int,
                     min_spacing_ms:int=3500) ->list[int]:
    """Investigate boundaries and ambiguous contexts only, not all old frames."""
    if type(budget) is not int or budget<0:
        raise ValueError("Invalid refinement budget")
    valid=sorted((o for o in observations if type(o.get("requestedMs")) is int),
                 key=lambda x:x["requestedMs"])
    candidates=[]
    existing={o["requestedMs"] for o in valid}
    for a,b in zip(valid,valid[1:]):
        gap=b["requestedMs"]-a["requestedMs"]
        if gap<min_spacing_ms*2:
            continue
        changed=a["label"]!=b["label"]
        uncertain="unknown" in (a["label"],b["label"])
        family_changed=a.get("family")!=b.get("family")
        if changed or uncertain:
            t=(a["requestedMs"]+b["requestedMs"])//2
            priority=(4 if family_changed else 3 if uncertain else 2,
                      -min(a["relativeFamilyMargin"],b["relativeFamilyMargin"]),
                      gap)
            candidates.append((priority,t))
    # Never use a low-margin boundary to imply a verified transition.
    for _,t in sorted(candidates,key=lambda pair:(pair[0],-pair[1]),reverse=True):
        if t in existing:
            continue
        existing.add(t)
        if len(existing)-len(valid)>=budget:
            break
    return sorted(existing-{o["requestedMs"] for o in valid})


def build_segments(observations:list[dict], duration_ms:int, *,
                   max_support_radius_ms:int=12000) ->dict:
    """Disjoint, evidence-bounded intervals; unknown gaps are left unknown."""
    if type(duration_ms) is not int or duration_ms<=0:
        raise ValueError("Positive video duration required")
    if not 1000<=max_support_radius_ms<=30000:
        raise ValueError("Invalid support radius")
    observations=sorted(observations,key=lambda x:x["requestedMs"])
    if any(type(x["requestedMs"]) is not int or
           not 0<=x["requestedMs"]<duration_ms
           for x in observations):
        raise ValueError("Observation timestamps outside media duration")
    if len({o["requestedMs"] for o in observations})!=len(observations):
        raise ValueError("Duplicate sampled timestamps are not independent evidence")
    slices=[]
    for i,frame in enumerate(observations):
        t=frame["requestedMs"]
        previous=observations[i-1]["requestedMs"] if i else None
        following=observations[i+1]["requestedMs"] if i+1<len(observations) else None
        start=max(0,t-max_support_radius_ms,
                  (previous+t)//2 if previous is not None else 0)
        end=min(duration_ms,t+max_support_radius_ms,
                (t+following)//2 if following is not None else duration_ms)
        if end<=start:
            continue
        slices.append({"startMs":start,"endMs":end,
                       "label":frame["label"],"family":frame["family"],
                       "frames":[frame]})
    merged=[]
    for node in slices:
        # A single local frame is a navigation hint; merge only touching
        # same-state intervals. A large unsampled gap ALWAYS splits.
        if (merged and merged[-1]["label"]==node["label"]
            and merged[-1]["family"]==node["family"]
            and node["startMs"]==merged[-1]["endMs"]):
            merged[-1]["endMs"]=node["endMs"]
            merged[-1]["frames"].extend(node["frames"])
        else:
            merged.append(node)
    out=[]
    for ix,node in enumerate(merged,1):
        sampled=node["frames"]
        n=len(sampled)
        is_unknown=node["label"]=="unknown"
        out.append({
            "segmentId":f"seg-{ix:04d}",
            "startMs":node["startMs"],"endMs":node["endMs"],
            "label":node["label"],"family":node["family"],
            "evidenceFrameCount":n,
            "requestedSampleTimesMs":[f["requestedMs"] for f in sampled],
            "sourcePtsEvidenceMs":[f.get("decodedPtsMs") for f in sampled],
            "medianRelativeFamilyMargin":round(
                sorted(f["relativeFamilyMargin"] for f in sampled)[n//2],5),
            "status":("abstained" if is_unknown else
                      "provisional_multi_frame" if n>=2 else
                      "single_frame_navigation_hint"),
            "boundaryMeaning":"halfway_between_samples_capped_by_support_radius",
            "verifiedActivity":False,
            "verifiedSportTechnique":False,
        })
    coverage=sum(s["endMs"]-s["startMs"] for s in out)
    return {"segments":out,
            "unsampledMs":max(0,duration_ms-coverage),
            "coveredFraction":round(coverage/duration_ms,4),
            "sampleCount":len(observations),
            "method":"bounded_sample_voronoi_with_temporal_support_v1"}


def overlay_events(segments:list[dict],detector_frames:dict) ->list[dict]:
    """Independent wildlife hints: overlapping cached object labels, not sightings."""
    for segment in segments:
        hints={}
        visited=set()
        for provider,frames in detector_frames.items():
            for frame in frames[:512]:
                t=frame.get("tMs")
                if type(t) is not int or not segment["startMs"]<=t<segment["endMs"]:
                    continue
                for obj in (frame.get("objects") or [])[:100]:
                    if not isinstance(obj,dict):
                        continue
                    label=obj.get("label")
                    group=OBJECT_GROUPS.get(label.strip().lower()) if isinstance(label,str) else None
                    score=obj.get("score")
                    if (group is None or not isinstance(score,(float,int))
                        or isinstance(score,bool) or not math.isfinite(score)
                        or not 0<=score<=1):
                        continue
                    # A group+timestamp can be proposed by multiple models;
                    # avoid artificial duplicate support.
                    identity=(t,group)
                    if identity in visited:
                        continue
                    visited.add(identity)
                    entry=hints.setdefault(group,{"group":group,"sampleTimesMs":[],
                                                   "providers":[]})
                    entry["sampleTimesMs"].append(t)
                    if provider not in entry["providers"]:
                        entry["providers"].append(provider)
        segment["independentEventHints"]=[
            {"kind":"unverified_marine_object",**h,
             "sampleTimesMs":sorted(h["sampleTimesMs"])[:24],
             "notSpeciesEvidence":True,"notAnimalCount":True}
            for h in sorted(hints.values(),key=lambda x:x["group"])]
    return segments


def _escape_photo_name(name:str)->str:
    return escape(name,quote=True)


def html_review(report:dict) ->str:
    rows=[]
    activity=report["activity"]
    dur=max(1,report["durationMs"])
    for segment in report["segments"]:
        start=segment["startMs"]/1000
        end=segment["endMs"]/1000
        duration=end-start
        label=escape(segment["label"])
        family=escape(segment["family"])
        hints=", ".join(escape(h["group"]) for h in segment["independentEventHints"])
        rows.append(
            '<article><div class="seghead"><strong>'+label+'</strong>'
            f'<span>{start:.1f}–{end:.1f}s · {duration:.1f}s</span></div>'
            f'<p>{family} · {escape(segment["status"])} · '
            f'{segment["evidenceFrameCount"]} frame(s)</p>'
            +(f'<small>Unverified object proposals: {hints}</small>' if hints else "")
            +'</article>')
    preview=[]
    for frame in report.get("previewFrames",[]):
        src=_escape_photo_name(frame["image"])
        label=escape(frame["label"])
        preview.append(
            f'<div><img loading="lazy" src="{src}" alt="Sampled source frame">'
            f'<span>{frame["requestedMs"]/1000:.1f}s · {label}</span></div>')
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Klip · Temporal scene review</title><style>'
        'body{font:14px system-ui,sans-serif;background:#101a25;color:#eef2f5;'
        'max-width:1100px;margin:26px auto;padding:0 18px;line-height:1.4}'
        'h1{font-size:1.6rem}p,small,span{color:#aab9c8}'
        '.intro{max-width:720px}.grid{display:grid;grid-template-columns:'
        'repeat(auto-fill,minmax(245px,1fr));gap:12px}'
        'article{padding:15px;background:#1c2b39;border:1px solid #314354;'
        'border-radius:12px}strong{color:#f2c4b9}'
        '.seghead{display:flex;gap:12px;justify-content:space-between}'
        '.photos{display:flex;overflow:auto;gap:10px;padding:18px 0}'
        '.photos div{flex:0 0 185px}.photos img{width:185px;height:124px;'
        'object-fit:contain;background:#09131b;border-radius:8px}'
        '.photos span{display:block;padding:6px 0;font-size:12px}'
        '</style></head><body><h1>Scene segments · '+escape(activity)+'</h1>'
        '<p class="intro">Estimated temporal context from sparse, uncalibrated '
        'visual similarity. Segments are navigation proposals only. A wildlife '
        'overlay is not a species identification or an animal count. Unknown '
        'intervals and frame-level errors are preserved.</p>'
        f'<p>Coverage: {report["coverage"]["coveredFraction"]:.0%} · '
        f'{report["coverage"]["sampleCount"]} observations · '
        f'{report["durationMs"]/1000:.1f}s video</p>'
        '<section class="photos">'+''.join(preview)+'</section>'
        '<main class="grid">'+''.join(rows)+'</main></body></html>')

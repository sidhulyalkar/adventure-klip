"""Conservative, cached-only evidence for how two reviewed scenes meet.

No new frame decoding, model downloads or neural inference. All observations
are hypotheses from checksum-verified semantic/artifact data, never verified
object identity or species taxonomy. A matching object word does not imply
that the same individual persisted across a cut.
"""
from __future__ import annotations

import math

from .artifacts import read_artifact
from .acoustics import AUDIO_VERSION
from .audio_triage import audio_window
from .scene_atlas import clip_feature, frames_for_asset, similarity

VERSION = "edge-continuity-v1"
FRAME_WINDOW_MAX_MS = 4500
_VALID_LABEL_LEN = 100


def _timestamp(frame):
    t=frame.get("decodedPtsMs")
    if not isinstance(t,int) or isinstance(t,bool) or t<0:
        t=frame.get("tMs")
    return t if isinstance(t,int) and not isinstance(t,bool) and t>=0 else None


def _window(clip, edge):
    begin,end=clip["startMs"],clip["endMs"]
    span=end-begin
    if span<=0:
        raise ValueError("Invalid scene window")
    width=min(FRAME_WINDOW_MAX_MS,max(1500,span//3))
    if edge=="head":
        return begin,min(end,begin+width)
    if edge=="tail":
        return max(begin,end-width),end
    raise ValueError("Invalid edge")


def _observations(frames, start, end):
    return [f for f in frames if isinstance(f,dict)
            and (t:=_timestamp(f)) is not None and start<=t<end]


def _objects(frames):
    counts={}
    best={}
    for frame in frames:
        labels=set()
        for obj in frame.get("objects") or []:
            if not isinstance(obj,dict):
                continue
            label=obj.get("label")
            score=obj.get("score")
            if not isinstance(label,str) or not 0<len(label.strip())<=_VALID_LABEL_LEN:
                continue
            if not isinstance(score,(int,float)) or isinstance(score,bool) or not math.isfinite(score) or not 0<=score<=1:
                continue
            label=label.strip()
            # Multiple detections in a frame support presence of a label only
            # once, not multiple independent observations of individuals.
            labels.add(label)
            best[label]=max(best.get(label,0),float(score))
        for label in labels:
            counts[label]=counts.get(label,0)+1
    return [{"label":key,"sampledFrames":counts[key],
             "maxModelScore":round(best[key],4),"verified":False,
             "identity":"unknown","taxonomy":"unverified_query_label"}
            for key in sorted(counts,key=lambda k:(-counts[k],k))[:12]]


def _context(frames):
    scores={}
    for f in frames:
        scene=f.get("sceneScores") or {}
        if scene.get("method")!="siglip2_image_text_cosine":
            continue
        for label,score in (scene.get("similarities") or {}).items():
            if (isinstance(label,str) and isinstance(score,(int,float))
                 and not isinstance(score,bool) and math.isfinite(score)
                 and -1.01<=score<=1.01):
                scores.setdefault(label,[]).append(float(score))
    ranked=sorted(((sum(v)/len(v),k) for k,v in scores.items()),reverse=True)
    return [{"label":k,"relativeSimilarity":round(s,4),
             "interpretation":"uncalibrated_text_image_similarity"}
            for s,k in ranked[:3]]


class EdgeEvidence:
    """Read each source's immutable, checksum-verified caches once per request."""
    def __init__(self,db,workspace):
        self.db,self.workspace=db,workspace
        self.semantic={}
        self.audio={}

    def frames(self,asset,provider):
        key=(asset,provider)
        if key not in self.semantic:
            self.semantic[key]=frames_for_asset(self.db,self.workspace,asset,provider)
        return self.semantic[key]

    def acoustics(self,asset):
        if asset not in self.audio:
            doc=read_artifact(self.db,self.workspace,asset,AUDIO_VERSION)
            self.audio[asset]=(doc or {}).get("series")
        return self.audio[asset]

    def snapshot(self,clip,edge):
        start,end=_window(clip,edge)
        asset=clip["sourceAssetId"]
        objects_frames,_=self.frames(asset,"owlv2")
        context_frames,_=self.frames(asset,"context")
        visual_frames,model_key=self.frames(asset,"siglip2")
        # Combined "stack" artifacts can contain OWL objects and SigLIP
        # vectors, but never compare their embeddings to plain SigLIP since
        # their provider/config provenance is not identical.
        stack_frames,stack_key=self.frames(asset,"stack")
        obj_samples=_observations(objects_frames,start,end)
        if not obj_samples:
            obj_samples=_observations(stack_frames,start,end)
        context_samples=_observations(context_frames,start,end)
        if model_key is None:
            visual_frames,model_key=stack_frames,stack_key
        vec=clip_feature(visual_frames,model_key,start,end)
        audio=audio_window(self.acoustics(asset),start,end)
        return {
            "startMs":start,"endMs":end,
            "objectObservations":_objects(obj_samples),
            "objectSampleCount":len(obj_samples),
            "sceneContext":_context(context_samples),
            "contextSampleCount":len(context_samples),
            "embeddingSamples":vec.get("sampleCount",0),
            "embeddingAvailable":bool(vec["available"]),
            "audio":{k:v for k,v in audio.items() if k in
                     {"available","status","observations","medianRms",
                      "possibleDistortion","unusualAcousticChange","reason"}},
            "_vector":vec.get("vector"),"_modelKey":vec.get("modelKey"),
        }


def compare_edges(cache,first,second,first_activity="unknown",second_activity="unknown"):
    """Recommend an editable cut or a fade through black, never a physical match."""
    a=cache.snapshot(first,"tail")
    b=cache.snapshot(second,"head")
    visual=None
    if (a["_vector"] and b["_vector"] and a["_modelKey"]==b["_modelKey"]):
        visual=similarity(a["_vector"],b["_vector"])
    left={x["label"] for x in a["objectObservations"]}
    right={x["label"] for x in b["objectObservations"]}
    shared=sorted(left & right)
    same_asset=first["sourceAssetId"]==second["sourceAssetId"]
    gap=second["startMs"]-first["endMs"] if same_asset else None
    rationale=[]
    fade=False
    if first_activity!=second_activity and first_activity!="unknown" and second_activity!="unknown":
        fade=True
        rationale.append("Activity changes; a soft break may clarify the edit")
    if visual is not None:
        if visual<.45:
            fade=True
            rationale.append("Very different edge imagery under the same embedding model")
        elif visual>=.93:
            rationale.append("Similar edge appearance, not verified scene continuity")
    if shared:
        rationale.append("Repeated object queries: "+", ".join(shared[:4])+" (identities unverified)")
    if same_asset and gap is not None and 0<=gap<=2500:
        rationale.append("Source timestamps are nearby; inspect whether the natural cut works")
        if not fade:
            rationale.append("A hard cut preserves the original motion")
    if a["audio"].get("unusualAcousticChange") or b["audio"].get("unusualAcousticChange"):
        rationale.append("Acoustic changes suggest checking the sound across the cut")
    if not rationale:
        rationale.append("No reliable continuity evidence; hard cut is the conservative default")
    style="fade" if fade else "cut"
    def public(x):
        return {k:v for k,v in x.items() if not k.startswith("_")}
    return {
        "version":VERSION,"suggestedTransition":style,
        "transitionStyle":"fade-through-black" if fade else "hard-cut",
        "status":"unverified_editorial_evidence",
        "edgeVisualSimilarity":round(visual,4) if visual is not None else None,
        "visualComparable":visual is not None,
        "sharedObjectQueries":shared[:8],
        "sameAsset":same_asset,"sourceGapMs":gap,
        "outgoing":public(a),"incoming":public(b),
        "reasons":rationale,
        "limitations":[
            "Object classes and potential species are zero-shot labels, never confirmed identities",
            "Sparse samples may miss entrances, exits and scene changes",
            "The render recommendation is not a guarantee of a seamless edit"
        ],
    }

"""Opt-in bounded seven-frame second look over already verified private H.264 clips.

A second look is a *follow-up investigation*, not proof of continuous motion.
It never opens the DJI originals or changes the human review database.
Requests travel only to an Ollama instance on literal loopback.
"""
from __future__ import annotations

import base64
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

from . import adaptive_judge_v2 as v2
from . import studio_preview_judge as preview
from .media import probe, require_tool

POLICY="adaptive-temporal-second-look-v1"
SAMPLE_FRACTIONS=(.07,.20,.34,.50,.66,.80,.93)
MAX_JPEG_BYTES=105_000
RATINGS=("eventInterest","subjectVisibility","learningPotential","sharingPotential")
_STATES=("yes","no","uncertain")
_TRENDS=("potential_encounter","potential_maneuver","scenic_or_atmospheric",
         "activity_transition","unremarkable","unclear")


def should_investigate(first:dict|None)->bool:
    """Decide *eligibility*, not a claim that an event exists."""
    return bool(first and (
        first.get("needsTemporalReview") is True or
        first.get("recommendation")=="unsure" or
        first.get("observedEvent")=="uncertain" or
        first.get("uncertaintyReason") in
        ("between_frames","execution_unverified","identity_unclear",
         "subject_not_visible","scene_ambiguous")
    ))


def prompt_for(context:str)->str:
    if context not in v2.GROUPS:
        raise ValueError("Unknown activity hint")
    return (
        "You are a source-grounded adventure clip investigation assistant. "
        "You see SEVEN ORDERED still images at fractions "
        "7%, 20%, 34%, 50%, 66%, 80%, 93% through a single short clip. "
        "You do NOT see the intervening frames, true video motion, or audio. "
        "Describe only visible subjects, scene changes and context, citing "
        "the frame indices (0..6) that directly support each observation. "
        "Different positions in different still frames can SUGGEST a "
        "sequence but do not prove continuous movement or causality. "
        "A skier near a fallen figure does not prove a fall/recovery. "
        "A jump/rock garden in view does not establish completed execution. "
        "Do not identify wildlife species, count indistinct animals, infer "
        "rarity, location, or claim actions that happen between stills. "
        "Distinguish local animal visibility from ambient water clarity. "
        "A murky sandbar with a visible ray-like animal can deserve "
        "investigation over crystal-clear empty sand. A shadow alone "
        "requires abstention or more evidence, not a fabricated species. "
        "Separate discovery, learning and share-worthiness. "
        "Ratings are ordinal 0..3 or null if not observable, NOT calibrated "
        "probabilities. A recommendation is a human review PRIORITY only. "
        "Uncertain possible events should be UNSURE, never automatically "
        "discarded. Keep can mean visually interesting, not a verified trick. "
        "Only return the requested JSON.\n\n"
        "Weak activity hint (not ground truth): "+context+
        ". Ignore this hint if the images contradict it. "
        "Context-specific checks: "+v2.SPORT_GUIDANCE[context]
    )


def schema()->dict:
    properties={
        "recommendation":{"type":"string","enum":["keep","skip","unsure"]},
        "observedEvent":{"type":"string","enum":list(_STATES)},
        "sceneType":{"type":"string","enum":list(_TRENDS)},
        "summary":{"type":"string"},
        "reason":{"type":"string"},
        "frameEvidence":{
            "type":"array","minItems":1,"maxItems":5,
            "items":{"type":"object","additionalProperties":False,
                     "required":["index","observation"],
                     "properties":{
                        "index":{"type":"integer","minimum":0,"maximum":6},
                        "observation":{"type":"string"},
                     }},
        },
        "uncertainty":{"type":"string"},
        "needsContinuousVideoReview":{"type":"boolean"},
        "ratings":{"type":"object","additionalProperties":False,
                   "required":list(RATINGS),
                   "properties":{k:{"type":["integer","null"],"minimum":0,
                                    "maximum":3} for k in RATINGS}},
    }
    return {"type":"object","additionalProperties":False,
            "required":list(properties),"properties":properties}


def fingerprint(context:str)->str:
    payload={"policy":POLICY,"context":context,"prompt":prompt_for(context),
             "schema":schema(),"samplingFractions":SAMPLE_FRACTIONS,
             "jpegMaxBytes":MAX_JPEG_BYTES,"temperature":0}
    return sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),
                             allow_nan=False).encode()).hexdigest()


def _samples(path:Path)->tuple[list[str],list[int]]:
    """Decode seven JPEGs from short proxy, never original DJI media."""
    duration=float(probe(path)["duration"])
    if not math.isfinite(duration) or duration<1 or duration>61:
        raise ValueError("Bounded H264 preview duration invalid")
    ffmpeg=require_tool("ffmpeg")
    encoded=[]
    stamps=[]
    with tempfile.TemporaryDirectory(prefix="klip-temporal-private-") as folder:
        for i,fraction in enumerate(SAMPLE_FRACTIONS):
            seconds=duration*fraction
            filename=Path(folder)/f"second-look-{i}.jpg"
            args=[ffmpeg,"-hide_banner","-loglevel","error","-nostdin",
                  "-ss",f"{seconds:.3f}","-i",str(path),
                  "-frames:v","1","-vf","scale='min(384,iw)':-2",
                  "-q:v","9","-y",str(filename)]
            job=subprocess.run(args,stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE,timeout=30,check=False)
            if (job.returncode or not filename.is_file() or
                    not 100<filename.stat().st_size<=MAX_JPEG_BYTES):
                raise RuntimeError(
                    "Unable to decode one bounded second-look frame from the H264 proxy")
            encoded.append(base64.b64encode(filename.read_bytes()).decode("ascii"))
            stamps.append(int(round(seconds*1000)))
    return encoded,stamps


def validate(raw:dict)->dict:
    if not isinstance(raw,dict) or set(raw)!=set(schema()["required"]):
        raise ValueError("Malformed second-look response fields")
    if raw["recommendation"] not in ("keep","skip","unsure") or \
            raw["observedEvent"] not in _STATES or \
            raw["sceneType"] not in _TRENDS or \
            type(raw["needsContinuousVideoReview"]) is not bool:
        raise ValueError("Invalid second-look categorical assessment")
    for key,cap in (("summary",240),("reason",480),("uncertainty",240)):
        if not isinstance(raw[key],str) or not 1<=len(raw[key])<=cap:
            raise ValueError("Missing or overlong second-look narrative")
    e=raw["frameEvidence"]
    if not isinstance(e,list) or not 1<=len(e)<=5:
        raise ValueError("Second look requires 1..5 frame references")
    seen=set()
    for row in e:
        if (not isinstance(row,dict) or set(row)!={"index","observation"} or
                type(row["index"]) is not int or
                not 0<=row["index"]<len(SAMPLE_FRACTIONS) or
                row["index"] in seen or not isinstance(row["observation"],str)
                or not 1<=len(row["observation"])<=160):
            raise ValueError("Invalid or duplicated second-look frame reference")
        seen.add(row["index"])
    ratings=raw["ratings"]
    if (not isinstance(ratings,dict) or set(ratings)!=set(RATINGS) or
            any(v is not None and (type(v) is not int or v<0 or v>3)
                for v in ratings.values())):
        raise ValueError("Second-look scores must be 0..3 or null")
    proposed=raw["recommendation"]
    recommendation=proposed
    safety=[]
    if (recommendation=="skip" and
            (raw["observedEvent"]=="uncertain" or
             raw["needsContinuousVideoReview"] or
             (ratings["eventInterest"] is not None and ratings["eventInterest"]>=2))):
        recommendation="unsure"
        safety.append("avoid_skip_when_temporal_evidence_incomplete")
    if recommendation=="keep" and (raw["observedEvent"]=="uncertain"
                                   and ratings["subjectVisibility"] in (None,0)):
        recommendation="unsure"
        safety.append("avoid_overclaim_invisible_subject")
    return {
        "policyVersion":POLICY,"recommendation":recommendation,
        "rawModelRecommendation":proposed,
        "observedEvent":raw["observedEvent"],
        "sceneType":raw["sceneType"],
        "summary":raw["summary"],"reason":raw["reason"],
        "frameEvidence":e,"uncertainty":raw["uncertainty"],
        "needsContinuousVideoReview":raw["needsContinuousVideoReview"],
        "ratings":ratings,"policyOverrides":safety,
        "humanApprovalRequired":True,"reviewedByHuman":False,
        "speciesVerified":False,"athleteTechniqueVerified":False,
        "videoOrAudioReviewed":False,"sourceFramesSeen":len(SAMPLE_FRACTIONS),
        "interpretation":"Seven separate stills; no validated temporal action",
    }


def _record_path(path:Path,model:str,context:str)->Path:
    if not preview.valid_local_model(model) or context not in v2.GROUPS:
        raise ValueError("Invalid local model or context")
    key=sha256((model+":"+fingerprint(context)).encode()).hexdigest()[:24]
    return path.with_name(path.stem+"-temporal-v1-"+key+".json")


def cached_second_look(path:Path,*,model:str,context:str,
                       first:dict|None=None)->dict|None:
    destination=_record_path(path,model,context)
    if (not destination.is_file() or not path.is_file() or
            destination.stat().st_size>22000):
        return None
    try:
        raw=json.loads(destination.read_text(encoding="utf-8"))
        if (raw.get("model")!=model or raw.get("context")!=context or
                raw.get("fingerprint")!=fingerprint(context) or
                raw.get("previewSha256")!=sha256(path.read_bytes()).hexdigest()):
            return None
        answer=validate(raw["assessment"])
        return {**answer,"model":model,"contextHint":context,
                "promptFingerprint":fingerprint(context),
                "sampleTimesMs":raw["sampleTimesMs"],
                "cached":True,"backend":"ollama_loopback_only",
                "firstPassComparison":_comparison(first,answer)}
    except (ValueError,KeyError,TypeError,OSError):
        return None


def _comparison(first:dict|None,second:dict)->dict:
    if not first:
        return {"previousJudgmentAvailable":False,
                "recommendationChanged":None,
                "previousRecommendation":None}
    old=first.get("recommendation")
    return {"previousJudgmentAvailable":True,
            "previousRecommendation":old,
            "recommendationChanged":old!=second["recommendation"],
            "firstPassWasProvisional":True,
            "secondPassIsStillProvisional":True}


def second_look(path:Path,*,model:str,context:str,
                first:dict|None,transport=None)->dict:
    if not preview.valid_local_model(model) or context not in v2.GROUPS:
        raise ValueError("Only a named localhost image model and known sport context are supported")
    with preview._CACHE_LOCK:
        previous=cached_second_look(path,model=model,context=context,first=first)
        if previous is not None:
            return previous
        encoded,stamps=_samples(path)
        payload={"model":model,"stream":False,"format":schema(),
                 "options":{"temperature":0},
                 "messages":[{"role":"user","content":prompt_for(context),
                              "images":encoded}]}
        response=v2._send_local(payload,transport=transport)
        try:
            raw=json.loads(response["message"]["content"])
        except (ValueError,KeyError,TypeError) as exc:
            raise ValueError("Local second-look model returned invalid JSON") from exc
        assessed=validate(raw)
        record={"model":model,"policy":POLICY,"context":context,
                "fingerprint":fingerprint(context),
                "previewSha256":sha256(path.read_bytes()).hexdigest(),
                "sampleTimesMs":stamps,"assessment":raw}
        target=_record_path(path,model,context)
        with tempfile.NamedTemporaryFile(mode="w",encoding="utf-8",
                prefix=".klip-temporal-",dir=target.parent,delete=False) as f:
            tmp=Path(f.name)
            json.dump(record,f,sort_keys=True,allow_nan=False)
        try:
            os.replace(tmp,target)
        finally:
            tmp.unlink(missing_ok=True)
        return {**assessed,"model":model,"contextHint":context,
                "promptFingerprint":fingerprint(context),
                "sampleTimesMs":stamps,
                "cached":False,"backend":"ollama_loopback_only",
                "firstPassComparison":_comparison(first,assessed),
                "totalDurationNs":response.get("total_duration"),
                "modelTagMutable":True}

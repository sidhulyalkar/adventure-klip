"""Sport-aware evidence-first local visual judge v2.

This policy is explicitly opt-in while v1 jobs are running. v2 has a separate
content-addressed cache, so already-running v1 inference/checkpoints are never
modified. Three stills are NOT continuous video or audio evidence. Any model
opinion is provisional and cannot alter human-reviewed SQLite truth.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import studio_preview_judge as legacy

POLICY="adaptive-context-rubric-v2"
GROUPS={"snorkeling","ski","bike","unresolved"}
SCENES={
    "underwater_sand","underwater_reef","open_water","surface_or_boat",
    "ski_lift_or_flat","ski_descent","ski_terrain_feature",
    "bike_transfer_or_climb","bike_flow_trail","bike_technical_feature",
    "landscape_or_other","unresolved",
}
DIMENSIONS=(
    "eventInterest","subjectVisibility","ambientClarity",
    "narrativePotential","visualQuality","learningPotential",
    "sharingPotential",
)
UNCERTAINTY={
    "none","between_frames","identity_unclear","execution_unverified",
    "subject_not_visible","scene_ambiguous","poor_frame_quality",
}
SPORT_GUIDANCE={
    "snorkeling":(
        "Understand above-water transport vs actual submerged filming; pale "
        "sandbar and reef scenes can both contain exceptional wildlife. "
        "Judge whether a *subject* is locally visible, independent of water "
        "turbidity or overall blue content. Look for plausible ray, shark, "
        "turtle, fish, or coral observation, but do not assert any species, "
        "animal count, rarity, interactions or behavior from 3 stills. "
        "A murky frame with a distinctive visible animal can be compelling; "
        "a pristine empty sandy bottom may not be. A shadow alone is "
        "insufficient: request temporal review rather than rejecting it."
    ),
    "ski":(
        "Distinguish stationary lifts, level traverses, sustained descents, "
        "turn sequences, terrain approaches and scenic footage. Terrain "
        "difficulty alone does NOT establish skier skill. Do not claim "
        "that a jump landed, a trick occurred, or a controlled turn was "
        "completed without continuous temporal evidence. Smooth riding "
        "can produce little camera motion and still be valuable for learning."
    ),
    "bike":(
        "Distinguish climbs, transfer trails, flowing descents, rock gardens, "
        "berms, jumps and technical features. A feature in the frame "
        "does NOT demonstrate riding it, landing or successful execution. "
        "Look for evidence of approach and exit, while marking action "
        "continuity unknown with 3 frames. Video shake is NOT a skill score."
    ),
    "unresolved":(
        "Independently determine whether this is underwater footage, skiing, "
        "mountain biking, transport, or something else. Do not inherit a sport "
        "label just because the archive selected this recording for review. "
        "A visually ordinary scene may still contain a missed brief event. "
        "Prefer unsure when sport or meaningful action remains ambiguous."
    ),
}
_COMMON=(
    "You are an evidence-constrained personal adventure-film EDITORIAL TRIAGE "
    "assistant. These are EXACTLY THREE chronological STILL images sampled "
    "from a short source interval. You did NOT view motion, intervening frames, "
    "audio, or the entire video bank. First describe what is visible, then "
    "separately assess what deserves deeper inspection or would interest a "
    "viewer. Never claim animal species, rarity, unique counts, named "
    "locations, completed tricks, safe landings, athlete performance, or "
    "continuous animal behavior without appropriate evidence. "
    "All scoring axes range 0..3 (0 poor/absent, 1 modest, 2 promising, "
    "3 visually exceptional) or null for UNKNOWN. Score 'eventInterest' "
    "for potential importance, 'subjectVisibility' for ability to actually "
    "see the subject, 'ambientClarity' for the overall scene independently, "
    "'narrativePotential' for whether a story might exist, 'visualQuality' "
    "for focus/framing, 'learningPotential' for whether review could help "
    "technique or filming, 'sharingPotential' for viewer comprehensibility. "
    "These are NOT calibrated probabilities. Personal meaning or novelty "
    "relative to other archive recordings cannot be inferred from three "
    "frames, so do not claim either. Missing context must be represented "
    "as null, uncertainty and 'needsTemporalReview'. "
    "An uncertain animal, dramatic terrain or obscured action should "
    "typically be UNSURE rather than SKIP when more frames could change "
    "the judgment. An attractive empty landscape may have visual value "
    "without representing an achievement. Protect the possibility of "
    "rare missed events even in dull-looking intervals. Return only JSON."
)

def context_hint(assignment:dict|None)->str:
    if not isinstance(assignment,dict):
        return "unresolved"
    group=assignment.get("selectionGroup")
    return group if group in GROUPS else "unresolved"


def prompt_for(context:str)->str:
    if context not in GROUPS:
        raise ValueError("Unsupported visual context")
    return (
        _COMMON + "\n\nWeak contextual prior: "+context+
        ". This is NOT verified interval ground truth. If the images disagree, "
        "choose the visible scene over this hint. Do not mention whether "
        "this interval was a model candidate, random control, or human review.\n"+
        "Context-specific evidence checks: "+SPORT_GUIDANCE[context]+
        "\n\nReturn observedEvent=yes only when a meaningful visual subject "
        "or feature is directly apparent. Return uncertain for action or "
        "encounter only inferred between stills. recommendation=keep is "
        "only a shortlist proposal; skip requires actually visible evidence "
        "for ordinary/redundant content, not mere absence of detected wildlife. "
        "For a possible event with unclear outcome choose unsure and "
        "needsTemporalReview=true. State one concrete frame-grounded rationale."
    )


def schema()->dict:
    fields={
        "scene":{"type":"string","enum":sorted(SCENES)},
        "observedEvent":{"type":"string","enum":["yes","no","uncertain"]},
        "recommendation":{"type":"string","enum":["keep","skip","unsure"]},
        "summary":{"type":"string"},
        "reason":{"type":"string"},
        "evidence":{"type":"array","items":{"type":"string"},
                    "minItems":1,"maxItems":3},
        "uncertainty":{"type":"string","enum":sorted(UNCERTAINTY)},
        "needsTemporalReview":{"type":"boolean"},
        "ratings":{"type":"object","additionalProperties":False,
                   "required":list(DIMENSIONS),
                   "properties":{k:{"type":["integer","null"],"minimum":0,"maximum":3}
                                 for k in DIMENSIONS}},
    }
    return {"type":"object","additionalProperties":False,
            "required":list(fields),"properties":fields}


def policy_fingerprint(context:str)->str:
    bundle={"policy":POLICY,"context":context,"prompt":prompt_for(context),
            "format":schema(),"sampleFractions":[.18,.50,.82],
            "temperature":0}
    return sha256(json.dumps(bundle,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def normalize_reply(raw:dict)->dict:
    """Strictly validate scores and audit deterministic conservative overrides."""
    if not isinstance(raw,dict) or set(raw)!=set(schema()["required"]):
        raise ValueError("Visual judge v2 returned unexpected or missing fields")
    if raw["scene"] not in SCENES or raw["observedEvent"] not in ("yes","no","uncertain"):
        raise ValueError("Invalid scene or observed-event assessment")
    if raw["recommendation"] not in ("keep","skip","unsure"):
        raise ValueError("Invalid editorial suggestion")
    if raw["uncertainty"] not in UNCERTAINTY or type(raw["needsTemporalReview"]) is not bool:
        raise ValueError("Invalid temporal/uncertainty evidence")
    for key,limit in (("summary",240),("reason",480)):
        if not isinstance(raw[key],str) or not 1<=len(raw[key])<=limit:
            raise ValueError("Invalid explanation")
    if (not isinstance(raw["evidence"],list) or
        not 1<=len(raw["evidence"])<=3 or
        any(not isinstance(x,str) or not 1<=len(x)<=160 for x in raw["evidence"])):
        raise ValueError("Expected up to three concrete frame-grounded observations")
    scores=raw["ratings"]
    if not isinstance(scores,dict) or set(scores)!=set(DIMENSIONS):
        raise ValueError("Missing or unexpected rating dimensions")
    if any(v is not None and (type(v) is not int or not 0<=v<=3) for v in scores.values()):
        raise ValueError("Scores must be integer 0..3 or null")
    # An apparently unremarkable frame is never a sufficient reason to
    # reject a possibly important event whose temporal span is unseen.
    suggestion=raw["recommendation"]
    overrides=[]
    if suggestion=="skip" and (raw["needsTemporalReview"] or
            raw["observedEvent"]=="uncertain" or
            (raw["observedEvent"]=="yes" and
             (scores["eventInterest"] is None or scores["eventInterest"]>=1)) or
            (scores["eventInterest"] is not None and scores["eventInterest"]>=2)):
        suggestion="unsure"
        overrides.append("avoid_false_skip_with_incomplete_temporal_evidence")
    if suggestion=="keep" and (raw["observedEvent"]=="uncertain" or
            raw["uncertainty"] in ("subject_not_visible","scene_ambiguous")):
        suggestion="unsure"
        overrides.append("unverified_event_requires_further_review")
    priority=("high" if suggestion=="keep" else
              "investigate" if suggestion=="unsure" else "low")
    return {
        "policyVersion":POLICY,
        "recommendation":suggestion,
        "rawModelRecommendation":raw["recommendation"],
        "observedEvent":raw["observedEvent"],
        "scene":raw["scene"],
        "summary":raw["summary"],
        "reason":raw["reason"],
        "evidence":raw["evidence"],
        "uncertaintyReason":raw["uncertainty"],
        "needsTemporalReview":raw["needsTemporalReview"],
        "ratings":scores,
        "priority":priority,
        "policyOverrides":overrides,
        "limitations":[
            "Only three stills; no continuous video or audio was reviewed",
            "Unverified species, athletic outcome and personal novelty",
        ],
        "reviewedByHuman":False,"speciesVerified":False,
        "athleteTechniqueVerified":False,
        "sourceFramesSeen":3,"videoOrAudioReviewed":False,
        "humanApprovalRequired":True,
        "interpretation":"Uncalibrated, provisional context-aware visual triage",
    }


def _cache_path(preview:Path,model:str,context:str)->Path:
    if not legacy.valid_local_model(model) or context not in GROUPS:
        raise ValueError("Invalid local model or contextual hint")
    fingerprint=policy_fingerprint(context)
    marker=sha256((model+":"+fingerprint).encode()).hexdigest()[:20]
    return Path(preview).with_name(Path(preview).stem+"-judge-v2-"+marker+".json")


def cached_adaptive_judgment(preview:Path,*,model:str,context:str)->dict|None:
    record=_cache_path(preview,model,context)
    if not record.is_file() or not preview.is_file() or record.stat().st_size>18000:
        return None
    try:
        document=json.loads(record.read_text(encoding="utf-8"))
        if (document.get("model")!=model or document.get("context")!=context or
            document.get("policyFingerprint")!=policy_fingerprint(context) or
            document.get("previewSha256")!=sha256(preview.read_bytes()).hexdigest()):
            return None
        assessment=normalize_reply(document["modelAssessment"])
        return {**assessment,"model":model,"contextHint":context,
                "promptFingerprint":policy_fingerprint(context),
                "backend":"ollama_loopback_only","cached":True}
    except (ValueError,KeyError,OSError,TypeError):
        return None


def _send_local(body:dict,timeout:int=150,transport=None)->dict:
    data=json.dumps(body,separators=(",",":")).encode()
    if len(data)>1_500_000:
        raise ValueError("Bounded local judge request exceeded image size limit")
    if transport is not None:
        return transport(body)
    req=Request("http://127.0.0.1:11434/api/chat",data=data,
                headers={"Content-Type":"application/json"},method="POST")
    try:
        with urlopen(req,timeout=timeout) as response:
            payload=response.read(131073)
            if len(payload)>131072:
                raise ValueError("Local model response too large")
            return json.loads(payload)
    except (URLError,TimeoutError,HTTPError):
        raise RuntimeError("Local Ollama is unavailable or timed out") from None


def adaptive_judgment(preview:Path,*,model:str,context:str="unresolved",
                      transport=None)->dict:
    if not legacy.valid_local_model(model) or context not in GROUPS:
        raise ValueError("Invalid installed local model or sport context")
    with legacy._CACHE_LOCK:
        cached=cached_adaptive_judgment(preview,model=model,context=context)
        if cached is not None:
            return cached
        images=legacy._jpeg_samples(preview)
        body={"model":model,"stream":False,"format":schema(),
              "options":{"temperature":0},
              "messages":[{"role":"user","content":prompt_for(context),
                           "images":images}]}
        response=_send_local(body,transport=transport)
        try:
            raw=json.loads(response["message"]["content"])
        except (ValueError,TypeError,KeyError) as exc:
            raise ValueError("Visual judge returned malformed JSON response") from exc
        answer=normalize_reply(raw)
        record={
            "policy":POLICY,"model":model,"context":context,
            "policyFingerprint":policy_fingerprint(context),
            "previewSha256":sha256(Path(preview).read_bytes()).hexdigest(),
            "modelAssessment":raw,
        }
        destination=_cache_path(preview,model,context)
        import os
        from tempfile import NamedTemporaryFile
        with NamedTemporaryFile(mode="w",encoding="utf-8",dir=destination.parent,
                                prefix=".judge-v2-",delete=False) as handle:
            temporary=Path(handle.name)
            json.dump(record,handle,sort_keys=True,allow_nan=False)
        try:
            os.replace(temporary,destination)
        finally:
            temporary.unlink(missing_ok=True)
        return {**answer,"model":model,"contextHint":context,
                "promptFingerprint":policy_fingerprint(context),
                "backend":"ollama_loopback_only","cached":False,
                "totalDurationNs":response.get("total_duration"),
                "modelTagMutable":True,
                "note":"Model tags are not immutable digests; pin the local Ollama model digest for release claims"}

"""Evidence-aware, multi-objective moment ranking, independent of model providers.

This is a deterministic REVIEW policy, not an action or species classifier.
No missing measurement is silently imputed as zero, no detector confidence is
treated as a probability, and publication always requires a separate human
approval. Sport-balanced selection is separate from the feature score so
naturally different activities cannot crowd out one another merely due to
camera motion.
"""
from __future__ import annotations

from collections import Counter
import json
import math
from html import escape
from pathlib import Path

VERSION="moment-ranking-policy-v1"
SPORTS=("snorkeling","ski","bike")
SIGNALS=(
    "eventSignificance",       # reviewed or qualified action/encounter value
    "visualNovelty",           # novelty among this person's clips
    "subjectVisibility",       # can the viewer actually see the subject?
    "imageQuality",            # sharpness, exposure, underwater clarity, etc.
    "narrativeCompleteness",   # does the clip show setup/event/outcome?
    "learningValue",          # evidence-backed personal performance/observation
    "investigationPotential",  # expected value of decoding more frames
    "uncertainty",             # explicit unresolved context
    "verifiedTaxonRarity",     # ONLY with independently verified taxon
)
WEIGHTS={
    "discovery":{
        "investigationPotential":.40,
        "eventSignificance":.30,
        "visualNovelty":.20,
        "uncertainty":.10,
    },
    "share":{
        "eventSignificance":.44,
        "visualNovelty":.16,
        "narrativeCompleteness":.14,
        "imageQuality":.10,
        "subjectVisibility":.11,
        "verifiedTaxonRarity":.05,
    },
    "learning":{
        "learningValue":.44,
        "eventSignificance":.23,
        "narrativeCompleteness":.19,
        "subjectVisibility":.14,
    },
}
# These are review-policy defaults, not empirically tuned clinical/biological
# thresholds. Train them on held-out human preferences before automatic use.
SHARE_REQUIRED=("eventSignificance","subjectVisibility",
                "imageQuality","narrativeCompleteness")
LEARN_REQUIRED=("learningValue","narrativeCompleteness")
DISCOVERY_REQUIRED=("investigationPotential","uncertainty")


def _numeric_signal(value,field):
    if value is None:
        return None
    if (type(value) not in (float,int)
            or not math.isfinite(value) or not 0<=value<=1):
        raise ValueError(f"{field} must be null or finite within [0,1]")
    return float(value)


def validate_moment(raw:dict) ->dict:
    if not isinstance(raw,dict):
        raise ValueError("Moment entry must be an object")
    ident=raw.get("momentId")
    if not isinstance(ident,str) or not 1<=len(ident)<=160:
        raise ValueError("momentId must be a nonempty bounded string")
    activity=raw.get("activity")
    if activity not in SPORTS:
        raise ValueError("activity must be snorkeling, ski or bike")
    aid=raw.get("assetId")
    if (not isinstance(aid,str) or len(aid)!=64
            or any(c not in "0123456789abcdef" for c in aid)):
        raise ValueError("assetId must be the indexed full lowercase SHA256")
    a,b=raw.get("startMs"),raw.get("endMs")
    if type(a) is not int or type(b) is not int or not 0<=a<b:
        raise ValueError("Moment requires a positive video-relative time window")
    if b-a>180_000:
        raise ValueError("Moment window cannot exceed 180 seconds")
    session=raw.get("sessionId")
    if session is not None and (not isinstance(session,str) or len(session)>160):
        raise ValueError("sessionId must be a bounded string or null")
    topic=raw.get("subjectGroup","unknown")
    if not isinstance(topic,str) or not 1<=len(topic)<=100:
        raise ValueError("Invalid subject group")
    if raw.get("humanReview") not in ("keep","skip","unreviewed",None):
        raise ValueError("humanReview must be keep, skip or unreviewed")
    taxonomy=raw.get("taxonomyStatus","unverified")
    if taxonomy not in ("unverified","verified_species","not_applicable"):
        raise ValueError("Invalid taxonomy status")
    evidence=raw.get("signals",{})
    if not isinstance(evidence,dict):
        raise ValueError("signals must be an object")
    if set(evidence)-set(SIGNALS):
        raise ValueError("Unrecognized signal keys")
    signals={name:_numeric_signal(evidence.get(name),name) for name in SIGNALS}
    verification=raw.get("verifiedTaxonEvidence")
    provenance_valid=(
        taxonomy=="verified_species" and isinstance(verification,dict)
        and verification.get("basis") in ("expert_review","curated_reference_review")
        and isinstance(verification.get("taxonId"),str)
        and 1<=len(verification["taxonId"])<=160
        and isinstance(verification.get("source"),str)
        and 3<=len(verification["source"])<=240
    )
    # A self-asserted "verified_species" field does not create verification.
    # Require a documented species-level review/referent to use rarity.
    if not provenance_valid:
        signals["verifiedTaxonRarity"]=None
    notes=raw.get("evidenceNotes",[])
    if (not isinstance(notes,list) or len(notes)>20
            or any(not isinstance(n,str) or len(n)>512 for n in notes)):
        raise ValueError("evidenceNotes must be short strings")
    return {
        "momentId":ident,"activity":activity,"assetId":aid,
        "startMs":a,"endMs":b,"sessionId":session,"subjectGroup":topic,
        "taxonomyStatus":taxonomy,
        "verifiedTaxonProvenanceProvided":provenance_valid,
        "humanReview":raw.get("humanReview") or "unreviewed",
        "signals":signals,
        "evidenceNotes":notes,
    }


def _weighted_score(signals:dict,axis:str,required:tuple[str,...]) ->float|None:
    if any(signals[k] is None for k in required):
        return None
    terms=[(w,signals[k]) for k,w in WEIGHTS[axis].items()
           if signals[k] is not None]
    denom=sum(w for w,_ in terms)
    if denom==0:
        return None
    return round(sum(w*v for w,v in terms)/denom,5)


def score_moment(moment:dict)->dict:
    m=validate_moment(moment)
    s=m["signals"]
    reasons=[]
    if m["taxonomyStatus"]!="verified_species":
        reasons.append("no_species_rarity_bonus_without_verification")
    if m["taxonomyStatus"]=="verified_species" and not m["verifiedTaxonProvenanceProvided"]:
        reasons.append("self_asserted_taxon_missing_review_provenance")
    elif m["taxonomyStatus"]=="verified_species" and s["verifiedTaxonRarity"] is None:
        reasons.append("verified_taxon_but_rarity_unassessed")
    if s["subjectVisibility"] is None:
        reasons.append("subject_visibility_unassessed")
    elif s["subjectVisibility"]<.35:
        reasons.append("low_subject_visibility_manual_review_needed")
    if s["imageQuality"] is None:
        reasons.append("image_quality_unassessed")
    if s["narrativeCompleteness"] is None:
        reasons.append("temporal_completeness_unassessed")
    if m["subjectGroup"]=="conflicting_labels_unverified":
        reasons.append("cross_label_detector_conflict_not_species_identity")
    missing=[k for k,v in s.items() if v is None]
    discovery=_weighted_score(s,"discovery",DISCOVERY_REQUIRED)
    share=_weighted_score(s,"share",SHARE_REQUIRED)
    learning=_weighted_score(s,"learning",LEARN_REQUIRED)
    if m["humanReview"]=="skip":
        reasons.append("explicit_human_skip_applies_to_share_selection")
    # Discoverability and publishability are fundamentally different. A poor
    # image can contain an excellent observation, but a subject invisible to
    # the viewer cannot be automatically proposed as a share-ready highlight.
    share_review_eligible=(
        share is not None and s["subjectVisibility"]>=.35 and
        s["narrativeCompleteness"]>=.35 and m["humanReview"]!="skip"
    )
    return {
        **{k:m[k] for k in ("momentId","activity","assetId","startMs","endMs",
                            "sessionId","subjectGroup","taxonomyStatus","humanReview")},
        "scores":{"discovery":discovery,"share":share,"learning":learning},
        "reviewEligibility":{
            "discovery":discovery is not None,
            "share":share_review_eligible,
            "learning":learning is not None,
            "autoPublish":False,
        },
        "missingSignalFields":missing,
        "taxonReviewProvenanceProvided":m["verifiedTaxonProvenanceProvided"],
        "evidenceNotes":m["evidenceNotes"],
        "reasonCodes":reasons,
        "signalsUsed":s,
        "rankingVersion":VERSION,
        "interpretation":"Heuristic review ordering, not calibrated model confidence",
    }


def _overlap_fraction(a:dict,b:dict)->float:
    if a["assetId"]!=b["assetId"]:
        return 0.
    intersection=max(0,min(a["endMs"],b["endMs"])-max(a["startMs"],b["startMs"]))
    union=max(a["endMs"],b["endMs"])-min(a["startMs"],b["startMs"])
    return intersection/union if union else 0.


def select_review_queue(scored:list[dict],*,axis:str="share",limit:int=20,
                        balance_sports:bool=True,max_per_asset:int=4)->list[dict]:
    """Diverse shortlist, not automatic acceptance or irreversible exclusion.

    Round-robin across represented sports before applying shared slot limit.
    This prevents inherently different visual dynamics from saturating a
    cross-sport reel before proper per-sport human-rating calibration exists.
    Within-sport ties are deterministic. Same-source temporal redundancy is
    suppressed only for the shortlist, never removed from the audit/report.
    """
    if axis not in WEIGHTS or type(limit) is not int or not 1<=limit<=200:
        raise ValueError("Unsupported review axis or limit")
    if type(max_per_asset) is not int or not 1<=max_per_asset<=30:
        raise ValueError("max_per_asset must be 1..30")
    buckets={a:[] for a in SPORTS}
    for x in scored:
        v=x["scores"][axis]
        if v is None or not x["reviewEligibility"][axis]:
            continue
        buckets[x["activity"]].append(x)
    for k,rows in buckets.items():
        buckets[k]=sorted(rows,key=lambda r:(-r["scores"][axis],r["assetId"],
                                              r["startMs"],r["momentId"]))
    if balance_sports:
        order=[]
        # Alternating sport exposure avoids comparing uncalibrated sports
        # as though they were one perfectly calibrated scalar axis.
        while any(buckets.values()):
            for sport in SPORTS:
                if buckets[sport]:
                    order.append(buckets[sport].pop(0))
    else:
        order=sorted((r for rows in buckets.values() for r in rows),
                     key=lambda r:(-r["scores"][axis],r["activity"],
                                   r["assetId"],r["startMs"]))
    chosen=[]
    per_asset=Counter()
    for c in order:
        if per_asset[c["assetId"]]>=max_per_asset:
            continue
        if any(_overlap_fraction(c,x)>=.60 for x in chosen):
            continue
        chosen.append(c)
        per_asset[c["assetId"]]+=1
        if len(chosen)>=limit:
            break
    return chosen


def rank_moments(raw:list[dict],*,limit:int=20,balance_sports:bool=True,
                 max_per_asset:int=4)->dict:
    if not isinstance(raw,list) or len(raw)>10000:
        raise ValueError("Input must be a bounded JSON list of moments")
    scored=[score_moment(x) for x in raw]
    ids=[x["momentId"] for x in scored]
    if len(set(ids))!=len(ids):
        raise ValueError("Duplicate momentId not permitted")
    queues={axis:select_review_queue(scored,axis=axis,limit=limit,
                                    balance_sports=balance_sports,
                                    max_per_asset=max_per_asset)
            for axis in ("discovery","share","learning")}
    status=Counter(("share_review_eligible" if x["reviewEligibility"]["share"]
                    else "needs_more_review") for x in scored)
    return {
        "schemaVersion":1,"version":VERSION,
        "inputCount":len(raw),"rankedCount":len(scored),
        "balancedSportExposure":balance_sports,
        "counts":dict(sorted(status.items())),
        "queues":{name:[x["momentId"] for x in values]
                  for name,values in queues.items()},
        "moments":scored,
        "limitations":[
            "Heuristic review ordering only, not a calibrated cross-sport score.",
            "Missing measurements stay null; missing animal data is not absence.",
            "Species rarity contributes only with verified species-level labels.",
            "Murky but visually meaningful wildlife may deserve investigation.",
            "Minimum visibility gate prevents auto-proposing invisible public highlights.",
            "No algorithm automatically accepts, exports or publishes moments.",
            "High-similarity imagery never proves the same wildlife individual.",
        ],
    }


def from_event_context(doc:dict)->list[dict]:
    """Adapter for existing event-context manifest: no invented quality scores.

    Event context only contains detector proposals and uncalibrated vectors;
    all important visibility/rarity/technique fields remain null until
    qualified by expert/human review or validated models.
    """
    if (not isinstance(doc,dict) or doc.get("version")!="event-context-embedding-v1"
            or not isinstance(doc.get("assetId"),str)
            or not isinstance(doc.get("events"),list)):
        raise ValueError("Expected versioned event-context manifest")
    out=[]
    for event in doc["events"]:
        out.append({
            "momentId":f'{doc["assetId"]}:{event["id"]}',
            "activity":"snorkeling","assetId":doc["assetId"],
            "startMs":event["startMs"],"endMs":event["endMs"],
            "subjectGroup":event.get("group","unknown"),
            "taxonomyStatus":"unverified","humanReview":"unreviewed",
            "signals":{},  # uncalibrated similarity is not event significance
            "evidenceNotes":[
                "Imported detector proposal; quantitative review scores unknown.",
                "Review complete source window and check cross-label hypotheses.",
            ],
        })
    return out



def _review_html(report:dict)->str:
    by_id={m["momentId"]:m for m in report["moments"]}
    sections=[]
    for axis,title in (("discovery","Investigate"),
                       ("share","Share review"),("learning","Technique and learning")):
        cards=[]
        for ident in report["queues"][axis]:
            m=by_id[ident]
            labels={
                "discovery":"Priority for additional investigation",
                "share":"Not publication approval",
                "learning":"Learning hypothesis for review",
            }
            missing=", ".join(m["missingSignalFields"]) or "No missing signals"
            reasons=", ".join(m["reasonCodes"]) or "No risk flags"
            cards.append(
                '<article><strong>'+escape(m["momentId"])+'</strong>'
                f'<p>{escape(m["activity"])} · {m["startMs"]/1000:.1f}–'
                f'{m["endMs"]/1000:.1f}s · {m["scores"][axis]:.3f}</p>'
                f'<small>{escape(labels[axis])}. Missing: {escape(missing)}. '
                f'Flags: {escape(reasons)}.</small></article>')
        sections.append(
            '<section><h2>'+title+'</h2>'
            +(''.join(cards) if cards else
              '<p>No eligible scored moments. Review missing evidence below.</p>')
            +'</section>')
    unscored=[m for m in report["moments"]
              if not any(m["reviewEligibility"].values())]
    pending=''.join(
        '<article><strong>'+escape(x["momentId"])+'</strong>'
        '<p>Unscored proposal; no unsupported quality or species assertions.</p>'
        '<small>Need: '+escape(", ".join(x["missingSignalFields"]))+
        '</small></article>' for x in unscored)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Klip · Evidence-aware moment ranking</title><style>'
        'body{font:14px system-ui;background:#101923;color:#eaf0f4;'
        'max-width:1200px;margin:26px auto;padding:0 16px;line-height:1.45}'
        'h1{font-size:1.6rem}h2{font-size:1.1rem;color:#f1bcb2}'
        'p,small{color:#b3c2cd}main{display:grid;'
        'grid-template-columns:repeat(auto-fit,minmax(min(100%,310px),1fr));gap:18px}'
        'article{background:#1b2b37;border:1px solid #334454;border-radius:12px;'
        'padding:12px;margin-bottom:10px}article strong{overflow-wrap:anywhere}'
        'small{display:block}</style></head><body>'
        '<h1>Moment triage</h1><p>Uncalibrated review queues, not decisions to '
        'publish or verified animal identifications. Exceptional encounters '
        'can remain important even with poor clarity, but invisible subjects '
        'are not automatically selected for sharing.</p>'
        '<main>'+''.join(sections)+'</main><h2>Evidence still needed</h2>'
        +pending+'</body></html>')


def create_rank_report(workspace:Path,input_file:Path,output_dir:Path,*,
                       review_file:Path|None=None,limit:int=20,
                       balance_sports:bool=True)->dict:
    """Read existing event packets or bounded moment features. Never decode."""
    from .catalog import connect
    from .safety import protect_source_output
    workspace=Path(workspace).expanduser().resolve()
    input_file=Path(input_file).expanduser().resolve()
    target=Path(output_dir).expanduser().resolve()
    if (target==workspace or not target.is_relative_to(workspace)
            or target.relative_to(workspace).parts[0] not in ("reports","exports")
            or target.exists()):
        raise ValueError("Ranking output must be a new workspace reports/ or exports/ folder")
    with connect(workspace) as db:
        protect_source_output(db,target/"ranking.json")
        protect_source_output(db,target/"review.html")
    def read_json(path):
        if not path.is_file() or path.stat().st_size>20_000_000:
            raise ValueError("Ranking input must be a bounded JSON file")
        return json.loads(path.read_text(encoding="utf-8"))
    payload=read_json(input_file)
    if isinstance(payload,dict) and payload.get("version")=="event-context-embedding-v1":
        raw=from_event_context(payload)
    elif isinstance(payload,list):
        raw=payload
    elif isinstance(payload,dict) and isinstance(payload.get("moments"),list):
        raw=payload["moments"]
    else:
        raise ValueError("Expected event-context manifest or moment list")
    if review_file is not None:
        reviews=read_json(Path(review_file).expanduser().resolve())
        if not isinstance(reviews,list):
            raise ValueError("Review overrides must be a JSON list")
        overrides={}
        for row in reviews:
            if (not isinstance(row,dict)
                    or not isinstance(row.get("momentId"),str)
                    or set(row)-{"momentId","signals","taxonomyStatus",
                               "humanReview","evidenceNotes"}):
                raise ValueError("Invalid review override")
            ident=row["momentId"]
            if ident in overrides:
                raise ValueError("Repeated moment review override")
            overrides[ident]=row
        if set(overrides)-{x.get("momentId") for x in raw if isinstance(x,dict)}:
            raise ValueError("Review refers to an absent moment")
        augmented=[]
        for m in raw:
            patch=overrides.get(m.get("momentId"))
            if not patch:
                augmented.append(m)
                continue
            changes={k:v for k,v in patch.items() if k!="momentId"}
            merged={**m,**changes}
            if "signals" in changes:
                merged["signals"]={**m.get("signals",{}),**changes["signals"]}
            augmented.append(merged)
        raw=augmented
    report=rank_moments(raw,limit=limit,balance_sports=balance_sports)
    report["inputFile"]=str(input_file)
    report["reviewOverridesUsed"]=review_file is not None
    report["sourceVideosDecoded"]=0
    report["modelInferenceCalls"]=0
    target.mkdir(parents=True,exist_ok=False)
    with (target/"ranking.json").open("x",encoding="utf-8") as handle:
        handle.write(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+"\n")
    with (target/"review.html").open("x",encoding="utf-8") as handle:
        handle.write(_review_html(report))
    return {
        "status":"review_only","momentCount":len(raw),
        "queues":report["queues"],"sourceVideosDecoded":0,
        "ranking":str(target/"ranking.json"),"reviewHtml":str(target/"review.html"),
        "note":"Unknown event significance or rare species remains unscored until validated.",
    }

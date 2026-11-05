"""No-media pretraining data preflight for Action Highlight Curation v1.

This is a structural and provenance risk gate, NOT an audiovisual validator,
legal opinion, annotator audit or model trainer. It never opens source media.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
import json
import re

VERSION="klip-highlight-curation-record-v1"
_MANIFEST="klip-highlight-curation-manifest-v1"
_SHA=re.compile(r"^[0-9a-f]{64}$")
_ALLOWED={"research_reference_only","pending","approved_for_training","revoked"}
_PARTITIONS={"unassigned","train","validation","test"}
_OUTCOMES={"completed","failed","partial","not_applicable","unknown"}
_VISIBILITY={"visible","partially_visible","not_visible","ambiguous"}
_PHASES={"context","preparation","initiation","action_peak","resolution","aftermath"}
_OBSERVER={"athlete","expert_coach","casual_spectator","filmmaker","editor",
           "nature_specialist","safety_reviewer","participant","other"}


def _integer(value) -> bool:
    return type(value) is int


def _span(start,end,limit:int,what:str) -> None:
    if (not _integer(start) or not _integer(end)
            or not 0<=start<end<=limit):
        raise ValueError(f"Invalid {what} timestamps or order")


def _nonempty(value,what:str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{what} must be a nonempty identifier")
    return value


def audit_highlight_curation_manifest(path:Path)->dict:
    """Validate strict source grouping and explicit rights for supplied JSON.

    Does not infer that a boolean permission flag is a real permission grant.
    Real legal chain-of-title and original video hash must be separately
    checked before media processing or training.
    """
    path=Path(path).expanduser().resolve()
    if not path.is_file() or path.stat().st_size>10_000_000:
        raise ValueError("No curation manifest or exceeds 10 MB")
    try:
        data=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,UnicodeError,ValueError) as exc:
        raise ValueError("Invalid JSON curation manifest") from exc
    if (not isinstance(data,dict) or data.get("version")!=_MANIFEST
            or not isinstance(data.get("records"),list)
            or len(data["records"])>5000):
        raise ValueError("Expected bounded versioned curation manifest")
    seen=set()
    source_partitions={}
    outing_partitions={}
    states=Counter()
    sports=Counter()
    observer_roles=Counter()
    has_human=0
    unknown_outcomes=0
    for idx,row in enumerate(data["records"]):
        if not isinstance(row,dict) or row.get("version")!=VERSION:
            raise ValueError(f"Invalid curation record version at {idx}")
        rid=_nonempty(row.get("recordId"),"recordId")
        if rid in seen:
            raise ValueError(f"Duplicate recordId {rid}")
        seen.add(rid)
        source=row.get("source")
        event=row.get("event")
        rights=row.get("rights")
        split=row.get("split")
        sport=row.get("sportContext")
        filming=row.get("filming")
        reviews=row.get("observerJudgments")
        reactions=row.get("reactions")
        status=row.get("evidenceStatus")
        if any(not isinstance(x,dict) for x in
               (source,event,rights,split,sport,filming,status)):
            raise ValueError(f"Missing structured source/event/rights at {rid}")
        if not isinstance(reviews,list) or not isinstance(reactions,list):
            raise ValueError(f"Invalid observer/reaction collections at {rid}")
        aid=source.get("assetSha256")
        if not isinstance(aid,str) or not _SHA.fullmatch(aid):
            raise ValueError(f"Invalid source SHA at {rid}")
        family=_nonempty(source.get("sourceFamilyId"),"source family")
        outing=_nonempty(source.get("outingGroupId"),"outing group")
        if (family!=split.get("sourceFamilyId")
                or outing!=split.get("outingGroupId")):
            raise ValueError(f"Frozen group IDs differ for {rid}")
        partition=split.get("partition")
        if partition not in _PARTITIONS:
            raise ValueError(f"Invalid partition for {rid}")
        for index,groups,name in ((family,source_partitions,"source family"),
                                  (outing,outing_partitions,"outing")):
            past=groups.get(index)
            if past is not None and past!=partition:
                raise ValueError(
                    f"Train/val/test leakage across {name} {index}: {past} vs {partition}")
            groups[index]=partition
        duration=source.get("sourceDurationMs")
        if not _integer(duration) or duration<1000:
            raise ValueError(f"Unknown/invalid video duration for {rid}")
        _span(event.get("startMs"),event.get("endMs"),duration,"event")
        peak=event.get("peakMs")
        if peak is not None and (
                not _integer(peak) or not event["startMs"]<=peak<event["endMs"]):
            raise ValueError(f"Event peak outside interval for {rid}")
        outcome=event.get("observedOutcome")
        if outcome not in _OUTCOMES:
            raise ValueError(f"Invalid observed outcome for {rid}")
        if (outcome in ("completed","failed")
                and event.get("outcomeVisible")!="yes"):
            raise ValueError(
                f"Observed outcome cannot be definite with invisible/ambiguous resolution at {rid}")
        unknown_outcomes+=outcome=="unknown"
        phases=event.get("phases")
        if not isinstance(phases,list) or not 1<=len(phases)<=20:
            raise ValueError(f"Expected bounded event phases for {rid}")
        for phase in phases:
            if (not isinstance(phase,dict)
                    or phase.get("phase") not in _PHASES
                    or phase.get("visibility") not in _VISIBILITY):
                raise ValueError(f"Invalid phase visibility for {rid}")
            _span(phase.get("startMs"),phase.get("endMs"),
                  duration,"phase")
            if (phase["startMs"]<event["startMs"]
                    or phase["endMs"]>event["endMs"]):
                raise ValueError(f"Phase outside event interval for {rid}")
        if not isinstance(sport.get("sport"),str):
            raise ValueError(f"Sport unrecorded for {rid}")
        sports[sport["sport"]]+=1
        if (filming.get("technicalQuality") is not None
                and (not _integer(filming["technicalQuality"])
                     or not 1<=filming["technicalQuality"]<=5)):
            raise ValueError(f"Technical quality outside human rubric for {rid}")
        if (filming.get("aestheticQuality") is not None
                and (not _integer(filming["aestheticQuality"])
                     or not 1<=filming["aestheticQuality"]<=5)):
            raise ValueError(f"Aesthetic quality outside human rubric for {rid}")
        if (filming.get("eventReadability") is not None
                and (not _integer(filming["eventReadability"])
                     or not 1<=filming["eventReadability"]<=5)):
            raise ValueError(f"Event readability outside human rubric for {rid}")
        for review in reviews:
            if (not isinstance(review,dict)
                    or review.get("role") not in _OBSERVER
                    or type(review.get("blindActionOnly")) is not bool):
                raise ValueError(f"Invalid observer perspective for {rid}")
            observer_roles[review["role"]]+=1
            if review.get("reviewStatus")=="human_verified":
                has_human+=1
        for reaction in reactions:
            if not isinstance(reaction,dict):
                raise ValueError(f"Invalid reaction at {rid}")
            start,end=reaction.get("sourceStartMs"),reaction.get("sourceEndMs")
            if reaction.get("mappingStatus")=="human_verified":
                _span(start,end,duration,"reaction-to-source map")
                if (start<event["startMs"] or end>event["endMs"]):
                    raise ValueError(f"Reaction mapping outside event for {rid}")
            elif start is not None or end is not None:
                # Unverified mappings may be hypotheses, but must be bounded.
                _span(start,end,duration,"unverified reaction-to-source map")
        rights_state=rights.get("rightsStatus")
        if rights_state not in _ALLOWED:
            raise ValueError(f"Invalid rights status at {rid}")
        states[rights_state]+=1
        if rights_state=="approved_for_training":
            required=("sourceAccessAuthorized","sourceTrainingAuthorized",
                      "embeddedMediaTrainingAuthorized","audioTrainingAuthorized",
                      "speakerVoiceConsent")
            if (any(rights.get(k) is not True for k in required)
                    or rights.get("revocationState")!="active"
                    or not isinstance(rights.get("sourcePermissionRecordId"),str)
                    or len(rights["sourcePermissionRecordId"])<8):
                raise ValueError(f"Training permission fields incomplete at {rid}")
            if (partition=="unassigned" or
                    not status.get("sourceSha256Reverified") or
                    not status.get("humanEventAnnotationFrozen") or
                    not status.get("humanRatingsFrozen") or
                    not status.get("modelHypothesesStoredSeparately")):
                raise ValueError(f"Training evidence/split gates incomplete at {rid}")
    return {
        "version":"klip-highlight-curation-preflight-v1",
        "records":len(seen),
        "sources":len(source_partitions),"outings":len(outing_partitions),
        "splitMembershipSourceOutingChecked":True,
        "rightsStatuses":dict(sorted(states.items())),
        "structurallyEligibleButRightsNotIndependentlyVerified":
            states["approved_for_training"],
        "humanVerifiedOpinionRecords":has_human,
        "unknownOutcomeRecords":unknown_outcomes,
        "sports":dict(sorted(sports.items())),
        "observerRoles":dict(sorted(observer_roles.items())),
        "mediaReadCount":0,"modelCalls":0,"modelsTrained":0,
        "legalPermissionIndependentlyVerified":False,
        "sourceHashesIndependentlyReverified":False,
        "trainingIsAuthorizedByThisTool":False,
        "nextAction":"Independent rights/document/source verification and annotator adjudication before any training. "
                     "Structural eligibility is NOT legal or scientific readiness.",
    }

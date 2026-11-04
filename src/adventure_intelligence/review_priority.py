"""Bounded editorial investigation ordering for uncalibrated local AI suggestions.

This is *not* a publishability estimate, event probability, or human approval.
A high score only means "inspect this first". Missing evidence abstains.
Full clips and independent controls must remain available in the queue.
"""
from __future__ import annotations

WEIGHTS={
    "eventInterest":3.0,
    "subjectVisibility":2.0,
    "narrativePotential":1.4,
    "sharingPotential":1.3,
    "learningPotential":1.3,
    "visualQuality":0.3,
}
REQUIRED=("eventInterest","subjectVisibility")


def investigation_priority(ratings:dict)->dict:
    if not isinstance(ratings,dict):
        return {"investigationScore":None,
                "assessedDimensions":0,"scoreMeaning":"unassessed"}
    observed={k:v for k,v in ratings.items()
              if k in WEIGHTS and type(v) is int and 0<=v<=3}
    if len(observed)<4 or any(k not in observed for k in REQUIRED):
        return {"investigationScore":None,
                "assessedDimensions":len(observed),
                "scoreMeaning":"not_enough_frame_evidence"}
    denominator=sum(WEIGHTS[k] for k in observed)
    score=round(100*sum(WEIGHTS[k]*v for k,v in observed.items())/
                (3*denominator))
    return {
        "investigationScore":score,"assessedDimensions":len(observed),
        "scoreMeaning":"uncalibrated_review_order_not_event_probability",
        "scoreBasis":{
            "eventInterest":observed["eventInterest"],
            "subjectVisibility":observed["subjectVisibility"],
            "ambientClarityExcluded":True,
            "verifiedRarityBonus":0,
        },
    }

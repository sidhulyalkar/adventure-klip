"""Rank source-verified playable proxy clips without inventing model quality.

A reviewed complete run is a stronger tier of evidence than seven sampled VLM
stills. VLM ranks are REVIEW OPPORTUNITIES only, not human quality scores.
Always preserve rejected clips as coverage controls, never as negatives.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from html import escape
import json
import math

VERSION = "klip-full-preview-ranked-review-v2"
SPORTS = ("ski", "snorkeling", "bike")
# Scarce, semantically concrete categories aid navigation, but are hypotheses.
WEIGHTS = {
    "ski_jump": 3.0, "ski_rail": 3.2, "ski_box": 3.2,
    "ski_creative": 2.5, "ski_moguls": 1.9, "ski_trees": 1.8,
    "ski_speed": 0.9, "ski_park": 0.2,
    "marine_ray": 2.1, "marine_fish": 1.7,
    "marine_wildlife": 2.4, "marine_underwater_passage": 1.8,
    "marine_reef": 1.2, "marine_seagrass": 1.4,
    "marine_scenic": 0.3,
    "riding_general": 0.5,
}


def review_opportunity(proposal: dict, category_counts: dict,
                       sport_counts: dict) -> dict:
    """Transparent heuristic; does not score visual storytelling or difficulty."""
    sport = proposal["activity"]
    source_n = max(1, sport_counts.get(sport, 1))
    terms = []
    for item in proposal["categories"]:
        tag = item["tag"]
        base = WEIGHTS.get(tag, 0.25)
        prevalence = max(1, category_counts.get(tag, 1))
        # Contrast within the proposed activity, to avoid rewarding the
        # classifier's universal 'ski_park' and 'marine_scenic' guesses.
        rarity = math.log1p(source_n / prevalence)
        support = 0.80 + 0.20 * len(item["frames"]) / 7
        value = round(base * rarity * support, 5)
        terms.append({
            "tag": tag, "proposedFrames": item["frames"],
            "withinActivityPrevalence": prevalence,
            "withinActivityPopulation": source_n,
            "heuristicContribution": value,
        })
    terms.sort(key=lambda t: (-t["heuristicContribution"], t["tag"]))
    values=[t["heuristicContribution"] for t in terms]
    score = round(
        sum(values[:2]) + 0.2*sum(values[2:]) +
        (0.2 if proposal["eventContinuity"]=="visually_plausible" else 0),
        5
    )
    return {
        "reviewOpportunityScore": score,
        "signals": terms,
        "scoreBasis": "scarce_specific_model_suggestions_not_validated_event_quality",
        "qualityFlagsUsedToPenalize": False,
        "modelPredictionsHumanVerified": False,
        "rankingIsFinalHighlightQuality": False,
    }


def candidates(rows: list[dict], receipts: dict[str,dict]) -> list[dict]:
    """All successful observations remain traceable to one SHA-verified proxy."""
    counter=Counter()
    sports=Counter()
    for row in rows:
        rec=receipts[row["recordId"]]
        if rec["status"]!="done":
            continue
        p=rec["analysis"]["proposal"]
        sports[p["activity"]]+=1
        counter.update(t["tag"] for t in p["categories"])
    result=[]
    for row in rows:
        rec=receipts[row["recordId"]]
        if rec["status"]!="done":
            continue
        p=rec["analysis"]["proposal"]
        opportunity=review_opportunity(p,counter,sports)
        result.append({
            "id":row["recordId"], "origin":"autoscout_seven_stills",
            "activity":p["activity"],"sourceSha256":row["sourceSha256"],
            "sourceStartMs":row["sourceStartMs"],"sourceEndMs":row["sourceEndMs"],
            "proxySha256":row["proxySha256"],"previewRecordId":row["recordId"],
            "ownerReviewed":False,"humanKeepConfirmed":False,
            "modelStatus":"done","reviewPriority":p["reviewPriority"],
            "categoryProposals":[t["tag"] for t in p["categories"]],
            "qualityFlagProposals":[t["kind"] for t in p["qualityFlags"]],
            "modelUncertaintyText":p["uncertainty"],
            "modelEventContinuity":p["eventContinuity"],
            "heuristic":opportunity,
            "candidateTitle":"Potential "+p["activity"]+" scene",
            "limitations":["Seven frame stills cannot prove maneuvers, species, or story quality.",
                           "20-second proxy can omit important before/after context."],
        })
    return result


def reviewed_run_groups(owner: dict, edit_plan: dict) -> list[dict]:
    """Owner-backed groups already checked against the full source run plan."""
    if (owner.get("preference")!="keep"
            or owner.get("reviewerVerifiedContinuousRun") is not True):
        return []
    groups=edit_plan["alternatives"]["balanced"]["groups"]
    if not groups:
        raise ValueError("Verified run cannot produce empty groups")
    source=owner["sourceAssetSha256"]
    if edit_plan["sourceAssetSha256"]!=source:
        raise ValueError("Owner and edit source identities differ")
    result=[]
    for group in groups:
        kind=group["role"]
        result.append({
            "id":"owner-"+sha256(
                (source+"|"+str(group["sourceStartMs"])+"|"
                 +str(group["sourceEndMs"])).encode()).hexdigest()[:24],
            "origin":"human_reviewed_run_tentative_edit",
            "activity":"ski","sourceSha256":source,
            "sourceStartMs":group["sourceStartMs"],
            "sourceEndMs":group["sourceEndMs"],
            "ownerReviewed":True,"humanKeepConfirmed":True,
            "ownerGroupingConfirmed":False, # Cut points are suggestions.
            "runBoundsHumanVerified":True,
            "modelStatus":"not_applicable",
            "reviewPriority":"owner_run_keep",
            "categoryProposals":[],
            "qualityFlagProposals":[],
            "candidateTitle":(
                "Three-feature ski line" if kind=="main_line_candidate"
                else "Standalone ski feature" if len(group["features"])==1
                else "Linked ski features"
            ),
            "featureTimesMs":[x["sourceMs"] for x in group["features"]],
            "featureKinds":[x["kind"] for x in group["features"]],
            "role":kind,
            "postRollShortfallMs":group["postRollShortfallMs"],
            "heuristic":None,
            "limitations":[
                "Human verified run, but tentative edit boundaries are not native-PTS aligned.",
                "Trick quality, takeoff, landing and completion not independently scored.",
            ],
        })
    return result


def quarantine_model_candidates(raw:list[dict], *,min_duration_ms:int=6000)->list[dict]:
    """'Needs context' is NOT a negative human preference."""
    result=[]
    for item in raw:
        if item["origin"]!="autoscout_seven_stills":
            continue
        reasons=[]
        if item["activity"] not in ("ski","snorkeling","bike"):
            reasons.append("other_or_unknown_activity")
        if (item["sourceEndMs"]-item["sourceStartMs"]<min_duration_ms
                and not item.get("humanKeepConfirmed",False)):
            reasons.append("under_six_seconds_unreviewed")
        if item.get("humanReviewDecision")=="skip":
            reasons.append("explicit_owner_skip")
        if reasons:
            result.append({
                "recordId":item["id"],"sourceSha256":item["sourceSha256"],
                "sourceStartMs":item["sourceStartMs"],
                "sourceEndMs":item["sourceEndMs"],
                "activityProposal":item["activity"],
                "reasons":reasons,"noOriginalFootageDiscarded":True,
                "notAnAutomaticNegativeLabel":True,
            })
    return result


def rank_review_queue(raw:list[dict], *,top:int=24,per_sport:int|None=None,
                      snorkeling_target_share:float=0.60,max_bike:int=2)->dict:
    """Human Kept first, then snorkeling-heavy source-balanced review leads.

    Ranking is editorial triage, never calibrated animal/trick truth.
    """
    if type(top) is not int or not 1<=top<=150:
        raise ValueError("top must be 1..150")
    if per_sport is not None and (type(per_sport) is not int or not 1<=per_sport<=150):
        raise ValueError("Invalid sport cap")
    if (type(snorkeling_target_share) not in (int,float)
            or not math.isfinite(snorkeling_target_share)
            or not 0<=snorkeling_target_share<=1):
        raise ValueError("Invalid snorkeling target")
    if type(max_bike) is not int or not 0<=max_bike<=150:
        raise ValueError("Invalid bike cap")
    max_bike=min(max_bike,top)
    quarantined=quarantine_model_candidates(raw)
    excluded={x["recordId"] for x in quarantined}
    owner=sorted(
        [x for x in raw if x["ownerReviewed"] and x["humanKeepConfirmed"]
         and x["activity"] in ("ski","snorkeling","bike")
         and x["id"] not in excluded],
        key=lambda x:(
            # The reviewed ski line stays first, but an ordinary, personally
            # meaningful reef scene can outrank a poorly filmed animal sighting.
            0 if x["origin"]=="human_reviewed_run_tentative_edit" else 1,
            x.get("role")!="main_line_candidate",
            -((x.get("humanSceneInterpretation") or {}).get("sceneInterest")
              if (x.get("humanSceneInterpretation") or {}).get("sceneInterest") is not None
              else -1),
            -(x.get("humanMarineQualityScore") if
              x.get("humanMarineQualityScore") is not None else -1),
            -len(x.get("featureTimesMs",[])),
            x["sourceSha256"],x["sourceStartMs"],
        ),
    )
    owner_sources={x["sourceSha256"] for x in owner}
    proposals=sorted(
        [x for x in raw if x["origin"]=="autoscout_seven_stills"
         and x["id"] not in excluded
         and x["sourceSha256"] not in owner_sources],
        key=lambda x:(-x["heuristic"]["reviewOpportunityScore"],
                      x["sourceSha256"],x["sourceStartMs"],x["id"]),
    )
    selected=[]; seen=set(); counts=Counter()
    for x in owner:
        if len(selected)>=top:
            break
        if per_sport is not None and counts[x["activity"]]>=per_sport:
            continue
        if x["activity"]=="bike" and counts["bike"]>=max_bike:
            continue
        selected.append(x);seen.add(x["sourceSha256"]);counts[x["activity"]]+=1
    groups=defaultdict(list)
    for x in proposals:
        groups[x["activity"]].append(x)
    def pop_sport(sport):
        if per_sport is not None and counts[sport]>=per_sport:
            return None
        if sport=="bike" and counts["bike"]>=max_bike:
            return None
        q=groups[sport]
        while q and q[0]["sourceSha256"] in seen:
            q.pop(0)
        return q.pop(0) if q else None
    target=math.ceil(top*snorkeling_target_share)
    if per_sport is not None:
        target=min(target,per_sport)
    while len(selected)<top and counts["snorkeling"]<target:
        x=pop_sport("snorkeling")
        if x is None:
            break
        selected.append(x);seen.add(x["sourceSha256"]);counts["snorkeling"]+=1
    while len(selected)<top:
        chosen=None
        for sport in sorted(("ski","snorkeling","bike"),key=lambda s:(
                counts[s], {"ski":0,"snorkeling":1,"bike":2}[s])):
            chosen=pop_sport(sport)
            if chosen is not None:
                break
        if chosen is None:
            break
        selected.append(chosen)
        seen.add(chosen["sourceSha256"])
        counts[chosen["activity"]]+=1
    ranked=[]
    for i,item in enumerate(selected,1):
        tier=("human_reviewed_complete_run" if item["origin"]=="human_reviewed_run_tentative_edit"
              else "human_confirmed_visible_animal_keep" if item.get("humanConfirmedVisibleAnimal")
              else "human_kept_underwater_scene" if item["ownerReviewed"]
              else "model_only_review_opportunity")
        ranked.append({**item,"rank":i,"rankClass":tier})
    return {
        "top":ranked,"quarantined":quarantined,
        "quarantineCounts":dict(sorted(Counter(
            reason for x in quarantined for reason in x["reasons"]
        ).items())),
        "mainGalleryOtherUnknownCount":sum(
            x["activity"] not in ("ski","snorkeling","bike") for x in ranked),
        "mainGalleryUnreviewedUnderSixSeconds":sum(
            not x["humanKeepConfirmed"] and
            x["sourceEndMs"]-x["sourceStartMs"]<6000 for x in ranked),
        "snorkelingTarget":target,"snorkelingSelected":counts["snorkeling"],
        "maxBike":max_bike,"availableModelCandidates":len(proposals),
        "ownerApprovedRunCandidateGroups":sum(
            x["origin"]=="human_reviewed_run_tentative_edit" for x in owner),
        "modelOnlyCandidatesInTop":sum(not x["ownerReviewed"] for x in ranked),
        "humanReviewedCandidatesInTop":sum(x["ownerReviewed"] for x in ranked),
        "sportMix":dict(sorted(Counter(x["activity"] for x in ranked).items())),
        "scoreNotCalibrated":True,"ranksDoNotProveBestFootage":True,
    }


def gallery_html(report:dict)->str:
    """Static offline gallery with each MP4 already local, no network scripts."""
    cards=[]
    for item in report["topRankedClips"]:
        badges=(
            "Human-reviewed ski run" if item.get("origin")=="human_reviewed_run_tentative_edit"
            else "Human-confirmed visible animal" if item.get("humanConfirmedVisibleAnimal")
            else "Human-kept underwater scene" if item["ownerReviewed"]
            else "Unverified model candidate"
        )
        title=f'#{item["rank"]} · '+item["candidateTitle"]
        label=(f'Clip: {item["sourceStartMs"]/1000:.3f}–'
               f'{item["sourceEndMs"]/1000:.3f}s | '
               f'Source {item["sourceSha256"][:12]}')
        if item.get("origin")=="human_reviewed_run_tentative_edit":
            evidence="Features: "+", ".join(str(t/1000)+"s" for t in item["featureTimesMs"])
        elif item.get("humanMarineReview") is not None:
            human=item["humanMarineReview"]
            evidence=("Owner saw: "+human["animalPresence"]+" / "+
                      human["animalType"]+"; behavior: "+human["behavior"]+
                      "; human filming score: "+str(item.get("humanMarineQualityScore")))
        else:
            evidence=("Suggested tags: "+(", ".join(item["categoryProposals"]) or "none")
                      +". Tags are NOT ground truth.")
        owner_scene=item.get("humanSceneInterpretation") or {}
        story_notes=[]
        for field,title in (("sceneDescription","Scene description"),
                            ("whyKeep","Owner's reason")):
            value=owner_scene.get(field)
            if isinstance(value,str) and value.strip():
                story_notes.append("<p><strong>"+escape(title)+":</strong> "+
                                   escape(value)+"</p>")
        if owner_scene.get("storyRole") not in (None,"unassigned"):
            story_notes.append(
                "<p class='muted'>Story role: "+
                escape(owner_scene["storyRole"].replace("_"," "))+"</p>"
            )
        cards.append(
            "<article><video controls preload='metadata' playsinline src='media/"
            +escape(item["mediaFile"])+"'></video><h2>"+escape(title)
            +"</h2><strong>"+escape(badges)+"</strong><p>"+escape(label)
            +"</p><p>"+escape(evidence)
            +"</p><p class='muted'>"+escape(item.get("rankExplanation",""))
            +"</p>"+"".join(story_notes)+"</article>"
        )
    return """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; media-src 'self' file:; style-src 'unsafe-inline'; connect-src 'none'; script-src 'none'; img-src 'none'">
<title>Klip / Verified Top Clips Review</title><style>
:root{color-scheme:dark;background:#10181e;color:#eaf4f3;font:14px/1.5 system-ui}
body{max-width:1260px;margin:auto;padding:24px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:16px}
h1{font-size:26px}h2{font-size:17px}article{border:1px solid #3a535b;background:#1b2a33;padding:14px;border-radius:12px}
video{width:100%;aspect-ratio:16/9;background:black;border-radius:9px}
.muted{color:#adc2c8}.notice{padding:13px;border-left:3px solid #84caba;background:#233740}
</style></head><body><h1>Klip / Top Clips</h1>
<p class='notice'>Verified playable proxy footage, not a full-library quality ranking.
Human-reviewed run edits are prioritized. Other rankings are provisional
research leads based on sparse model suggestions. All footage requires review
before a final master edit. No original DJI footage was accessed here.</p>
<div class="grid">"""+"\n".join(cards)+"</div></body></html>"

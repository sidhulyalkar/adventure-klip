"""Frozen, source-referenced clip-value reviews for the first archive pilot.

The packet contains a balanced mix of real candidate episode hypotheses,
scheduled reconnaissance and deterministic random controls. A random control
is NOT a negative label. Build and score without decoding original videos,
or declaring wildlife identity, skill, rarity, or audience preference.
"""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
import json
import math

from .adaptive_sampling import validate_plan
from .archive_status import _validate_campaign, _read_json
from .catalog import connect
from .moment_ranking import rank_moments
from .safety import protect_source_output

VERSION="clip-value-lab-v1"
CRITERIA={
    "eventSignificance":"How meaningful was the OBSERVED action or encounter?",
    "subjectVisibility":"Is the main animal, rider, feature, or action actually legible?",
    "ambientClarity":"How clear are the surroundings (water, fog, snow, light)?",
    "imageQuality":"Framing, exposure, sharpness and stability of this camera shot",
    "narrativeCompleteness":"Does the clip contain approach, event and outcome?",
    "actionOutcome":"Can you see the action/encounter happen and conclude?",
    "visualNovelty":"How different is this from other clips in your archive?",
    "learningValue":"How actionable is the observation or technique feedback?",
    "personalMeaning":"How memorable or meaningful is this experience to you?",
    "audienceAppeal":"Would someone unfamiliar with the outing enjoy this?",
    "watchAgain":"Would you want to revisit this moment?",
    "investigationPotential":"Could deeper analysis reveal a valuable missed event?",
    "uncertainty":"How much important evidence is unresolved or obscured?",
}
SCALE={
    "0":"None / very poor",
    "1":"Weak",
    "2":"Some",
    "3":"Strong",
    "4":"Exceptional",
}
OUTCOMES=("observed","not_observed","uncertain")
DECISIONS=("keep","skip","later")
_MAX_PACKET=240
_SHA=set("0123456789abcdef")


def _hash(data)->str:
    return sha256(json.dumps(data,sort_keys=True,separators=(",",":"),
                             allow_nan=False).encode()).hexdigest()


def _moment(asset_id,start,end,source,group,kind,plan_id,confirmed):
    mid="mv-"+sha256(f"{plan_id}:{asset_id}:{start}:{end}:{source}".encode()).hexdigest()[:26]
    return {
        "momentId":mid,"assetId":asset_id,"startMs":start,"endMs":end,
        "selectionSource":source,"selectionGroup":group,
        "activityConfirmed":bool(confirmed),"candidateKind":str(kind)[:70],
        "reviewStatus":"unreviewed",
        "labelStatus":"hypothesis_not_ground_truth",
        "eventOccurrenceVerified":False,
    }


def _window(t:int,duration:int,length:int)->tuple[int,int]:
    width=min(length,duration)
    start=min(max(0,t-width//2),duration-width)
    return start,start+width


def _overlap_ratio(a:tuple[int,int],b:tuple[int,int])->float:
    intersect=max(0,min(a[1],b[1])-max(a[0],b[0]))
    return intersect/max(1,min(a[1]-a[0],b[1]-b[0]))


def _random_window(aid:str,duration:int,plan_id:str,used:list[tuple[int,int]],
                   length:int,slot:int):
    limit=max(1,duration-min(length,duration)+1)
    options=[]
    for i in range(32):
        digest=sha256(f"{VERSION}:{plan_id}:{aid}:{slot}:{i}".encode()).digest()
        loc=int.from_bytes(digest[:8],"big")%limit
        candidate=_window(loc,duration,length)
        risk=max((_overlap_ratio(candidate,w) for w in used),default=0.)
        options.append((risk,i,candidate))
        if risk<.25:
            break
    risk,_,window=min(options,key=lambda x:(x[0],x[1]))
    return window,round(risk,4)


def _cached_context_candidates(root:Path,aid:str,duration:int)->list[tuple]:
    """Reuse existing bounded local event-context manifest candidates, if any.

    These are detector hypotheses and never imply a verified animal encounter.
    No media files are opened and no new inference occurs.
    """
    matches=[]
    for path in sorted((root/"reports").glob("*event-context*/manifest.json"))[:64]:
        if path.stat().st_size>8_000_000:
            continue
        try:
            packet=json.loads(path.read_text(encoding="utf-8"))
        except (OSError,ValueError,UnicodeError):
            continue
        if (packet.get("version")!="event-context-embedding-v1"
            or packet.get("assetId")!=aid
            or not isinstance(packet.get("events"),list)):
            continue
        for ev in packet["events"][:30]:
            if not isinstance(ev,dict):
                continue
            a,b=ev.get("startMs"),ev.get("endMs")
            if (type(a) is not int or type(b) is not int
                or not 0<=a<b<=duration):
                continue
            if b-a>60000:
                a,b=_window((a+b)//2,duration,30000)
            if b-a>=2000:
                matches.append((a,b,"cached_event_hypothesis",
                                str(ev.get("group","unverified_subject"))[:70]))
    return matches


def make_value_packet(workspace:Path,campaign:Path,*,batch:int=1,
                      output_dir:Path|None=None,max_per_asset:int=3)->dict:
    """Create one immutable review packet; later reviews never mutate the packet."""
    if type(batch) is not int or not 1<=batch<=10000:
        raise ValueError("batch must be 1..10000")
    if type(max_per_asset) is not int or not 2<=max_per_asset<=6:
        raise ValueError("max-per-asset must be 2..6")
    root=Path(workspace).expanduser().resolve()
    campaign=Path(campaign).expanduser().resolve()
    roster=_validate_campaign(root,campaign)
    folder=campaign/f"batch-{batch:04d}"
    manifest=_read_json(folder/"manifest.json")
    plan=_read_json(folder/"plan.json")
    schedules=validate_plan(plan)
    batch_size=roster["config"]["batchSize"]
    assets=roster["orderedAssets"][(batch-1)*batch_size:batch*batch_size]
    if (not assets or set(schedules)!={a["assetId"] for a in assets}
        or manifest.get("settings",{}).get("planId")!=plan["planId"]):
        raise ValueError("Frozen batch and signed plan disagree")
    target=(Path(output_dir).expanduser().resolve() if output_dir is not None
            else root/"reports"/"clip-value-lab-v1"/f"batch-{batch:04d}")
    if (target==root or not target.is_relative_to(root)
        or target.relative_to(root).parts[0] not in ("reports","exports")):
        raise ValueError("Review packet must be under workspace reports/ or exports/")
    with connect(root) as db:
        protect_source_output(db,target/"manifest.json")
        if target.exists():
            existing=_read_json(target/"manifest.json")
            if (existing.get("version")!=VERSION or
                existing.get("sourcePlanId")!=plan["planId"] or
                existing.get("campaign")!=str(campaign) or
                existing.get("maxPerAsset")!=max_per_asset or
                existing.get("packetId")!=_hash({k:v for k,v in existing.items()
                                                if k!="packetId"})):
                raise ValueError("Existing review packet is incompatible or corrupted")
            return {"status":"existing","batch":batch,"packetId":existing["packetId"],
                    "momentCount":len(existing["moments"]),
                    "selectionCounts":dict(Counter(x["selectionSource"]
                                                     for x in existing["moments"])),
                    "manifest":str(target/"manifest.json"),
                    "studioUrlPath":"/value-lab","videoDecoded":False}
        moments=[]
        for a in assets:
            aid=a["assetId"]
            duration=a["durationMs"]
            if type(duration) is not int or duration<2000:
                continue
            row=db.execute("SELECT duration FROM assets WHERE id=?",(aid,)).fetchone()
            if row is None or int(float(row["duration"])*1000)!=duration:
                raise ValueError("Asset duration changed since frozen plan")
            prior=[]
            # Prior human reviews should be represented, but never silently
            # converted into true labels for a different candidate window.
            reviewed=[dict(x) for x in db.execute(
                "SELECT * FROM example_revisions WHERE asset_id=? "
                "ORDER BY rowid DESC LIMIT 64",(aid,))]
            seen=set()
            for review in reviewed:
                eid=review["example_id"]
                if eid in seen or review["end_ms"]-review["start_ms"]>60000:
                    continue
                seen.add(eid)
                prior.append((review["start_ms"],review["end_ms"],
                              "reviewed_reference",review["label"]))
                if len(prior)>=max_per_asset-1:
                    break
            # The initial ray-like snorkeling pilot may already have
            # source-backed event packets even if episodes are not indexed.
            # Keep event labels unverified; selected intervals are for review.
            existing_ids={(p[0],p[1]) for p in prior}
            for ev in _cached_context_candidates(root,aid,duration):
                if len(prior)>=max_per_asset-1:
                    break
                if (ev[0],ev[1]) in existing_ids or any(
                    _overlap_ratio((ev[0],ev[1]),(x[0],x[1]))>.65
                    for x in prior):
                    continue
                prior.append(ev)
            if len(prior)<max_per_asset-1:
                eps=list(db.execute(
                    "SELECT start_ms,end_ms,kind FROM episodes "
                    "WHERE asset_id=? AND kind!='recording_summary' "
                    "ORDER BY score DESC,start_ms LIMIT 16",(aid,)))
                for row in eps:
                    start,end=row["start_ms"],row["end_ms"]
                    if (type(start) is not int or type(end) is not int
                        or start<0 or end<=start or start>=duration):
                        continue
                    start,end=max(0,start-2500),min(duration,end+3000)
                    if end-start>60000:
                        center=(start+end)//2
                        start,end=_window(center,duration,25000)
                    if end-start<2000:
                        continue
                    if any(_overlap_ratio((start,end),(x[0],x[1]))>.5 for x in prior):
                        continue
                    prior.append((start,end,"episode_proposal",row["kind"]))
                    if len(prior)>=max_per_asset-1:
                        break
            if len(prior)<max_per_asset-1:
                # Planned coverage is NOT an event proposal or a negative.
                times=schedules[aid]
                for idx in sorted({0,len(times)//2})[:max_per_asset-1]:
                    start,end=_window(times[idx],duration,14000)
                    if any(_overlap_ratio((start,end),(x[0],x[1]))>.5 for x in prior):
                        continue
                    prior.append((start,end,"coverage_probe","unverified_scene"))
            group=a["selectionGroup"]
            confirmed=a["activityConfirmed"]
            used=[]
            for start,end,source,kind in prior[:max_per_asset-1]:
                start=max(0,min(start,duration-1))
                end=min(duration,max(start+1,end))
                used.append((start,end))
                moments.append(_moment(aid,start,end,source,group,kind,
                                       plan["planId"],confirmed))
            # One randomized observation in every included video. It may
            # contain a major event, and MUST NOT be assumed uninteresting.
            (start,end),risk=_random_window(aid,duration,plan["planId"],used,
                                            14000,slot=0)
            control=_moment(aid,start,end,"random_control",group,
                            "unlabeled_interval",plan["planId"],confirmed)
            control["overlapRiskWithCandidates"]=risk
            control["importanceUnknown"]=True
            moments.append(control)
    if not moments or len(moments)>_MAX_PACKET:
        raise ValueError("No valid bounded candidate windows in frozen batch")
    base={
        "schemaVersion":1,"version":VERSION,
        "campaign":str(campaign),"batch":batch,
        "sourcePlanId":plan["planId"],
        "maxPerAsset":max_per_asset,
        "rubricVersion":"moment-criteria-v1",
        "criteria":CRITERIA,"scale":SCALE,
        "moments":moments,
        "limitations":[
            "Random controls are unknown, not negative examples.",
            "Episode labels and activity hypotheses are unverified.",
            "No source video was decoded or visually judged to create this packet.",
            "Review scores are user ratings, not measured biomechanics or species.",
            "Personal and public value remain separate.",
            "Sampled candidates do not establish full-archive event recall.",
        ],
    }
    packet={**base,"packetId":_hash(base)}
    target.mkdir(parents=True,mode=0o700,exist_ok=False)
    with (target/"manifest.json").open("x",encoding="utf-8") as f:
        json.dump(packet,f,indent=2,sort_keys=True,allow_nan=False)
        f.write("\n")
    return {"status":"created","batch":batch,"packetId":packet["packetId"],
            "momentCount":len(moments),
            "selectionCounts":dict(Counter(m["selectionSource"] for m in moments)),
            "manifest":str(target/"manifest.json"),
            "studioUrlPath":"/value-lab","videoDecoded":False}


def read_value_packet(workspace:Path,*,batch:int=1)->dict:
    if type(batch) is not int or not 1<=batch<=10000:
        raise ValueError("Invalid value-lab batch")
    root=Path(workspace).expanduser().resolve()
    packet=_read_json(root/"reports"/"clip-value-lab-v1"/f"batch-{batch:04d}"/"manifest.json")
    ident=packet.get("packetId")
    if (packet.get("version")!=VERSION or
        not isinstance(ident,str) or
        _hash({k:v for k,v in packet.items() if k!="packetId"})!=ident):
        raise ValueError("Invalid or modified value-lab packet")
    return packet


def latest_value_reviews(db,packet_id:str)->dict:
    return {
        row["moment_id"]:{
            "revisionId":row["revision_id"],"decision":row["decision"],
            "eventObserved":row["event_observed"],
            "ratings":json.loads(row["ratings_json"]),
            "note":row["note"],"improvementNote":row["improvement_note"],
        }
        for row in db.execute(
            "SELECT r.* FROM moment_value_revisions r "
            "WHERE packet_id=? AND NOT EXISTS ("
            "SELECT 1 FROM moment_value_revisions newer "
            "WHERE newer.packet_id=r.packet_id AND newer.moment_id=r.moment_id "
            "AND newer.rowid>r.rowid)",(packet_id,))
    }


def value_lab_state(workspace:Path,*,batch:int=1)->dict:
    packet=read_value_packet(workspace,batch=batch)
    with connect(Path(workspace)) as db:
        reviewed=latest_value_reviews(db,packet["packetId"])
    return {
        "schemaVersion":1,"version":VERSION,"batch":batch,
        "packetId":packet["packetId"],
        "criteria":CRITERIA,"scale":SCALE,
        "moments":packet["moments"],"reviews":reviewed,
        "reviewedCount":len(reviewed),"total":len(packet["moments"]),
        "sourceVideoOpened":False,
        "limitations":packet["limitations"],
    }


def save_value_review(workspace:Path,*,batch:int,moment_id:str,
                      decision:str,event_observed:str,ratings:dict,
                      note:str="",improvement_note:str="")->dict:
    packet=read_value_packet(workspace,batch=batch)
    moments={m["momentId"]:m for m in packet["moments"]}
    if not isinstance(moment_id,str) or moment_id not in moments:
        raise ValueError("Moment does not belong to the frozen review packet")
    if decision not in DECISIONS or event_observed not in OUTCOMES:
        raise ValueError("Invalid review decision or observed event state")
    if not isinstance(ratings,dict) or set(ratings)-set(CRITERIA):
        raise ValueError("Only versioned rubric ratings are accepted")
    clean={}
    for k in CRITERIA:
        v=ratings.get(k)
        if v is not None and (type(v) is not int or not 0<=v<=4):
            raise ValueError("Rubric ratings must be integers 0..4 or null")
        clean[k]=v
    if (not isinstance(note,str) or not isinstance(improvement_note,str)
        or len(note)>1000 or len(improvement_note)>1000
        or "\x00" in note or "\x00" in improvement_note):
        raise ValueError("Invalid review note")
    if event_observed=="not_observed" and clean["eventSignificance"] not in (None,0):
        raise ValueError("No observed event cannot receive positive event significance")
    aid=moments[moment_id]["assetId"]
    with connect(Path(workspace)) as db:
        found=db.execute("SELECT id FROM assets WHERE id=?",(aid,)).fetchone()
        if found is None:
            raise ValueError("Reviewed source asset no longer indexed")
        previous=db.execute(
            "SELECT * FROM moment_value_revisions "
            "WHERE packet_id=? AND moment_id=? ORDER BY rowid DESC LIMIT 1",
            (packet["packetId"],moment_id)).fetchone()
        data=json.dumps(clean,sort_keys=True,separators=(",",":"),allow_nan=False)
        if (previous is not None and previous["ratings_json"]==data
            and previous["decision"]==decision
            and previous["event_observed"]==event_observed
            and previous["note"]==note
            and previous["improvement_note"]==improvement_note):
            return {"status":"unchanged","revisionId":previous["revision_id"],
                    "momentId":moment_id}
        revision="mvr-"+uuid4().hex
        db.execute(
            "INSERT INTO moment_value_revisions "
            "(revision_id,packet_id,moment_id,previous_revision_id,asset_id,"
            "start_ms,end_ms,decision,event_observed,ratings_json,note,improvement_note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (revision,packet["packetId"],moment_id,
             previous["revision_id"] if previous else None,aid,
             moments[moment_id]["startMs"],moments[moment_id]["endMs"],
             decision,event_observed,data,note,improvement_note))
    return {"status":"reviewed","momentId":moment_id,
            "revisionId":revision,"previousRevisionId":
            previous["revision_id"] if previous else None}


def value_report(workspace:Path,*,batch:int=1)->dict:
    packet=read_value_packet(workspace,batch=batch)
    with connect(Path(workspace)) as db:
        latest=latest_value_reviews(db,packet["packetId"])
    criteria_counts=Counter()
    reviewed=[]
    all_moments=[]
    for m in packet["moments"]:
        r=latest.get(m["momentId"])
        scores={}
        if r:
            scores={k:v/4 if v is not None else None
                    for k,v in r["ratings"].items()}
            criteria_counts.update(k for k,v in scores.items() if v is not None)
            reviewed.append(m["momentId"])
        # Crucially, missing and unrated fields are still None, not zero.
        signals={
            k:scores.get(k) for k in
            ("eventSignificance","subjectVisibility","imageQuality",
             "narrativeCompleteness","visualNovelty","learningValue",
             "investigationPotential","uncertainty")
        }
        if not r or r["eventObserved"]!="observed":
            # A possible event with no observation never gets a positive
            # confirmed significance or human-verified outcome.
            signals["eventSignificance"]=None if not r or \
                r["eventObserved"]=="uncertain" else 0.
        sport=m["selectionGroup"]
        if sport not in ("ski","bike","snorkeling"):
            # Preserve unresolved clips for study, but do not falsely
            # assign them an activity to force a ranking score.
            continue
        raw={
            "momentId":m["momentId"],"assetId":m["assetId"],
            "startMs":m["startMs"],"endMs":m["endMs"],"activity":sport,
            "humanReview":("skip" if r and r["decision"]=="skip" else
                           "keep" if r and r["decision"]=="keep" else "unreviewed"),
            "subjectGroup":"unverified_scene",
            "taxonomyStatus":"unverified","signals":signals,
            "evidenceNotes":[
                f"Human rubric source: {VERSION} packet {packet['packetId'][:12]}",
                "Uncalibrated review ordering; no automatic publication",
            ],
        }
        all_moments.append(raw)
    ranked=rank_moments(all_moments,limit=24)
    reviewed_by_source=Counter(
        next(m["selectionSource"] for m in packet["moments"] if
             m["momentId"]==ident) for ident in reviewed)
    total_by_source=Counter(m["selectionSource"] for m in packet["moments"])
    return {
        "schemaVersion":1,"version":VERSION,
        "packetId":packet["packetId"],"batch":batch,
        "reviewedCount":len(reviewed),
        "totalCount":len(packet["moments"]),
        "reviewedBySelectionSource":dict(reviewed_by_source),
        "totalBySelectionSource":dict(total_by_source),
        "ratedCriteriaCounts":dict(criteria_counts),
        "ranking":ranked,
        "unresolvedActivityExcludedFromRankings":
            sum(m["selectionGroup"] not in ("ski","bike","snorkeling")
                for m in packet["moments"]),
        "warnings":[
            "Human-reviewed ordinal 0–4 ratings are inputs, not calibrated probabilities.",
            "Unreviewed intervals remain unknown; random controls are not negatives.",
            "This is preliminary within-pilot preference alignment, not event recall.",
            "Species rarity, sports technique and athlete physiology have not been verified.",
            "These rankings are for review only, never automatic publication.",
        ],
    }

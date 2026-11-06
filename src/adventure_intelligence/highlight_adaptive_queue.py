"""Deterministic feedback-driven *inspection* queue for local action video.

This is not a learned highlight ranker or a downloader. It joins frozen
source identities, prior reviewer context proposals and optional new timed
reactions, deduplicates nearby search work, balances source coverage, and
links reviewable intervals to verified private Reel Studio clips. No source
media read, catalog stat, preview decode, deletion or model training.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from html import escape
from pathlib import Path
from urllib.parse import quote, urlencode
import json
import os
import re

from .highlight_curation_pilot import _safe_dest
from .highlight_feedback_discovery import (
    _safe_input, VERSION as PLAN_VERSION
)
from .highlight_reel_studio import (
    VERSION as STUDIO_VERSION, _read_local
)
from .highlight_reel_feedback import (
    _verify_private_proxy, _REPORT_VERSION as FEEDBACK_VERSION
)

VERSION="klip-adaptive-highlight-inspection-queue-v1"
_SHA=re.compile(r"^[a-f0-9]{64}$")
_ID=re.compile(r"^[a-f0-9]{24}$")
_REASON={
    "retrim":0,
    "marked_interest":1,
    "prior_context":2,
}


def _identity(root:Path,pilot:Path,plan_path:Path,studio:Path):
    frozen,digest=_read_local(pilot/"curation-manifest.json")
    plan,plan_sha=_read_local(plan_path)
    manifest,studio_sha=_read_local(studio/"reel-manifest.json")
    if (frozen.get("version")!="klip-highlight-curation-manifest-v1"
            or not isinstance(frozen.get("records"),list)
            or plan.get("version")!=PLAN_VERSION
            or plan.get("frozenPilotManifestSha256")!=digest
            or manifest.get("version")!=STUDIO_VERSION
            or manifest.get("frozenPilotSha256")!=digest):
        raise ValueError("Pilot, review plan and local Reel Studio must share one frozen source manifest")
    sources={}
    for record in frozen["records"]:
        source=record["source"]
        aid=source["assetSha256"]
        family=source["sourceFamilyId"]
        duration=source["sourceDurationMs"]
        if (not _SHA.fullmatch(aid) or not isinstance(family,str)
                or type(duration) is not int or duration<1000):
            raise ValueError("Invalid frozen source identity or duration")
        if aid in sources:
            if sources[aid]["durationMs"]!=duration or sources[aid]["family"]!=family:
                raise ValueError("Frozen intervals disagree on source metadata")
        else:
            sources[aid]={
                "durationMs":duration,
                "family":family,
                "sport":record["sportContext"]["sport"],
            }
    clips={}
    for clip in manifest.get("clips",[]):
        rid=clip.get("clipId")
        if (not isinstance(rid,str) or not _ID.fullmatch(rid)
                or rid in clips):
            raise ValueError("Invalid or duplicate cached source clip ID")
        aid=clip["sourceAssetSha256"]
        if (aid not in sources
                or clip["sourceFamilyId"]!=sources[aid]["family"]
                or type(clip["startMs"]) is not int
                or type(clip["endMs"]) is not int
                or not 0<=clip["startMs"]<clip["endMs"]<=sources[aid]["durationMs"]):
            raise ValueError("Reel clip identity inconsistent with frozen source")
        clips[rid]=clip
    return sources,clips,plan,manifest,digest,studio_sha,plan_sha


def _check_candidate(c:dict,sources:dict,kind:str):
    aid=c.get("sourceAssetSha256")
    if aid not in sources:
        raise ValueError("Search candidate refers to source absent from frozen pilot")
    s=sources[aid]
    if c.get("sourceFamilyId")!=s["family"]:
        raise ValueError("Search candidate source family conflict")
    start,end=c.get("startMs"),c.get("endMs")
    if (type(start) is not int or type(end) is not int
            or not 0<=start<end<=s["durationMs"]
            or end-start>60000 or end-start<1000):
        raise ValueError("Search candidate outside catalog-backed source timeline")
    return {
        "candidateId":sha256(
            f"{VERSION}:{aid}:{start}:{end}:{kind}".encode()).hexdigest()[:24],
        "sourceAssetSha256":aid,
        "sourceFamilyId":s["family"],
        "sportFromFrozenCatalog":s["sport"],
        "startMs":start,
        "endMs":end,
        "kind":kind,
        "selectionPriorityTier":_REASON[kind],
        "selectionPriorityNotQualityScore":True,
        "reason":c.get("reason","human_requested_followup"),
        "reviewerIds":list(c.get("annotators",[]))[:30],
        "sourceClipIds":list(c.get("clipIds",[]))[:30],
        "humanTimeMarkersMs":list(c.get("humanMarkedSourceTimesMs",[]))[:80],
        "cachedReviewCoverage":"none",
        "cachedReviewCoverageFraction":0.0,
        "linkedStudioClipId":None,
        "seekSourceMs":None,
        "viewerLink":None,
        "fullIntervalObserved":False,
        "newMediaPrepared":False,
        "originalSourceReadAuthorized":False,
        "eventExistenceOrSuccessVerified":False,
        "editorialApprovalInferred":False,
    }


def _overlap(a:dict,b:dict)->int:
    return max(0,min(a["endMs"],b["endMs"])-max(a["startMs"],b["startMs"]))


def _merge_candidates(candidates:list[dict]):
    # Explicit human trims are not widened or merged into a looser guess.
    # For other categories, drop same-source/near-identical interval proposals.
    retained=[]
    duplicate_count=0
    for c in sorted(candidates,key=lambda x:(
            x["selectionPriorityTier"],x["sourceAssetSha256"],
            x["startMs"],x["endMs"],x["candidateId"])):
        if c["kind"]=="retrim":
            retained.append(c)
            continue
        near=next((r for r in retained
                   if r["kind"]==c["kind"]
                   and r["sourceAssetSha256"]==c["sourceAssetSha256"]
                   and (_overlap(r,c)/max(
                       r["endMs"]-r["startMs"],
                       c["endMs"]-c["startMs"]))>=.72),None)
        if near is None:
            retained.append(c)
        else:
            duplicate_count+=1
            near["reason"]=str(near["reason"])+"; "+str(c["reason"])
            near["reviewerIds"]=sorted(set(near["reviewerIds"]+c["reviewerIds"]))
            near["sourceClipIds"]=sorted(set(near["sourceClipIds"]+c["sourceClipIds"]))
            near["humanTimeMarkersMs"]=sorted(set(
                near["humanTimeMarkersMs"]+c["humanTimeMarkersMs"]))
    return retained,duplicate_count


def _coverage(c:dict,clips:dict,valid:set[str])->None:
    matches=[]
    for rid,clip in clips.items():
        if clip["sourceAssetSha256"]!=c["sourceAssetSha256"] or rid not in valid:
            continue
        overlap=_overlap(c,clip)
        if overlap:
            matches.append((overlap,clip["humanApproved"],rid,clip))
    if not matches:
        return
    overlap,_,rid,clip=max(matches,key=lambda x:(x[0],x[1],x[2]))
    fraction=round(overlap/(c["endMs"]-c["startMs"]),4)
    center=(c["startMs"]+c["endMs"])//2
    seek=min(clip["endMs"]-1,max(clip["startMs"],center))
    c["cachedReviewCoverageFraction"]=fraction
    c["cachedReviewCoverage"]=(
        "full" if overlap>=c["endMs"]-c["startMs"] else "partial")
    c["linkedStudioClipId"]=rid
    c["seekSourceMs"]=seek
    c["fullIntervalObserved"]=False  # Availability != observed adjudication.


def _balanced_queue(items:list[dict],limit:int,per_source:int):
    # Separate inclusion priority from video quality; within each evidence
    # tier, round-robin across catalog activity groups and source assets.
    selected=[]
    kept=Counter()
    role_counts=Counter()
    deferred=[]
    for tier in (0,1,2):
        groups=defaultdict(list)
        for c in items:
            if c["selectionPriorityTier"]==tier:
                groups[c["sportFromFrozenCatalog"]].append(c)
        for group in groups.values():
            group.sort(key=lambda x:(
                0 if x["cachedReviewCoverage"]=="full" else
                1 if x["cachedReviewCoverage"]=="partial" else 2,
                x["sourceAssetSha256"],x["startMs"],x["candidateId"]))
        keys=sorted(groups,key=lambda k:(k=="unknown",k))
        while any(groups.values()):
            advanced=False
            for sport in keys:
                if not groups[sport]:
                    continue
                candidate=groups[sport].pop(0)
                if kept[candidate["sourceAssetSha256"]]>=per_source:
                    deferred.append(candidate)
                    continue
                kept[candidate["sourceAssetSha256"]]+=1
                role_counts[candidate["kind"]]+=1
                selected.append(candidate)
                advanced=True
                if len(selected)>=limit:
                    return selected,role_counts,len(deferred)
            if not advanced:
                break
    return selected,role_counts,len(deferred)


def _index_html(queue:dict,studio_relative:str)->str:
    tags={
        "retrim":"Exact trim",
        "marked_interest":"Your timecoded interest",
        "prior_context":"Earlier context request",
    }
    css="""<style>
    :root{color-scheme:dark}body{background:#101a28;color:#f1f4f8;font:14px/1.5 system-ui;margin:auto;max-width:1050px;padding:26px}
    h1{font-size:25px}h2{font-size:15px}p,small{color:#abbace}section{border:1px solid #2c4356;background:#18283a;border-radius:13px;padding:16px;margin:12px 0}
    b{color:#f3a88f}.tag{border:1px solid #496077;border-radius:12px;padding:3px 9px;font-size:11px;color:#bfd3e1}
    .row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
    a{color:#f3af9a;text-decoration:none}a:hover{text-decoration:underline}
    .bar{height:6px;border-radius:7px;background:#2e4354;overflow:hidden;margin:13px 0}.fill{height:6px;background:#83afa0}
    </style>"""
    body=["<!doctype html><html lang='en'><head><meta charset='utf-8'>",
          '<meta name="viewport" content="width=device-width,initial-scale=1">',
          '<meta http-equiv="Content-Security-Policy" content="default-src \'none\';style-src \'unsafe-inline\';base-uri \'none\';form-action \'none\'">',
          "<title>Klip | Next Review Pass</title>",css,"</head><body>",
          "<h1>Next review pass</h1>",
          "<p>Proposed source-time inspections, not confirmed new highlights. "
          "The colored coverage bar refers only to available verified local "
          "preview footage, not to athletic performance or editorial merit.</p>"]
    for idx,c in enumerate(queue["selected"],1):
        sport=escape(c["sportFromFrozenCatalog"])
        reason=escape(str(c["reason"])[:900])
        coverage=round(c["cachedReviewCoverageFraction"]*100)
        body.extend([
            "<section><div class='row'><b>"+str(idx).zfill(2)+" · "+sport+
            "</b><span class='tag'>"+escape(tags[c["kind"]])+"</span>"+
            "<span class='tag'>"+escape(c["cachedReviewCoverage"])+" cached</span></div>",
            "<p>Source "+escape(c["sourceAssetSha256"][:12])+" · "+
            str(c["startMs"]/1000)+"–"+str(c["endMs"]/1000)+
            " seconds · "+str(coverage)+"% of proposed interval viewable in a cached clip</p>",
            "<div class='bar'><div class='fill' style='width:"+str(coverage)+"%'></div></div>",
            "<p>"+reason+"</p>",
        ])
        if c["linkedStudioClipId"] is not None:
            href=studio_relative+"?"+urlencode({
                "clip":c["linkedStudioClipId"],"t":str(c["seekSourceMs"])})
            body.append("<a href='"+escape(href,quote=True)+
                        "'>Inspect available footage in Reel Studio</a>")
        else:
            body.append("<p>No exact/overlapping verified preview. This needs a "
                        "future controlled source-media pass, not a guess.</p>")
        if c["cachedReviewCoverage"]=="partial":
            body.append("<small>Only overlapping footage is playable; the "
                        "full proposed interval has NOT been reviewed.</small>")
        body.append("</section>")
    if not queue["selected"]:
        body.append("<p>No candidate intervals qualified for this frozen review set.</p>")
    body.append("<p>All selection priorities are about follow-up attention, "
                "not predicted video quality. Originals are untouched.</p></body></html>")
    return "\n".join(body)


def save_adaptive_queue(workspace:Path,pilot_dir:Path,plan_file:Path,
                        studio_dir:Path,output_dir:Path,*,
                        timed_feedback:Path|None=None,limit:int=60,
                        per_source:int=3)->dict:
    if type(limit) is not int or not 1<=limit<=160:
        raise ValueError("Adaptive review budget must be 1..160")
    if type(per_source) is not int or not 1<=per_source<=8:
        raise ValueError("Per-source cap must be 1..8")
    root=Path(workspace).expanduser().resolve()
    pilot=_safe_input(root,pilot_dir)
    studio=_safe_input(root,studio_dir)
    plan_path=_safe_input(root,plan_file)
    dest=_safe_dest(root,output_dir)
    if dest.is_relative_to(pilot) or dest.is_relative_to(studio):
        raise ValueError("Do not place adaptive output inside frozen source packet")
    sources,clips,plan,manifest,pilot_sha,studio_sha,plan_sha=_identity(
        root,pilot,plan_path,studio)
    queued=[]
    for item in plan.get("sourceContextProposals",[]):
        queued.append(_check_candidate(item,sources,"prior_context"))
    timed_sha=None
    if timed_feedback is not None:
        timed_path=_safe_input(root,timed_feedback)
        timed,timed_sha=_read_local(timed_path)
        if (timed.get("version")!=FEEDBACK_VERSION or
                timed.get("frozenStudioManifestSha256")!=studio_sha
                or not isinstance(timed.get("humanProposedRetrims"),list)
                or not isinstance(timed.get("humanInterestDiscoveryProposals"),list)):
            raise ValueError("Timed feedback must match exactly the frozen Reel Studio")
        for row in timed["humanProposedRetrims"]:
            c=dict(row)
            c["reason"]="human_explicit_precise_retrim"
            c["annotators"]=[row["annotatorId"]]
            c["clipIds"]=[row["clipId"]]
            queued.append(_check_candidate(c,sources,"retrim"))
        for row in timed["humanInterestDiscoveryProposals"]:
            queued.append(_check_candidate(row,sources,"marked_interest"))
    unique,duplicates=_merge_candidates(queued)
    # Verify only matching private studio derivatives, never catalog original.
    relevant={x["sourceAssetSha256"] for x in unique}
    verified={
        rid for rid,clip in clips.items()
        if clip["sourceAssetSha256"] in relevant
        and _verify_private_proxy(studio,clip)
    }
    for c in unique:
        _coverage(c,clips,verified)
    selected,roles,deferred=_balanced_queue(unique,limit,per_source)
    queue={
        "version":VERSION,
        "frozenPilotSha256":pilot_sha,
        "humanReviewPlanSha256":plan_sha,
        "frozenStudioSha256":studio_sha,
        "timedFeedbackSha256":timed_sha,
        "requestedReviewBudget":limit,
        "perSourceCap":per_source,
        "rawCandidateProposals":len(queued),
        "duplicateOrNearDuplicateSuggestions":duplicates,
        "uniqueCandidates":len(unique),
        "selectedCount":len(selected),
        "selectedSourceCount":len({x["sourceAssetSha256"] for x in selected}),
        "selectionRoles":dict(sorted(roles.items())),
        "unselectedDueToCap":deferred,
        "coverage":dict(sorted(Counter(
            x["cachedReviewCoverage"] for x in selected).items())),
        "selectionIsReviewPriorityNotPredictedQualityScore":True,
        "allCandidatesSourceMediaUnverifiedBeyondExistingCache":True,
        "originalMediaReads":0,
        "originalMediaDecodes":0,
        "localCachedPreviewShaChecks":len(verified),
        "sourceFilesChanged":False,
        "newPreviewCreated":False,
        "modelCalls":0,
        "trainingPerformed":False,
        "selected":selected,
        "limitations":[
            "A cached preview may cover only PART of a proposed expanded source interval.",
            "No ranking score here predicts athletic achievement, rarity or video quality.",
            "A human's Skip on a prior cut never rejects its entire source.",
            "All missing parts require authorized source-media recovery and later observation.",
            "Source/outings in the original pilot still need independent grouping.",
            "The seed candidate pool depends on prior human annotation and heuristic selection, not complete archive coverage.",
        ],
    }
    # A convenience deep link opens existing studio material; no new MP4 copy.
    rel=os.path.relpath(studio/"index.html",dest)
    if rel.startswith("/"):
        raise ValueError("Unexpected absolute path to local studio")
    relative=quote(rel.replace(os.sep,"/"),safe="./")
    dest.mkdir(parents=True,mode=0o700,exist_ok=False)
    with (dest/"adaptive-queue.json").open("x",encoding="utf-8") as fp:
        json.dump(queue,fp,indent=2,sort_keys=True,allow_nan=False)
        fp.write("\n")
    with (dest/"index.html").open("x",encoding="utf-8") as fp:
        fp.write(_index_html(queue,relative))
    return {
        "status":"adaptive_inspection_queue_created",
        "queue":str(dest/"adaptive-queue.json"),
        "html":str(dest/"index.html"),
        "candidates":len(selected),
        "uniqueSources":queue["selectedSourceCount"],
        "cachedReviewCoverage":queue["coverage"],
        "deduplicatedProposals":duplicates,
        "originalSourceReads":0,
        "modelsTrained":0,
    }

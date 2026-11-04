"""Cache-only, archive-spanning ranking study with randomized blind controls.

This is *not* a validated detector or an exhaustive sweep through unsampled
video frames. It creates auditable clip proposals and human-review assignments
from source metadata, existing episode hypotheses, and verified feature caches.
Original media are never opened or modified.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from hashlib import sha256
from html import escape
from pathlib import Path
from urllib.parse import quote
import json
import math
import random

from .archive_pilot import _open_catalog
from .archive_review import VERSION as BENCHMARK_VERSION, CRITERIA, SPORT_QUESTIONS
from .editorial_quality import editorial_candidates
from .scene_atlas import enrich_candidates, diverse_representatives
from .safety import protect_source_output

VERSION="archive-value-study-v1"
SPORTS=("snorkeling","ski","bike","unresolved")
WINDOWS_MS={"snorkeling":20000,"ski":28000,"bike":24000,"unresolved":18000}
MAX_CANDIDATE_POOL=4000


def _canonical(data:dict)->str:
    return json.dumps(data,sort_keys=True,separators=(",",":"),allow_nan=False)


def _activity(value)->str:
    return value if value in SPORTS[:3] else "unresolved"


def _valid_asset(row:dict)->bool:
    duration=row.get("duration")
    return (isinstance(row.get("id"),str)
            and len(row["id"])==64
            and all(x in "0123456789abcdef" for x in row["id"])
            and type(duration) in (float,int)
            and math.isfinite(duration) and 1<=duration<=43200)


def _centered(start:int,end:int,duration:int,window:int)->tuple[int,int]:
    if not 0<=start<end<=duration:
        raise ValueError("Invalid source-relative candidate interval")
    width=min(duration,max(window,end-start))
    width=min(width,120000)
    center=(start+end)//2
    left=max(0,min(duration-width,center-width//2))
    return left,left+width


def _control_interval(duration:int,window:int,existing:tuple[int,int]|None,
                      *,seed:int,aid:str)->tuple[int,int]|None:
    """Uniform start position before nonoverlap rejection; not an unbiased census."""
    width=min(duration,window)
    if width<1000:
        return None
    rng=random.Random(f"{VERSION}:{seed}:{aid}")
    # Never silently claim unbiased sampling after candidate overlap rejection.
    for _ in range(64):
        start=rng.randint(0,max(0,duration-width))
        end=start+width
        if existing is None or min(end,existing[1])<=max(start,existing[0]):
            return start,end
    return None


def _stable_assignment(asset:dict,role:str,start:int,end:int,**extra)->dict:
    aid=asset["id"]
    rid=sha256(f"{BENCHMARK_VERSION}:{aid}:{role}:{start}:{end}".encode()
               ).hexdigest()[:20]
    name=Path(asset["stored_path"]).name
    return {
        "reviewId":rid,"assetId":aid,"basename":name,
        "selectionGroup":asset["group"],"activityConfirmed":asset["group"]!="unresolved",
        "role":role,"startMs":start,"endMs":end,
        "anchorMs":(start+end)//2,
        "schedulerReasons":["cache_ranked_episode"] if role=="candidate_hypothesis"
                           else ["randomized_source_time_control"],
        "analysisCoverage":"cached_evidence_only",
        "sourceFileUrl":Path(asset["stored_path"]).as_uri(),
        "notGroundTruth":True,"truthStatus":"unreviewed",
        "observedEvent":None,"eventKind":None,"speciesVerified":False,
        "techniqueVerified":False,"reviewComplete":False,
        **extra,
    }


def _public(candidate:dict)->dict:
    # Never serialize raw embedding vectors in reports; keep model revision key.
    return {k:v for k,v in candidate.items() if not k.startswith("_")}


def _source_candidates(db,root:Path,assets:dict[str,dict])->tuple[list[dict],dict]:
    if not assets:
        return [],{}
    ids=list(assets)
    episodes=[]
    counts=Counter()
    # Bounded active source pool, never a hidden all-media decode.
    for start in range(0,len(ids),250):
        subset=ids[start:start+250]
        query=("SELECT id,asset_id,kind,start_ms,end_ms,score,origin,version "
               "FROM episodes WHERE asset_id IN ("
               +",".join("?" for _ in subset)+")")
        grouped=defaultdict(list)
        for row in db.execute(query,subset):
            d=dict(row)
            duration=assets[d["asset_id"]]["durationMs"]
            if (type(d["start_ms"]) is not int or type(d["end_ms"]) is not int or
                    not 0<=d["start_ms"]<d["end_ms"]<=duration or
                    not isinstance(d["score"],(float,int)) or
                    not math.isfinite(d["score"]) or not 0<=d["score"]<=1):
                counts["invalidEpisode"]+=1
                continue
            grouped[d["asset_id"]].append(d)
        for aid,rows in grouped.items():
            # Do not allow dozens of related hypotheses from one recording
            # to monopolize runtime or exposure. This is a review shortlist.
            chosen=sorted(rows,key=lambda x:(-x["score"],x["start_ms"],x["id"]))[:8]
            episodes.extend(chosen)
            counts["episodesBeyondEightPerSource"]+=max(0,len(rows)-len(chosen))
    if not episodes:
        return [],dict(counts)
    candidates,exclusions=editorial_candidates(db,root,episodes,
                                                policy="suggest",max_window_ms=28000)
    counts["episodeWindowsExcludedByExistingManualReview"]=len(exclusions)
    counts["editorialWindowProposals"]=len(candidates)
    # The existing enrichment uses ONLY completed homogeneous semantic caches.
    enriched,_=enrich_candidates(db,root,candidates,provider="siglip2")
    unique={}
    for item in enriched:
        aid=item["sourceAssetId"]
        if aid not in assets:
            continue
        duration=assets[aid]["durationMs"]
        a,b=_centered(item["startMs"],item["endMs"],duration,
                      WINDOWS_MS[assets[aid]["group"]])
        key=(aid,a,b)
        c={**item,"startMs":a,"endMs":b,"duration":round((b-a)/1000,3),
           "activityGroup":assets[aid]["group"]}
        if key not in unique or c["score"]>unique[key]["score"]:
            unique[key]=c
    all_candidates=sorted(unique.values(),key=lambda c:
                          (-c["score"],c["sourceAssetId"],c["startMs"]))
    if len(all_candidates)>MAX_CANDIDATE_POOL:
        counts["truncatedRankedCandidates"]=len(all_candidates)-MAX_CANDIDATE_POOL
        all_candidates=all_candidates[:MAX_CANDIDATE_POOL]
    return all_candidates,dict(counts)


def _balance_assets(assets:list[dict],*,seed:int)->list[dict]:
    by_group={s:[] for s in SPORTS}
    for a in assets:
        by_group[a["group"]].append(a)
    for group in SPORTS:
        by_group[group].sort(key=lambda a:a["id"])
        random.Random(f"{VERSION}:{seed}:{group}").shuffle(by_group[group])
    ordered=[]
    while any(by_group.values()):
        for sport in SPORTS:
            if by_group[sport]:
                ordered.append(by_group[sport].pop(0))
    return ordered


def make_value_study(workspace:Path,*,seed:int=104,max_assets:int=0,
                     review_limit:int=96,max_reel_seconds:int=900)->tuple[dict,dict]:
    if type(seed) is not int or not 0<=seed<=2**31-1:
        raise ValueError("Seed must be an integer in 0..2147483647")
    if type(max_assets) is not int or not 0<=max_assets<=2000:
        raise ValueError("max_assets must be 0 (all) or at most 2000")
    if type(review_limit) is not int or not 1<=review_limit<=500:
        raise ValueError("review_limit must be 1..500")
    if type(max_reel_seconds) is not int or not 60<=max_reel_seconds<=7200:
        raise ValueError("max_reel_seconds must be 60..7200")
    root=Path(workspace).expanduser().resolve()
    missing=Counter()
    all_assets=[]
    with closing(_open_catalog(root)) as db:
        has_override=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                                "AND name='asset_organization'").fetchone() is not None
        query=("SELECT a.id,a.stored_path,a.duration,a.activity,"
               "o.activity_override AS override FROM assets a "
               "LEFT JOIN asset_organization o ON o.asset_id=a.id"
               if has_override else
               "SELECT id,stored_path,duration,activity,NULL AS override FROM assets")
        catalog=list(map(dict,db.execute(query)))
        for row in catalog:
            if not _valid_asset(row):
                missing["invalidDurationOrIdentity"]+=1
                continue
            path=Path(row["stored_path"])
            if not path.is_absolute() or not path.is_file():
                missing["sourceUnavailable"]+=1
                continue
            group=_activity(row["override"] or row["activity"])
            all_assets.append({**row,"group":group,
                               "durationMs":int(round(row["duration"]*1000))})
        ordered=_balance_assets(all_assets,seed=seed)
        selected=ordered[:max_assets] if max_assets else ordered
        lookup={row["id"]:row for row in selected}
        pool,details=_source_candidates(db,root,lookup)
    by_asset=defaultdict(list)
    for c in pool:
        by_asset[c["sourceAssetId"]].append(c)
    assignments=[]
    for asset in selected:
        if len(assignments)>=review_limit:
            break
        aid=asset["id"]
        ranked=by_asset.get(aid,[])
        best=ranked[0] if ranked else None
        choice=None
        if best is not None:
            choice=(best["startMs"],best["endMs"])
            assignments.append(_stable_assignment(asset,"candidate_hypothesis",
                *choice,rankBasis="uncalibrated_editorial_evidence",
                candidateScore=round(best["score"],5)))
        if len(assignments)>=review_limit:
            break
        control=_control_interval(asset["durationMs"],WINDOWS_MS[asset["group"]],
                                  choice,seed=seed,aid=aid)
        if control is not None:
            assignments.append(_stable_assignment(asset,"control_systematic",
                *control,rankBasis="randomized_source_interval_not_event_negative"))
    # Assignment order is masked to discourage role-anchored judgments.
    random.Random(f"{VERSION}:review-order:{seed}").shuffle(assignments)
    assignment_ids=[x["reviewId"] for x in assignments]
    if len(set(assignment_ids))!=len(assignment_ids):
        raise ValueError("Duplicate seeded assignment identity")
    # Sport-specific shortlists: no assumption that raw scores transfer
    # between snow, underwater scenes, and biking.
    shortlists={}
    diversity={}
    for sport in SPORTS:
        if sport=="unresolved":
            shortlists[sport]=[]
            continue
        subset=[c.copy() for c in pool if c["activityGroup"]==sport]
        if not subset:
            shortlists[sport]=[]
            continue
        chosen,diag=diverse_representatives(subset,max_seconds=max_reel_seconds,
                                          max_per_asset=2)
        shortlists[sport]=[_public(c) for c in chosen[:60]]
        diversity[sport]=diag
    reviewed_assets=len({a["assetId"] for a in assignments})
    counts=Counter(x["selectionGroup"] for x in assignments)
    roles=Counter(x["role"] for x in assignments)
    report={
        "version":VERSION,"policy":"cached_heuristics_and_random_control_review",
        "seed":seed,"catalogAssets":len(catalog),
        "accessibleValidAssets":len(all_assets),
        "analyzedAssets":len(selected),
        "assetsWithCachedEpisodeCandidates":len(by_asset),
        "episodeCandidateWindows":len(pool),
        "studyReviewIntervals":len(assignments),
        "studyAssets":reviewed_assets,
        "reviewLimit":review_limit,
        "selectionGroups":dict(sorted(counts.items())),
        "selectionRoles":dict(sorted(roles.items())),
        "qualityAndCoverageGaps":dict(sorted(missing.items())),
        "episodeDiagnostics":details,
        "diversityBySport":diversity,
        "shortlistsBySport":shortlists,
        "rankedCandidates":[_public(c) for c in pool[:500]],
        "rankedCandidatesTruncated":max(0,len(pool)-500),
        "scope":"catalog-wide_cached_episode_proposals_not_full-frame_census",
        "zeroOriginalVideoBytesRead":True,"newFrameDecodes":0,
        "newModelCalls":0,"cloudUploads":0,
        "humanReviewsModified":False,"originalVideosModified":False,
        "filesDeleted":0,
        "holdoutQuality":"not_session_disjoint_without_verified_outing_ids",
        "calibratedRankingAccuracy":None,
        "archivePrecision":None,"archiveRecall":None,
        "limitations":[
            "Unanalyzed or unclassified footage is retained as unknown; no event absence is inferred",
            "Shortlists rank only cached episode hypotheses and have not seen intervening original frames",
            "Source-selection and candidate-overlap rejection mean controls are exploratory, not an unbiased archive prevalence estimate",
            "Audio amplitude, optical flow, motion and terrain cannot verify actual speed or a landed maneuver",
            "Semantic similarity is not wildlife identity or confirmed novelty; no species rarity is inferred",
            "Previously reviewed examples can affect editorial scores; this is not a leakage-free held-out model benchmark",
            "Blind reviewer UI masks AI/selection rationale, but a determined reviewer with workspace access can inspect the stored manifest",
            "No source file is automatically accepted, exported, deleted or marked safe to delete",
        ],
    }
    manifest={
        "schemaVersion":1,"version":BENCHMARK_VERSION,
        "reviewIntervalCount":len(assignments),
        "sourceAssetCount":reviewed_assets,
        "assignments":assignments,
        "selectionGroups":dict(sorted(counts.items())),
        "selectionRoles":dict(sorted(roles.items())),
        "criteria":CRITERIA,"sportQuestions":SPORT_QUESTIONS,
        "blindReview":True,
        "studyVersion":VERSION,"studySeed":seed,
        "sourceFramesDecoded":0,"modelInferenceCalls":0,
        "sourceMediaRead":False,
        "limitations":report["limitations"],
    }
    return report,manifest


def save_value_study(workspace:Path,output_dir:Path,*,seed:int=104,
                     max_assets:int=0,review_limit:int=96,
                     max_reel_seconds:int=900)->dict:
    root=Path(workspace).expanduser().resolve()
    dest=Path(output_dir).expanduser().resolve()
    if (dest==root or not dest.is_relative_to(root) or dest.exists()
            or dest.relative_to(root).parts[0] not in ("reports","exports")):
        raise ValueError("Use a NEW directory in workspace reports/ or exports/")
    with closing(_open_catalog(root)) as db:
        protect_source_output(db,dest)
    report,manifest=make_value_study(root,seed=seed,max_assets=max_assets,
                    review_limit=review_limit,max_reel_seconds=max_reel_seconds)
    dest.mkdir(parents=True,mode=0o700,exist_ok=False)
    for name,document in (("study.json",report),("assignments.json",manifest)):
        with (dest/name).open("x",encoding="utf-8") as f:
            json.dump(document,f,indent=2,sort_keys=True,allow_nan=False)
            f.write("\n")
    blocks=[]
    for sport in SPORTS[:3]:
        rows=report["shortlistsBySport"].get(sport,[])
        lis=[]
        for row in rows[:25]:
            lis.append("<tr><td>"+escape(Path(row["sourceAssetId"]).name[:12])+
                       "</td><td>"+escape(str(row["kind"]))+"</td><td>"+
                       escape(f'{row["startMs"]/1000:.1f}–{row["endMs"]/1000:.1f}s')+
                       "</td><td>"+escape(f'{row["score"]:.3f}')+"</td></tr>")
        blocks.append("<section><h2>"+escape(sport.title())+
                      "</h2><table><thead><tr><th>Asset SHA prefix</th><th>Hypothesis</th>"
                      "<th>Interval</th><th>Heuristic</th></tr></thead><tbody>"+
                      "".join(lis)+"</tbody></table></section>")
    html=(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Klip · Archive-wide value study</title><style>'
        'body{background:#0f1723;color:#e8edf2;font:14px system-ui;'
        'max-width:1080px;margin:32px auto;padding:0 18px;line-height:1.55}'
        'h1{font-size:1.7rem}h2{color:#efb4a2;font-size:1.15rem}'
        'table{border-collapse:collapse;width:100%;margin-bottom:26px}'
        'th,td{text-align:left;padding:9px;border-bottom:1px solid #334456}'
        'p{color:#b8c9d3}</style></head><body>'
        '<h1>Archive-wide editorial investigation</h1><p>'
        'All entries are cache-derived hypotheses, NOT verified best clips. '
        'Rank values are uncalibrated across sports. Original media remain '
        'untouched. Open the blind assignments in local Studio for human review.'
        '</p><p>Analyzed assets: '+str(report["analyzedAssets"])+
        ' · Cached candidate windows: '+str(report["episodeCandidateWindows"])+
        ' · Blind review intervals: '+str(report["studyReviewIntervals"])+
        '</p>'+''.join(blocks)+'</body></html>'
    )
    with (dest/"index.html").open("x",encoding="utf-8") as f:
        f.write(html)
    return {"status":"archive_value_study_created",
            "study":str(dest/"study.json"),
            "assignments":str(dest/"assignments.json"),
            "html":str(dest/"index.html"),
            "analyzedAssets":report["analyzedAssets"],
            "candidateWindows":report["episodeCandidateWindows"],
            "reviewIntervals":report["studyReviewIntervals"],
            "selectionRoles":report["selectionRoles"],
            "sourceVideoReads":0,"modelCalls":0,
            "humanReviewWrites":0,"filesDeleted":0}

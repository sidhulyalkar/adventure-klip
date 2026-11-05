"""Metadata-only discovery coverage audit and safe source-level sampling plan.

This does not re-run ranking, classify video, measure clip quality, query
removable-media paths, or promote unverified cached job status to evidence.
Designed to audit the frozen 232-source study before another costly scan.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import json
import math

from .archive_pilot import _open_catalog, _table
from .archive_value_study import _valid_asset
from .social_action_lens import _json, USER_VERSION, FEATURE_VERSION, _new_folder

VERSION="archive-discovery-coverage-audit-v1"
GROUPS=("ski","bike","snorkeling","unresolved")


def _source_group(value):
    return value if value in GROUPS[:3] else "unresolved"


def _internal_json(root:Path,path:Path,label:str):
    file=Path(path).expanduser().resolve()
    if (file==root or not file.is_relative_to(root)
            or file.relative_to(root).parts[0] not in ("reports","exports")):
        raise ValueError(f"{label} must be in workspace reports/ or exports/")
    return _json(file),sha256(file.read_bytes()).hexdigest()


def _metadata_issues(a:dict)->list[str]:
    issues=[]
    aid=a.get("id")
    digest=a.get("sha256")
    if (not isinstance(aid,str) or len(aid)!=64
            or any(c not in "0123456789abcdef" for c in aid)):
        issues.append("invalid_catalog_id")
    if (not isinstance(digest,str) or len(digest)!=64
            or any(c not in "0123456789abcdef" for c in digest)):
        issues.append("invalid_stored_sha256")
    if aid!=digest:
        issues.append("catalog_id_sha256_mismatch")
    duration=a.get("duration")
    if (type(duration) not in (int,float) or not math.isfinite(duration)
            or not 1<=duration<=43200):
        issues.append("invalid_or_missing_duration")
    if type(a.get("size_bytes")) is not int or a["size_bytes"]<1:
        issues.append("invalid_recorded_size")
    if not isinstance(a.get("stored_path"),str) or not a["stored_path"]:
        issues.append("invalid_recorded_source_path")
    return issues


def inspect_discovery_coverage(workspace:Path,study_file:Path,features_file:Path,*,
                               source_budget:int=32)->dict:
    if type(source_budget) is not int or not 1<=source_budget<=200:
        raise ValueError("Source exploration budget must be 1..200")
    root=Path(workspace).expanduser().resolve()
    study,study_sha=_internal_json(root,study_file,"Study")
    features,features_sha=_internal_json(root,features_file,"Feature template")
    if (study.get("version")!="archive-value-study-v1"
            or features.get("version")!=USER_VERSION
            or features.get("featureVersion")!=FEATURE_VERSION
            or not isinstance(study.get("rankedCandidates"),list)
            or not isinstance(features.get("items"),list)):
        raise ValueError("Expected frozen archive-value-study and matching personal feature template")
    # The old personal feature artifact has no parent-study SHA. Validate exact
    # membership rather than wrongly claiming cryptographic parent linkage.
    frozen={}
    by_asset=defaultdict(int)
    for row in study["rankedCandidates"]:
        if not isinstance(row,dict):
            raise ValueError("Invalid ranked study candidate")
        aid,start,end=(row.get(k) for k in ("sourceAssetId","startMs","endMs"))
        if (not isinstance(aid,str) or type(start) is not int
                or type(end) is not int or start>=end):
            raise ValueError("Malformed ranked candidate identity")
        key=(aid,start,end)
        if key in frozen:
            raise ValueError("Duplicate ranked candidate")
        frozen[key]=row
        by_asset[aid]+=1
    selected=[]
    seen=set()
    for row in features["items"]:
        if not isinstance(row,dict):
            raise ValueError("Invalid feature selection")
        key=tuple(row.get(k) for k in ("assetId","startMs","endMs"))
        if key in seen or key not in frozen:
            raise ValueError("Feature selection is duplicated or absent from frozen ranked study")
        seen.add(key)
        if row.get("sport")!=("other" if
                frozen[key].get("activityGroup")=="unresolved"
                else frozen[key].get("activityGroup")):
            raise ValueError("Selected feature activity differs from frozen study")
        selected.append(row)
    with closing(_open_catalog(root)) as db:
        org=_table(db,"asset_organization")
        sql=("SELECT a.id,a.sha256,a.stored_path,a.duration,a.size_bytes,"
             "COALESCE(NULLIF(o.activity_override,''),a.activity) AS activity "
             "FROM assets a LEFT JOIN asset_organization o ON o.asset_id=a.id"
             if org else
             "SELECT id,sha256,stored_path,duration,size_bytes,activity FROM assets")
        assets=[dict(r) for r in db.execute(sql)]
        episode_counts=Counter()
        if _table(db,"episodes"):
            episode_counts.update({r[0]:r[1] for r in
                db.execute("SELECT asset_id,COUNT(*) FROM episodes GROUP BY asset_id")})
        active_jobs=defaultdict(set)
        if _table(db,"semantic_jobs") and _table(db,"semantic_active"):
            for r in db.execute(
                "SELECT a.asset_id,a.provider,j.status FROM semantic_active a "
                "JOIN semantic_jobs j ON a.asset_id=j.asset_id AND a.config_id=j.config_id"):
                if r["status"]=="completed":
                    active_jobs[r["asset_id"]].add(r["provider"])
        stages=defaultdict(set)
        if _table(db,"analyses"):
            for r in db.execute("SELECT asset_id,stage,status FROM analyses"):
                if r["status"]=="completed":
                    stages[r["asset_id"]].add(r["stage"])
        reviewed=set()
        if _table(db,"example_revisions"):
            reviewed={r[0] for r in db.execute(
                "SELECT DISTINCT asset_id FROM example_revisions")}
    catalog_ids={a["id"] for a in assets}
    if not set(by_asset).issubset(catalog_ids):
        raise ValueError("Frozen study refers to source identities missing from current catalog")
    selected_sources={r["assetId"] for r in selected}
    metadata_issues=Counter()
    raw_groups=Counter()
    group_by_rep=Counter()
    asset_records=[]
    for asset in assets:
        aid=asset["id"]
        issues=_metadata_issues(asset)
        metadata_issues.update(issues)
        group=_source_group(asset["activity"])
        raw_groups[group]+=1
        if aid in by_asset:
            group_by_rep[group]+=1
        # _valid_asset is the original study validator: compare with its rules.
        original_valid=_valid_asset({"id":aid,"duration":asset["duration"]})
        priority=(
            "metadata_repair" if not original_valid else
            "already_ranked" if aid in by_asset else
            "unrepresented_with_completed_semantic_job" if active_jobs[aid] else
            "unrepresented_with_cached_episode" if episode_counts[aid] else
            "unrepresented_with_completed_analysis" if stages[aid] else
            "unrepresented_no_completed_analysis"
        )
        asset_records.append({
            "assetId":aid,"activityGroup":group,
            "catalogActivityRaw":asset["activity"],
            "durationMs":(int(round(asset["duration"]*1000))
                          if type(asset["duration"]) in (int,float) and
                          math.isfinite(asset["duration"]) else None),
            "sizeBytesRecorded":asset["size_bytes"],
            "originalStudyMetadataValid":original_valid,
            "metadataIssues":issues,
            "rankedCandidateWindows":by_asset[aid],
            "pilotWindows":sum(r["assetId"]==aid for r in selected),
            "cachedEpisodeRows":episode_counts[aid],
            "completedAnalysisStagesReported":sorted(stages[aid]),
            "completedSemanticProvidersReported":sorted(active_jobs[aid]),
            "humanReviewHistoryExists":aid in reviewed,
            "discoveryTier":priority,
            "sourceAccessibilityCheckedNow":False,
            "cacheArtifactIntegrityCheckedNow":False,
            "eventPresence":"unknown",
        })
    # Within each activity bucket, explore stronger existing evidence first,
    # then under-explored/unknown assets. This is not a quality ranking.
    tier_order={
        "unrepresented_with_completed_semantic_job":0,
        "unrepresented_with_cached_episode":1,
        "unrepresented_with_completed_analysis":2,
        "unrepresented_no_completed_analysis":3,
    }
    candidates=[a for a in asset_records if a["discoveryTier"] in tier_order]
    groups=defaultdict(list)
    for a in candidates:
        groups[a["activityGroup"]].append(a)
    for group in groups:
        groups[group].sort(key=lambda a:(tier_order[a["discoveryTier"]],
                                         -int(a["humanReviewHistoryExists"]),
                                         a["assetId"]))
    plan=[]
    while len(plan)<source_budget and any(groups.values()):
        for group in GROUPS:
            if not groups[group]:
                continue
            a=groups[group].pop(0)
            plan.append({
                "sourceAssetId":a["assetId"],"activityGroup":group,
                "reason":a["discoveryTier"],
                "completedSemanticProvidersReported":
                    a["completedSemanticProvidersReported"],
                "sourceMediaReadAuthorized":False,
                "humanReviewGroundTruth":False,
            })
            if len(plan)>=source_budget:
                break
    tier_counts=Counter(a["discoveryTier"] for a in asset_records)
    selected_groups=Counter(r["sport"] for r in selected)
    source_groups=Counter(a["activityGroup"] for a in asset_records
                          if a["assetId"] in selected_sources)
    report={
        "version":VERSION,
        "mode":"frozen_study_and_catalog_metadata_only",
        "studySha256":study_sha,"featureTemplateSha256":features_sha,
        "studyFeatureMembershipChecked":True,
        "featureParentStudySha256Available":False,
        "sourceRosterSize":len(assets),
        "originalStudyMetadataValidInCurrentCatalog":sum(
            a["originalStudyMetadataValid"] for a in asset_records),
        "originalStudyMetadataInvalidInCurrentCatalog":sum(
            not a["originalStudyMetadataValid"] for a in asset_records),
        "metadataIssues":dict(sorted(metadata_issues.items())),
        "catalogActivitiesAsRecorded":dict(sorted(raw_groups.items())),
        "rankedCandidateSourceCount":len(by_asset),
        "rankedCandidateWindowCount":len(frozen),
        "rankedCandidatesTruncated":study.get("rankedCandidatesTruncated"),
        "rankedCandidateSourcesByActivity":dict(sorted(group_by_rep.items())),
        "selectedPilotWindows":len(selected),
        "selectedPilotUniqueSources":len(selected_sources),
        "selectedPilotWindowActivity":dict(sorted(selected_groups.items())),
        "selectedPilotSourceActivity":dict(sorted(source_groups.items())),
        "discoveryTierCounts":dict(sorted(tier_counts.items())),
        "explorationSourceBudget":source_budget,
        "explorationSourcePlan":plan,
        "explorationPlanActivities":dict(sorted(Counter(
            a["activityGroup"] for a in plan).items())),
        "sourceRecords":asset_records,
        "storageAndEvaluationSafety":{
            "originalVideoReads":0,"externalDrivePathStatCalls":0,
            "videoFramesDecoded":0,"modelInferenceCalls":0,
            "sourceMediaModified":False,"humanReviewsModified":False,
            "sourceAccessibilityCheckedNow":False,
            "semanticArtifactsReverifiedNow":False,
        },
        "limitations":[
            "A catalog semantic-job completed status is NOT a reverified model artifact.",
            "Unrepresented means absent from frozen ranked candidates, NOT absence of events.",
            "Catalog metadata cannot establish that removable source media is currently accessible.",
            "The original study's accessible count involved source-path checks; this audit deliberately does not repeat them.",
            "The plan is for source-level exploration, not a best-clip ranking or approved scene labels.",
            "Activity unresolved/other is not proof of no skiing, biking, or snorkeling.",
            "Review/score leakage remains possible in old heuristic candidates; this is not held-out accuracy.",
            "The frozen ranked report may be truncated; coverage claims are bounded by its candidate roster.",
        ],
    }
    return report


def save_discovery_coverage(workspace:Path,study_file:Path,features_file:Path,
                            output_dir:Path,*,source_budget:int=32)->dict:
    root=Path(workspace).expanduser().resolve()
    dest=_new_folder(root,output_dir)
    report=inspect_discovery_coverage(root,study_file,features_file,
                                      source_budget=source_budget)
    dest.mkdir(mode=0o700,parents=True,exist_ok=False)
    for name,obj in (("coverage.json",report),("source-plan.json",{
            "version":VERSION,"studySha256":report["studySha256"],
            "featureTemplateSha256":report["featureTemplateSha256"],
            "sourceBudget":source_budget,
            "sourceCandidates":report["explorationSourcePlan"],
            "sourceMediaReadAuthorized":False})):
        with (dest/name).open("x",encoding="utf-8") as handle:
            json.dump(obj,handle,indent=2,sort_keys=True,allow_nan=False)
            handle.write("\n")
    return {
        "status":"read_only_discovery_coverage_written",
        "coverage":str(dest/"coverage.json"),
        "sourcePlan":str(dest/"source-plan.json"),
        "catalogAssets":report["sourceRosterSize"],
        "rankedSources":report["rankedCandidateSourceCount"],
        "rankedWindows":report["rankedCandidateWindowCount"],
        "selectedPilotWindows":report["selectedPilotWindows"],
        "selectedPilotActivities":report["selectedPilotWindowActivity"],
        "metadataInvalid":report["originalStudyMetadataInvalidInCurrentCatalog"],
        "unrepresentedSourcesInPlan":len(report["explorationSourcePlan"]),
        "sourceVideoReads":0,"externalDrivePathStatCalls":0,
        "modelCalls":0,"mediaModified":False,
    }

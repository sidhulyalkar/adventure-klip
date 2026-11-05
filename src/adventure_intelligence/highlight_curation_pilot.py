"""Source-safe, reproducible selection of *annotation candidates*, not highlights.

No original media paths are stat()'d, resolved, decoded or opened. Only
workspace-local verified H.264 preview derivatives may be linked into a private
offline human review packet. Sampling strata are diagnostic hypotheses and
must NEVER be mistaken for ground-truth outcome or preference labels.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote
import json
import math
import os
import random

from .archive_pilot import _open_catalog, _table
from .social_action_lens import USER_VERSION, FEATURE_VERSION
from .studio_preview_judge import preview_paths, _check_cached

VERSION="klip-highlight-curation-pilot-v1"
MANIFEST_VERSION="klip-highlight-curation-manifest-v1"
RECORD_VERSION="klip-highlight-curation-record-v1"
ROLES=("frozen_pilot","ranked_high","ranked_middling","unrepresented_source_control")


def _input(root:Path,path:Path,label:str)->tuple[dict,str]:
    p=Path(path).expanduser().resolve()
    if (p==root or not p.is_relative_to(root)
            or p.relative_to(root).parts[0] not in ("reports","exports")
            or not p.is_file() or p.stat().st_size>20_000_000):
        raise ValueError(f"{label} must be a bounded existing workspace report JSON")
    data=p.read_bytes()
    try:
        value=json.loads(data)
    except (UnicodeDecodeError,ValueError) as exc:
        raise ValueError(f"{label} JSON is invalid") from exc
    if not isinstance(value,dict):
        raise ValueError(f"{label} must be a JSON object")
    return value,sha256(data).hexdigest()


def _safe_dest(root:Path,output_dir:Path)->Path:
    """Never dereference a removable-media stored_path to validate output."""
    dest=Path(output_dir).expanduser().resolve()
    if (dest==root or not dest.is_relative_to(root) or dest.exists()
            or dest.relative_to(root).parts[0] not in ("reports","exports")):
        raise ValueError("Use a NEW directory inside workspace reports/ or exports/")
    with closing(_open_catalog(root)) as db:
        paths=[x[0] for x in db.execute("SELECT stored_path FROM assets")]
        if _table(db,"imports"):
            paths.extend(x[0] for x in db.execute("SELECT original_path FROM imports"))
    for raw in paths:
        if not isinstance(raw,str) or not os.path.isabs(raw):
            raise ValueError("Unsafe nonabsolute catalog source path")
        source=Path(os.path.normpath(raw))
        if dest==source or dest.is_relative_to(source.parent):
            raise ValueError("Output must not be written inside a source media folder")
    return dest


def _sport(value:str)->str:
    return value if value in ("ski","bike","snorkeling") else "unknown"


def _id(aid:str,start:int,end:int)->str:
    return sha256(f"{RECORD_VERSION}:{aid}:{start}:{end}".encode()).hexdigest()[:24]


def _valid_source(asset:dict)->bool:
    aid,duration=asset.get("id"),asset.get("duration")
    return (isinstance(aid,str) and len(aid)==64
            and all(x in "0123456789abcdef" for x in aid)
            and asset.get("sha256")==aid
            and type(duration) in (int,float) and math.isfinite(duration)
            and 1<=duration<=43200)


def _record(asset:dict,start:int,end:int)->dict:
    aid=asset["id"]
    # Conservatively place ALL unverified outings together. A later verified
    # outing mapping is necessary before any source/outing split is possible.
    family="original-"+aid
    outing="unresolved-outing-all"
    return {
        "version":RECORD_VERSION,"recordId":_id(aid,start,end),
        "source":{
            "assetSha256":aid,"sourceFamilyId":family,"outingGroupId":outing,
            "athleteGroupId":None,
            "cameraView":"unknown","rawOrEdited":"unknown",
            "sourceDurationMs":int(round(asset["duration"]*1000)),
            "resolution":None,"frameRate":None,
            "cameraSensorNotes":None,"linkedVersionGroupId":None,
        },
        "event":{
            "startMs":start,"endMs":end,"peakMs":None,
            "eventClass":None,"observedOutcome":"unknown",
            "outcomeVisible":"ambiguous",
            "phases":[{
                "phase":"context","startMs":start,"endMs":end,
                "visibility":"ambiguous","evidenceKind":"unknown",
                "annotatorId":None,"note":"Unreviewed interval, not an observed event",
            }],
            "observationCaveats":["Candidate window; event presence not assessed"],
            "measuredSpeedMps":None,"speedMeasurementMethod":None,
        },
        "sportContext":{
            "sport":_sport(asset["activity"]),
            "subdiscipline":None,"terrainOrEnvironment":None,
            "actionDifficultyHumanOrdinal":None,
            "executionEvidenceStatus":"unknown",
            "expertRationale":None,"sportSpecificTags":[],
        },
        "filming":{
            "technicalQuality":None,"aestheticQuality":None,
            "eventReadability":None,"cameraMotionInterpretation":"not_assessed",
            "focalEventVisible":"unknown",
            "shotCompositionNotes":None,"editingNotes":None,
            "technicalReviewStatus":"not_assessed",
        },
        "observerJudgments":[],"reactions":[],
        "rights":{
            "rightsStatus":"pending",
            "sourceAccessAuthorized":False,
            "sourceTrainingAuthorized":False,
            "embeddedMediaTrainingAuthorized":False,
            "audioTrainingAuthorized":False,
            "speakerVoiceConsent":False,
            "sourcePermissionRecordId":None,
            "trainingScope":None,"revocationState":"unknown",
            "reviewedBy":None,
        },
        "split":{
            "partition":"unassigned","sourceFamilyId":family,
            "outingGroupId":outing,"frozenSplitVersion":None,
        },
        "evidenceStatus":{
            "sourceSha256Reverified":False,
            "humanEventAnnotationFrozen":False,
            "humanRatingsFrozen":False,
            "modelHypothesesStoredSeparately":True,
            "dataCollectionProtocolVersion":VERSION,
        },
    }


def make_curation_pilot(workspace:Path,study_file:Path,features_file:Path,*,
                        limit:int=60,seed:int=104,max_per_source:int=2)->tuple[dict,dict]:
    if type(limit) is not int or not 1<=limit<=200:
        raise ValueError("Curation candidate budget must be 1..200")
    if type(seed) is not int or not 0<=seed<=2**31-1:
        raise ValueError("Invalid deterministic curation seed")
    if type(max_per_source) is not int or not 1<=max_per_source<=8:
        raise ValueError("Per-source selection limit must be 1..8")
    root=Path(workspace).expanduser().resolve()
    study,study_sha=_input(root,study_file,"Frozen archive study")
    features,feature_sha=_input(root,features_file,"Frozen feature template")
    if (study.get("version")!="archive-value-study-v1"
            or features.get("version")!=USER_VERSION
            or features.get("featureVersion")!=FEATURE_VERSION
            or not isinstance(study.get("rankedCandidates"),list)
            or len(study["rankedCandidates"])>5000
            or not isinstance(features.get("items"),list)
            or len(features["items"])>5000):
        raise ValueError("Incompatible frozen study or feature template")
    with closing(_open_catalog(root)) as db:
        org=_table(db,"asset_organization")
        query=("SELECT a.id,a.sha256,a.duration,"
               "COALESCE(NULLIF(o.activity_override,''),a.activity) AS activity "
               "FROM assets a LEFT JOIN asset_organization o ON o.asset_id=a.id"
               if org else
               "SELECT id,sha256,duration,activity FROM assets")
        assets={r["id"]:dict(r) for r in db.execute(query)}
    valid={aid:a for aid,a in assets.items() if _valid_source(a)}
    ranked=[]
    frozen={}
    for item in study["rankedCandidates"]:
        if not isinstance(item,dict):
            raise ValueError("Malformed ranked study item")
        aid,start,end=(item.get(k) for k in ("sourceAssetId","startMs","endMs"))
        if not isinstance(aid,str) or type(start) is not int or type(end) is not int:
            raise ValueError("Malformed ranked interval identity")
        key=(aid,start,end)
        if key in frozen:
            raise ValueError("Duplicate frozen ranked interval")
        frozen[key]=item
        score=item.get("score")
        if (aid in valid and 0<=start<end<=int(round(valid[aid]["duration"]*1000))
                and 1000<=end-start<=60000
                and type(score) in (int,float) and math.isfinite(score)
                and 0<=score<=1):
            ranked.append((key,float(score)))
    baseline=[]
    baseline_seen=set()
    for item in features["items"]:
        if not isinstance(item,dict):
            raise ValueError("Malformed baseline pilot selection")
        key=tuple(item.get(k) for k in ("assetId","startMs","endMs"))
        if key in baseline_seen or key not in frozen:
            raise ValueError("Baseline candidate missing from frozen ranked study or duplicated")
        baseline_seen.add(key)
        expected=("other" if frozen[key].get("activityGroup")=="unresolved"
                  else frozen[key].get("activityGroup"))
        if item.get("sport")!=expected:
            raise ValueError("Baseline activity differs from frozen study")
        if key[0] in valid and key in dict(ranked):
            baseline.append(key)
    # This sample is for annotation coverage, NOT a ranked set of highlights.
    # Each quota is approximate because coverage and sources may be limited.
    per_role={k:[] for k in ROLES}
    ordered=sorted(ranked,key=lambda pair:(-pair[1],pair[0]))
    per_role["frozen_pilot"]=sorted(
        baseline,key=lambda x:sha256(f"{seed}:pilot:{x}".encode()).hexdigest())
    per_role["ranked_high"]=[key for key,_ in ordered]
    by_middle=sorted(ordered,key=lambda pair:(abs(pair[1]-.5),pair[0]))
    per_role["ranked_middling"]=[key for key,_ in by_middle]
    # Source-time controls are sampled from sources that never made the
    # *reported* ranked candidate list. Do not interpret them as negatives.
    not_ranked=sorted(aid for aid in valid if aid not in {
        key[0] for key in frozen})
    random.Random(f"{VERSION}:{seed}:unrepresented").shuffle(not_ranked)
    for aid in not_ranked:
        duration=int(round(valid[aid]["duration"]*1000))
        width=min(20000,duration)
        if width<1000:
            continue
        start=random.Random(f"{VERSION}:{seed}:{aid}:time").randint(
            0,max(0,duration-width))
        per_role["unrepresented_source_control"].append((aid,start,start+width))
    selected=[]
    used_keys=set()
    per_asset=Counter()
    selected_roles=Counter()
    # Quotas are fair where possible, with deterministic backfill if one
    # group cannot contribute enough unique source identities.
    targets={role:limit//len(ROLES)+(i<limit%len(ROLES))
             for i,role in enumerate(ROLES)}
    for phase in (0,1):
        while len(selected)<limit:
            changed=False
            for role in ROLES:
                if phase==0 and selected_roles[role]>=targets[role]:
                    continue
                pool=per_role[role]
                while pool:
                    key=pool.pop(0)
                    if key not in used_keys and per_asset[key[0]]<max_per_source:
                        break
                else:
                    continue
                used_keys.add(key)
                per_asset[key[0]]+=1
                selected_roles[role]+=1
                selected.append({"key":key,"selectionRole":role})
                changed=True
                if len(selected)>=limit:
                    break
            if not changed:
                break
        if len(selected)>=limit:
            break
    records=[_record(valid[x["key"][0]],x["key"][1],x["key"][2])
             for x in selected]
    manifest={"version":MANIFEST_VERSION,"records":records}
    report={
        "version":VERSION,"seed":seed,"maxRecordsRequested":limit,
        "maxPerSource":max_per_source,"selectedRecords":len(records),
        "selectedUniqueSources":len(per_asset),
        "studySha256":study_sha,"featureTemplateSha256":feature_sha,
        "catalogSourceRecords":len(assets),"catalogMetadataValidSources":len(valid),
        "catalogMetadataInvalidSources":len(assets)-len(valid),
        "reportedFrozenRankedSourceCount":len({k[0] for k in frozen}),
        "usableRankedCandidateWindows":len(ranked),
        "selectionRoles":dict(sorted(selected_roles.items())),
        "observedEventGroundTruthCreated":0,
        "humanRatingsCollected":0,
        "physicalSourceMediaReads":0,"originalSourcePathStats":0,
        "sourceFilesModified":False,"externalUploads":0,
        "activityInSelected":dict(sorted(Counter(
            r["sportContext"]["sport"] for r in records).items())),
        "selections":[{
            "recordId":records[i]["recordId"],"sourceAssetId":s["key"][0],
            "startMs":s["key"][1],"endMs":s["key"][2],
            "selectionRole":s["selectionRole"],
            "heuristicScoreForAuditOnly":(frozen.get(s["key"]) or {}).get("score"),
        } for i,s in enumerate(selected)],
        "limitations":[
            "Unrepresented source-time intervals are UNKNOWN, not confirmed boring clips",
            "Old candidate scores and baseline selections may encode previous human review",
            "The old study's rankedCandidates list can be truncated",
            "No current original-source accessibility, SHA or playback was checked",
            "Missing verified preview cannot be annotated from the offline packet",
            "All outings deliberately share an unresolved grouping until independently established",
            "No rights, training labels, outcome visibility or model performance claims are created",
        ],
    }
    return report,manifest


def save_curation_pilot(workspace:Path,study_file:Path,features_file:Path,
                        output_dir:Path,*,limit:int=60,seed:int=104,
                        max_per_source:int=2)->dict:
    from .highlight_curation_review import render_blind_review
    from .highlight_curation_validate import audit_highlight_curation_manifest
    root=Path(workspace).expanduser().resolve()
    dest=_safe_dest(root,output_dir)
    report,manifest=make_curation_pilot(root,study_file,features_file,
                                      limit=limit,seed=seed,
                                      max_per_source=max_per_source)
    dest.mkdir(parents=True,mode=0o700,exist_ok=False)
    # Save and audit immutable, unassessed records BEFORE rendering any review.
    manifest_path=dest/"curation-manifest.json"
    manifest_bytes=(json.dumps(manifest,indent=2,sort_keys=True,
                               allow_nan=False)+"\n").encode("utf-8")
    with manifest_path.open("xb") as handle:
        handle.write(manifest_bytes)
    checked=audit_highlight_curation_manifest(manifest_path)
    if checked["records"]!=report["selectedRecords"]:
        raise ValueError("Curation manifest and selection counts differ")
    digest=sha256(manifest_bytes).hexdigest()
    media=dest/"media"
    media.mkdir(mode=0o700)
    presentation={}
    verified=0
    for entry in manifest["records"]:
        rid=entry["recordId"]
        aid=entry["source"]["assetSha256"]
        start,end=entry["event"]["startMs"],entry["event"]["endMs"]
        source,receipt=preview_paths(root,aid,start,end)
        url=None
        derivative_sha=None
        if _check_cached(source,receipt,aid=aid,start_ms=start,end_ms=end):
            # Only share a bounded, verified LOCAL DERIVATIVE, never original.
            # Hardlinks require the same local workspace filesystem.
            target=media/(rid+".mp4")
            try:
                os.link(source,target)
            except OSError:
                target=None
            if target is not None:
                expected=json.loads(receipt.read_text())["previewSha256"]
                if sha256(target.read_bytes()).hexdigest()!=expected:
                    target.unlink(missing_ok=True)
                    raise ValueError("Verified derivative changed while creating review packet")
                url="media/"+quote(target.name,safe=".")
                derivative_sha=expected
                verified+=1
        presentation[rid]={
            "verifiedProxyReady":url is not None,
            "proxyUrl":url,
            "proxySha256":derivative_sha,
            "requiresSourceReadForPreview":url is None,
        }
    report["verifiedReviewProxies"]=verified
    report["missingReviewProxies"]=len(manifest["records"])-verified
    report["manifestSha256"]=digest
    for name,object_ in (("selection-report.json",report),
                         ("presentation.json",{
                             "version":VERSION,
                             "manifestSha256":digest,
                             "records":presentation,
                             "sourceOriginalReads":0,
                         })):
        with (dest/name).open("x",encoding="utf-8") as handle:
            json.dump(object_,handle,indent=2,sort_keys=True,allow_nan=False)
            handle.write("\n")
    with (dest/"index.html").open("x",encoding="utf-8") as handle:
        handle.write(render_blind_review(manifest,presentation,digest))
    return {
        "status":"curation_pilot_created","directory":str(dest),
        "manifest":str(manifest_path),
        "selectionReport":str(dest/"selection-report.json"),
        "reviewPage":str(dest/"index.html"),
        "manifestSha256":digest,"candidateWindows":len(manifest["records"]),
        "uniqueSources":report["selectedUniqueSources"],
        "verifiedReviewProxies":verified,
        "missingReviewProxies":report["missingReviewProxies"],
        "originalSourceReads":0,"modelCalls":0,
        "humanLabelsCreated":0,"trainingEligibleRecords":0,
    }

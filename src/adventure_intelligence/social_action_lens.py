"""Offline Social Lens: rights-gated social video preference baseline.

Models observable *raw-footage* features, not TikTok/Instagram recommendation
algorithms or actual future view counts. No network, scraping, downloading,
publishing, or private-video uploads. Same-creator, same-platform, same-sport
pairwise signal and creator-disjoint heldout evaluation are essential guardrails.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import date
from hashlib import sha256
from pathlib import Path
from html import escape
import json
import math
import random

from .archive_pilot import _open_catalog
from .safety import protect_source_output

VERSION="social-action-lens-pairwise-v1"
DATA_VERSION="licensed-social-action-features-v1"
USER_VERSION="klip-social-feature-review-v1"
FEATURE_VERSION="action-raw-human-rubric-v1"
FEATURES=(
    "subjectVisibility","framingQuality","imageClarity","cameraStability",
    "motionCoherence","actionContinuity","eventInterest",
    "narrativeCompleteness","visualNovelty","earlyVisualHook",
)
SPORTS={"ski","bike","snorkeling","other"}
PLATFORMS={"tiktok","instagram","licensed_reference"}
ACCESS={"creator_export","approved_research_api","licensed_dataset"}
MAX_ITEMS=5000
_MIN_VIEWS=200
# These are exploratory pre-registered *engagement proxies*, not coefficients
# estimated to predict watch time or measures of true creative quality.
ENGAGEMENT_WEIGHTS={"shareCount":1.0,"saveCount":1.5,
                    "commentCount":.4,"likeCount":.08}


def _json(path:Path,limit:int=20_000_000)->dict:
    if not path.is_file():
        raise ValueError(
            f"Input JSON does not exist: {path}. Check the workflow stage "
            "and generate this exact input before running its dependent command. "
            "For Social Lens feature/temporal/pair files, run "
            "'klip social-lens-workflow-status' for a non-destructive diagnosis. "
            "External social reference corpora are a separate workflow.")
    if path.stat().st_size>limit:
        raise ValueError(f"Input JSON exceeds {limit} bytes: {path}")
    document=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document,dict):
        raise ValueError("JSON document must be an object")
    return document


def _strict_features(item:dict)->list[float]:
    if not isinstance(item,dict) or set(item)!=set(FEATURES):
        raise ValueError("Feature schema mismatch; missing values are not zero")
    values=[]
    for name in FEATURES:
        v=item[name]
        if type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1:
            raise ValueError("Every raw-footage feature needs an observed 0..1 value")
        values.append(float(v))
    return values


def _date(value:str)->date:
    if type(value) is not str or len(value)!=10:
        raise ValueError("Dates must be ISO YYYY-MM-DD")
    try:
        parsed=date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid metric snapshot date") from exc
    if parsed.isoformat()!=value:
        raise ValueError("Invalid ISO date")
    return parsed


def _social_record(item:dict)->dict:
    if not isinstance(item,dict):
        raise ValueError("Social records must be objects")
    if (type(item.get("clipId")) is not str
            or not 8<=len(item["clipId"])<=128
            or type(item.get("creatorGroup")) is not str
            or not 8<=len(item["creatorGroup"])<=128
            or type(item.get("contentGroup")) is not str
            or not 8<=len(item["contentGroup"])<=128):
        raise ValueError("Pseudonymous stable clip/creator/content IDs are required")
    if item.get("platform") not in PLATFORMS or item.get("sport") not in SPORTS:
        raise ValueError("Unsupported platform or sport")
    if item.get("featureMethod")!=FEATURE_VERSION:
        raise ValueError("All media features must use one identical observed rubric version")
    rights=item.get("rights")
    if (not isinstance(rights,dict)
            or rights.get("videoModelTrainingAllowed") is not True
            or rights.get("metadataAccess") not in ACCESS
            or not isinstance(rights.get("sourceLicense"),str)
            or not 5<=len(rights["sourceLicense"])<=240):
        raise ValueError("Each record needs independently documented media-training rights")
    if item.get("promoted") is not False:
        raise ValueError("Unknown or promoted placements cannot enter organic engagement training")
    duration=item.get("durationSec")
    if type(duration) not in (int,float) or not math.isfinite(duration) or not 4<=duration<=180:
        raise ValueError("Invalid short-form video duration")
    m=item.get("metrics")
    if not isinstance(m,dict):
        raise ValueError("Engagement metadata required")
    views=m.get("viewCount")
    if type(views) is not int or not _MIN_VIEWS<=views<=10**12:
        raise ValueError("Minimum measured view count is 200")
    for key in ENGAGEMENT_WEIGHTS:
        value=m.get(key)
        if type(value) is not int or not 0<=value<=10**12:
            raise ValueError("All four engagement fields must be observed nonnegative integers")
        if value>views*3:
            raise ValueError("Implausibly high interaction count relative to views")
    created=_date(m.get("postedDate"))
    measured=_date(m.get("observedDate"))
    age=(measured-created).days
    if age<7 or age>3650:
        raise ValueError("Observe engagement at least seven days after posting")
    return {
        "clipId":item["clipId"],"creatorGroup":item["creatorGroup"],
        "contentGroup":item["contentGroup"],"platform":item["platform"],
        "sport":item["sport"],"durationSec":float(duration),
        "postedDay":created.toordinal(),"ageDays":age,
        "views":views,
        "metricScore":math.log((
            sum(m[key]*weight for key,weight in ENGAGEMENT_WEIGHTS.items())+.5
            )/(views+100)),
        "features":_strict_features(item.get("features")),
    }


def load_training_corpus(path:Path)->list[dict]:
    data=_json(path)
    if data.get("version")!=DATA_VERSION or not isinstance(data.get("items"),list):
        raise ValueError("Expected versioned and licensed social corpus")
    if not 6<=len(data["items"])<=MAX_ITEMS:
        raise ValueError(
            "Training needs 6..5000 fully reviewed, independently authorized "
            "real social-video records. An initialized empty manifest is not "
            "training data. Run social-lens-preflight first.")
    records=[_social_record(x) for x in data["items"]]
    ids=[r["clipId"] for r in records]
    if len(set(ids))!=len(ids):
        raise ValueError("Duplicate social content IDs")
    if len({r["creatorGroup"] for r in records})<3:
        raise ValueError("At least three independent creator groups required")
    return records


def make_pairs(records:list[dict])->list[tuple[dict,dict]]:
    """Exposure-conditioned engagement comparisons within creator and niche.

    This does not remove distribution bias or establish causation. Duplicate
    edits and mismatched observation ages should not form training pairs.
    """
    groups=defaultdict(list)
    for r in records:
        groups[(r["creatorGroup"],r["platform"],r["sport"])].append(r)
    pairs=[]
    for key,group in sorted(groups.items()):
        # Bounded work for a first pilot; source order cannot influence labels.
        group=sorted(group,key=lambda r:r["clipId"])[:64]
        for i,a in enumerate(group):
            for b in group[i+1:]:
                if (a["contentGroup"]==b["contentGroup"]
                        or abs(a["ageDays"]-b["ageDays"])>14
                        or abs(a["postedDay"]-b["postedDay"])>120
                        or max(a["durationSec"],b["durationSec"])>
                            2*min(a["durationSec"],b["durationSec"])):
                    continue
                if abs(a["metricScore"]-b["metricScore"])<math.log(1.2):
                    continue
                pairs.append((a,b) if a["metricScore"]>b["metricScore"]
                             else (b,a))
    return pairs


def split_creators(records:list[dict],seed:int)->tuple[set[str],set[str]]:
    creators=sorted({r["creatorGroup"] for r in records})
    if len(creators)<3:
        raise ValueError("Need at least three independent creators")
    random.Random(f"{VERSION}:{seed}").shuffle(creators)
    n_test=max(1,round(len(creators)*.25))
    return set(creators[n_test:]),set(creators[:n_test])


def _mean_scale(records:list[dict])->tuple[list[float],list[float]]:
    if not records:
        raise ValueError("No training creators")
    center=[sum(r["features"][i] for r in records)/len(records)
            for i in range(len(FEATURES))]
    scale=[max(.15,math.sqrt(sum((r["features"][i]-center[i])**2
                    for r in records)/len(records))) for i in range(len(FEATURES))]
    return center,scale


def _fit(pairs:list[tuple[dict,dict]],scale:list[float],seed:int)->list[float]:
    if len(pairs)<3:
        raise ValueError("Need at least three matched pairs from training creators")
    weights=[0.0]*len(FEATURES)
    shuffled=list(pairs)
    random.Random(f"{VERSION}:training:{seed}").shuffle(shuffled)
    # Pure-Python pairwise logistic preference head. L2 shrinkage and bounded
    # iterations avoid assuming a large pretrained social model.
    for epoch in range(240):
        step=.13/(1+epoch/120)
        for positive,negative in shuffled:
            delta=[(a-b)/s for a,b,s in zip(
                positive["features"],negative["features"],scale)]
            logit=max(-35,min(35,sum(a*b for a,b in zip(weights,delta))))
            derivative=-1/(1+math.exp(logit))
            weights=[w-step*(derivative*d+.018*w)
                     for w,d in zip(weights,delta)]
    return [round(x,8) for x in weights]


def _accuracy(pairs:list[tuple[dict,dict]],weights:list[float],
              scale:list[float])->dict:
    if not pairs:
        return {"pairs":0,"accuracy":None,"ties":0}
    good=0
    ties=0
    for positive,negative in pairs:
        margin=sum(w*(a-b)/s for w,a,b,s in zip(weights,
                    positive["features"],negative["features"],scale))
        if abs(margin)<=1e-10:
            ties+=1
            good+=.5
        elif margin>0:
            good+=1
    return {"pairs":len(pairs),"accuracy":round(good/len(pairs),4),
            "ties":ties}


def train_social_head(corpus:Path,*,seed:int=23)->dict:
    if type(seed) is not int or not 0<=seed<=2**31-1:
        raise ValueError("Invalid seed")
    records=load_training_corpus(corpus)
    train_ids,test_ids=split_creators(records,seed)
    train=[r for r in records if r["creatorGroup"] in train_ids]
    test=[r for r in records if r["creatorGroup"] in test_ids]
    train_pairs=make_pairs(train)
    test_pairs=make_pairs(test)
    if not test_pairs:
        raise ValueError(
            "No matched pairs in held-out creator group; gather more real "
            "creator videos before interpreting a trained preference head.")
    center,scale=_mean_scale(train)
    weights=_fit(train_pairs,scale,seed)
    uniform=[1/len(FEATURES)]*len(FEATURES)
    source_fingerprint=sha256(corpus.read_bytes()).hexdigest()
    return {
        "version":VERSION,"featureVersion":FEATURE_VERSION,
        "featureNames":list(FEATURES),
        "featureMean":[round(x,8) for x in center],
        "featureScale":[round(x,8) for x in scale],
        "weights":weights,
        "seed":seed,"corpusSha256":source_fingerprint,
        "trainingSummary":{
            "totalClips":len(records),
            "creators":len(train_ids)+len(test_ids),
            "trainingCreators":len(train_ids),"heldOutCreators":len(test_ids),
            "trainPairs":len(train_pairs),"heldOutPairs":len(test_pairs),
            "sports":dict(sorted(Counter(r["sport"] for r in records).items())),
            "platforms":dict(sorted(Counter(r["platform"] for r in records).items())),
            "sameCreatorSamePlatformSameSportPairing":True,
            "sameContentGroupPairsAllowed":False,
            "creatorDisjoint":train_ids.isdisjoint(test_ids),
        },
        "heldOutPairwise":_accuracy(test_pairs,weights,scale),
        "heldOutUniformBaseline":_accuracy(test_pairs,uniform,scale),
        "calibratedSocialEngagementProbability":None,
        "predictedViews":None,
        "calibratedPersonalKeepProbability":None,
        "limitations":[
            "Engagement per view is associated with content, not a causal quality label",
            "Exposure is not randomized; platform algorithms, editing, timing and trends confound outcomes",
            "Even creator-disjoint test examples can share a trend; content/time deduplication remains necessary",
            "Small held-out samples have high uncertainty; null held-out accuracy is not success",
            "Source media licensing and API-use rights must independently permit model training",
            "Human rubric scores are not automatic raw-video features; validation is required before deployment",
            "This is a lightweight offline linear preference head, not a fine-tuned video foundation model",
        ],
    }


def _score(features:dict,model:dict)->float:
    if (model.get("version")!=VERSION or
            model.get("featureVersion")!=FEATURE_VERSION or
            model.get("featureNames")!=list(FEATURES)):
        raise ValueError("Incompatible Social Lens model or raw-feature version")
    vals=_strict_features(features)
    w,mean,scale=(model.get(k) for k in
                  ("weights","featureMean","featureScale"))
    if (not all(isinstance(a,list) and len(a)==len(FEATURES)
                for a in (w,mean,scale)) or
            not all(type(v) in (float,int) and math.isfinite(v) for a in
                    (w,mean,scale) for v in a) or
            any(s<=0 for s in scale)):
        raise ValueError("Invalid social-head coefficients")
    return round(sum(float(weight)*(value-center)/denom
                 for weight,value,center,denom in zip(w,vals,mean,scale)),6)


def _identity(asset:str,start:int,end:int)->str:
    return f"{asset}:{start}:{end}"


def _rank_spread_indices(length:int)->list[int]:
    """Deterministically expose both high and low ranking strata, not only hits."""
    if length<=0:
        return []
    result=[0]
    if length>1:
        result.append(length-1)
    intervals=[(0,length-1)]
    while intervals:
        intervals.sort(key=lambda x:(-(x[1]-x[0]),x[0]))
        a,b=intervals.pop(0)
        mid=(a+b)//2
        if mid in result or mid<=a or mid>=b:
            continue
        result.append(mid)
        intervals.extend(((a,mid),(mid,b)))
    return result


def _stratified_personal_review(rows:list[dict],limit:int)->list[dict]:
    """Small annotation budgets include every represented sport and rank tier."""
    if len(rows)<=limit:
        return rows
    buckets=defaultdict(list)
    for row in rows:
        buckets[row["sport"]].append(row)
    # Each sport contributes high, low, then mid-ranked examples. The audit
    # still reports selected candidate coverage; no archive recall is inferred.
    ordered={sport:[items[i] for i in _rank_spread_indices(len(items))]
             for sport,items in buckets.items()}
    output=[]
    for _ in range(limit):
        available=[sport for sport in ("ski","bike","snorkeling","other")
                   if ordered.get(sport)]
        if not available:
            break
        sport=min(available,key=lambda x:(sum(v["sport"]==x for v in output),
                                            ("ski","bike","snorkeling","other").index(x)))
        output.append(ordered[sport].pop(0))
    return output


def feature_template(study:dict,*,limit:int=300)->dict:
    if type(limit) is not int or not 1<=limit<=300:
        raise ValueError("Feature review limit must be 1..300")
    if study.get("version")!="archive-value-study-v1":
        raise ValueError("Expected an archive value study report")
    rows=study.get("rankedCandidates",[])
    if not isinstance(rows,list):
        raise ValueError("Missing ranked candidates")
    output=[]
    seen=set()
    for item in rows:
        aid=item.get("sourceAssetId")
        start,end=item.get("startMs"),item.get("endMs")
        if (not isinstance(aid,str) or len(aid)!=64
                or type(start) is not int or type(end) is not int
                or not 0<=start<end):
            continue
        ident=_identity(aid,start,end)
        if ident in seen:
            continue
        seen.add(ident)
        output.append({"assetId":aid,"startMs":start,"endMs":end,
                       "sport":("other" if item.get("activityGroup")=="unresolved"
                                else item.get("activityGroup")),
                       "featureVersion":FEATURE_VERSION,
                       "featureMethod":"unassessed",
                       "features":{name:None for name in FEATURES}})
    selected=_stratified_personal_review(output,limit)
    return {"version":USER_VERSION,"source":"archive-value-study-v1",
            "featureVersion":FEATURE_VERSION,"items":selected,
            "samplingPolicy":("sport_and_rank_tier_spread"
                              if len(output)>limit else "all_available_candidates"),
            "studyCandidateCount":len(output),
            "note":"Nulls are UNKNOWN, not bad. For smaller pilots sample across sport and ranking tiers. Observe or qualify a common extractor before scoring."}


def _user_features(document:dict)->dict[str,dict]:
    if (document.get("version")!=USER_VERSION
            or document.get("featureVersion")!=FEATURE_VERSION
            or not isinstance(document.get("items"),list)):
        raise ValueError("Expected versioned personal clip feature manifest")
    result={}
    for item in document["items"]:
        aid=item.get("assetId")
        start,end=item.get("startMs"),item.get("endMs")
        if (not isinstance(aid,str) or len(aid)!=64
                or any(x not in "0123456789abcdef" for x in aid)
                or type(start) is not int or type(end) is not int
                or not 0<=start<end or item.get("sport") not in SPORTS):
            raise ValueError("Invalid personal candidate identity or sport")
        ident=_identity(aid,start,end)
        if ident in result:
            raise ValueError("Duplicate clip candidate feature record")
        if (item.get("featureVersion")!=FEATURE_VERSION
                or item.get("featureMethod")!=FEATURE_VERSION):
            # Leave incomplete manual templates out of comparison.
            if item.get("featureMethod")=="unassessed" and (
                    isinstance(item.get("features"),dict)
                    and all(x is None for x in item["features"].values())):
                continue
            raise ValueError("Social comparison requires observed common-rubric features")
        result[ident]={"features":item.get("features"),"sport":item["sport"]}
        _strict_features(item.get("features"))
    return result


def compare_to_klip(model:dict,study:dict,candidate_features:dict,
                    *,top_k:int=20)->dict:
    if type(top_k) is not int or not 1<=top_k<=100:
        raise ValueError("top_k must be 1..100")
    if study.get("version")!="archive-value-study-v1":
        raise ValueError("Expected frozen archive-value-study-v1 report")
    shared=_user_features(candidate_features)
    rows=[]
    seen=set()
    total=0
    for c in study.get("rankedCandidates",[]):
        total+=1
        aid,start,end=(c.get(x) for x in
                       ("sourceAssetId","startMs","endMs"))
        if not isinstance(aid,str) or type(start) is not int or type(end) is not int:
            continue
        key=_identity(aid,start,end)
        if key not in shared or key in seen:
            continue
        seen.add(key)
        sport=shared[key]["sport"]
        if c.get("activityGroup")!=sport:
            raise ValueError("Cross-sport feature/study mismatch")
        klip_score=c.get("score")
        if (type(klip_score) not in (float,int) or
                not math.isfinite(klip_score) or not 0<=klip_score<=1):
            continue
        observed=shared[key]["features"]
        contributions=[
            {"feature":name,"contribution":round(
                model["weights"][i]*(observed[name]-model["featureMean"][i])
                /model["featureScale"][i],6)}
            for i,name in enumerate(FEATURES)]
        contributions.sort(key=lambda x:(-abs(x["contribution"]),x["feature"]))
        rows.append({"clipId":key,"assetId":aid,"sport":sport,
                     "startMs":start,"endMs":end,
                     "klipHeuristicScore":round(float(klip_score),6),
                     "socialLensScore":_score(observed,model),
                     "topSocialFeatureContributions":contributions[:4]})
    per_sport={}
    for sport in sorted(SPORTS):
        group=[r for r in rows if r["sport"]==sport]
        if not group:
            continue
        klip=sorted(group,key=lambda r:(-r["klipHeuristicScore"],r["clipId"]))
        social=sorted(group,key=lambda r:(-r["socialLensScore"],r["clipId"]))
        k=min(top_k,len(group))
        selected_klip={r["clipId"] for r in klip[:k]}
        selected_social={r["clipId"] for r in social[:k]}
        per_sport[sport]={
            "comparableClips":len(group),"topK":k,
            "topKOverlap":len(selected_klip&selected_social),
            "topKOverlapFraction":round(len(selected_klip&selected_social)/k,4),
            "klipTop":[r["clipId"] for r in klip[:k]],
            "socialLensTop":[r["clipId"] for r in social[:k]],
            "socialPromoted":[r["clipId"] for r in social[:k]
                              if r["clipId"] not in selected_klip],
            "humanKeepAtK":None,
            "meaning":"Rank disagreement among *same* feature-complete candidates, not accuracy",
        }
    return {
        "version":"social-action-lens-comparison-v1",
        "modelVersion":VERSION,"featureVersion":FEATURE_VERSION,
        "sourceStudyVersion":"archive-value-study-v1",
        "socialHeadFeatureWeights":[{"feature":name,"weight":model["weights"][i]}
                                    for i,name in enumerate(FEATURES)],
        "studyRankedCandidateCount":total,
        "featureCompleteComparableCount":len(rows),
        "featureMissingOrUnmatched":max(0,total-len(rows)),
        "bySport":per_sport,
        "rows":rows,
        "socialPredictedViews":None,"klipVsSocialWinnerVerified":False,
        "humanPreferenceAccuracy":None,
        "sourceVideoBytesRead":0,"modelCalls":0,"externalUploads":0,
        "interpretation":[
            "Social-trained head is advisory and may reward platform artifacts",
            "Social score is a relative preference margin, not calibrated reach, retention, or Keep probability",
            "No model can infer missing common-rubric features, especially from three stills",
            "A rank reversal is a candidate for paired human review, not proof Klip made an error",
            "Social and Klip rank the exact same feature-complete subset within each sport",
        ],
    }


def _new_folder(root:Path,destination:Path)->Path:
    dest=Path(destination).expanduser().resolve()
    if (dest==root or not dest.is_relative_to(root) or dest.exists()
            or dest.relative_to(root).parts[0] not in ("reports","exports")):
        raise ValueError("Output must be a NEW directory in workspace reports/ or exports/")
    with closing(_open_catalog(root)) as db:
        protect_source_output(db,dest)
    return dest


def _write_folder(destination:Path,documents:dict)->dict:
    destination.mkdir(mode=0o700,parents=True,exist_ok=False)
    for name,body in documents.items():
        with (destination/name).open("x",encoding="utf-8") as f:
            json.dump(body,f,indent=2,sort_keys=True,allow_nan=False)
            f.write("\n")
    return {key:str(destination/key) for key in documents}


def save_training(workspace:Path,corpus:Path,output_dir:Path,*,seed:int=23)->dict:
    # Separate preflight from actual fitting so a missing corpus never creates
    # a model directory or ambiguous "model failed" message.
    from .social_corpus_onboarding import preflight_corpus
    check=preflight_corpus(corpus,seed=seed)
    if not check["trainingReady"]:
        raise ValueError(
            f"Social Lens cannot train: {check['status']}. "
            f"{check['nextAction']} "
            "Run 'klip social-lens-preflight --corpus PATH' for a full audit.")
    root=Path(workspace).expanduser().resolve()
    dest=_new_folder(root,output_dir)
    model=train_social_head(Path(corpus).expanduser().resolve(),seed=seed)
    outputs=_write_folder(dest,{"model.json":model})
    return {"status":"offline_social_head_trained",
            "model":outputs["model.json"],
            "trainingSummary":model["trainingSummary"],
            "heldOutPairwise":model["heldOutPairwise"],
            "heldOutUniformBaseline":model["heldOutUniformBaseline"],
            "sourceVideosDownloaded":0,"privateVideoUploads":0}


def save_feature_template(workspace:Path,study_file:Path,output_dir:Path,
                          *,limit:int=300)->dict:
    root=Path(workspace).expanduser().resolve()
    dest=_new_folder(root,output_dir)
    doc=feature_template(_json(Path(study_file).expanduser().resolve()),
                         limit=limit)
    outputs=_write_folder(dest,{"feature-template.json":doc})
    return {"status":"unassessed_feature_template",
            "template":outputs["feature-template.json"],
            "candidates":len(doc["items"]),
            "observedFeatures":0,"modelInferenceCalls":0}


def render_social_comparison_html(report:dict)->str:
    """Small, self-contained report without embedded media or external scripts."""
    sections=[]
    for sport,summary in sorted(report["bySport"].items()):
        lines=[]
        data={r["clipId"]:r for r in report["rows"]}
        for ident in summary["socialLensTop"]:
            row=data[ident]
            top=", ".join(x["feature"] for x in
                          row["topSocialFeatureContributions"][:2])
            lines.append("<tr><td>"+escape(ident[:12])+"</td><td>"+
                         escape(f'{row["startMs"]/1000:.1f}–'
                                f'{row["endMs"]/1000:.1f}s')+
                         "</td><td>"+escape(str(row["klipHeuristicScore"]))+
                         "</td><td>"+escape(str(row["socialLensScore"]))+
                         "</td><td>"+escape(top)+"</td></tr>")
        sections.append(
            "<section><h2>"+escape(sport.title())+"</h2><p>"+
            f'{summary["comparableClips"]} matched candidates · '+
            f'top-{summary["topK"]} overlap '+
            f'{summary["topKOverlap"]}/{summary["topK"]}.'+
            "</p><table><thead><tr><th>Asset SHA prefix</th>"
            "<th>Review interval</th><th>Klip heuristic</th>"
            "<th>Social margin</th><th>Largest score contributors</th>"
            "</tr></thead><tbody>"+ "".join(lines)+"</tbody></table></section>")
    if not sections:
        sections.append("<p>No comparable feature-complete clips. "
                        "Null inputs are not filled with guessed quality.</p>")
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Klip · Social Lens comparison</title><style>'
        'body{background:#0f1723;color:#e8edf2;font:14px system-ui;'
        'max-width:1150px;margin:32px auto;padding:0 18px;line-height:1.6}'
        'h1{font-size:1.7rem}h2{color:#efb4a2;font-size:1.18rem}'
        'p{color:#b9c9d5}table{width:100%;border-collapse:collapse}'
        'td,th{text-align:left;padding:10px;border-bottom:1px solid #314256}'
        'table{margin-bottom:30px;overflow-wrap:anywhere}</style></head><body>'
        '<h1>Social Lens vs Klip · same candidate windows</h1><p>'
        'Social margin is NOT a predicted view count. Klip is uncalibrated. '
        'Ranking disagreement is a human-review hypothesis, not a winner. '
        'Only clips with independently observed common-rubric features are compared. '
        'All footage stays local and originals are unchanged.</p>'
        +''.join(sections)+'</body></html>'
    )


def save_comparison(workspace:Path,model_file:Path,study_file:Path,
                    features_file:Path,output_dir:Path,*,top_k:int=20)->dict:
    root=Path(workspace).expanduser().resolve()
    dest=_new_folder(root,output_dir)
    result=compare_to_klip(
        _json(Path(model_file).expanduser().resolve()),
        _json(Path(study_file).expanduser().resolve()),
        _json(Path(features_file).expanduser().resolve()),
        top_k=top_k)
    outputs=_write_folder(dest,{"comparison.json":result})
    with (dest/"index.html").open("x",encoding="utf-8") as handle:
        handle.write(render_social_comparison_html(result))
    return {"status":"paired_ranking_disagreement_audit",
            "report":outputs["comparison.json"],
            "html":str(dest/"index.html"),
            "compared":result["featureCompleteComparableCount"],
            "bySport":{k:{"comparableClips":v["comparableClips"],
                          "topKOverlap":v["topKOverlap"],
                          "topK":v["topK"]}
                       for k,v in result["bySport"].items()},
            "sourceVideosRead":0,"externalUploads":0,
            "rankingAccuracyClaim":False}

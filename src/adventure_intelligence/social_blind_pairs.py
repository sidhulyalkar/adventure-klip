"""Private blinded A/B preferences on exactly verified Klip candidate windows.

Not an external social model, not publication engagement prediction and not
archive recall. The model baseline is evaluated only on chosen eligible
same-sport pairs. No user footage is opened, modified, exported, uploaded, or
deleted; report playback links are verified H264 derivatives only.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from html import escape
from pathlib import Path
from urllib.parse import quote
import json
import math
import os
import random

from .social_action_lens import USER_VERSION, FEATURE_VERSION, _json, _new_folder
from .social_temporal_evidence import VERSION as TEMPORAL_VERSION
from .studio_preview_judge import preview_paths, _check_cached

VERSION="social-blind-pairs-v1"
REVIEW_VERSION="social-blind-pair-reviews-v1"
CHOICES={"a","b","tie","neither"}
REASONS={
    "sustained_action","clear_subject","better_framing","coherent_sequence",
    "interesting_event","visual_novelty","less_repetition","clearer_image",
    "other",
}
SPORTS=("ski","bike","snorkeling","other")
MAX_PAIRS=40
MAX_CANDIDATES=500


def _bounded_json(path:Path)->tuple[dict,str]:
    path=Path(path).expanduser().resolve()
    document=_json(path,limit=20_000_000)
    return document,sha256(path.read_bytes()).hexdigest()


def _clip_key(aid:str,start:int,end:int)->str:
    return f"{aid}:{start}:{end}"


def _good_clip(row:dict)->bool:
    aid=row.get("assetId")
    return (isinstance(aid,str) and len(aid)==64
            and all(c in "0123456789abcdef" for c in aid)
            and type(row.get("startMs")) is int and
            type(row.get("endMs")) is int
            and 0<=row["startMs"]<row["endMs"]
            and 1000<=row["endMs"]-row["startMs"]<=60000
            and row.get("sport") in SPORTS)


def _ready_clips(workspace:Path,study:dict,features:dict,
                 temporal:dict)->tuple[dict[str,list[dict]],dict]:
    if (study.get("version")!="archive-value-study-v1"
            or features.get("version")!=USER_VERSION
            or features.get("featureVersion")!=FEATURE_VERSION
            or temporal.get("version")!=TEMPORAL_VERSION
            or temporal.get("sourceTemplateSha256") is None
            or temporal.get("mode")!="local_preview_temporal_extraction"):
        raise ValueError("Expected completed compatible study, feature template and temporal run")
    if not isinstance(features.get("items"),list) or len(features["items"])>MAX_CANDIDATES:
        raise ValueError("Invalid feature candidate count")
    if not isinstance(temporal.get("candidates"),list) or len(temporal["candidates"])>MAX_CANDIDATES:
        raise ValueError("Invalid temporal candidate list")
    if not isinstance(study.get("rankedCandidates"),list):
        raise ValueError("Missing frozen Klip candidate ranking")
    template={}
    for row in features["items"]:
        if not isinstance(row,dict) or not _good_clip(row):
            raise ValueError("Invalid feature candidate interval")
        key=_clip_key(row["assetId"],row["startMs"],row["endMs"])
        if key in template:
            raise ValueError("Duplicate reviewed feature candidate")
        template[key]=row["sport"]
    scores={}
    for row in study["rankedCandidates"]:
        if not isinstance(row,dict):
            continue
        aid,start,end=(row.get(k) for k in ("sourceAssetId","startMs","endMs"))
        if not isinstance(aid,str) or type(start) is not int or type(end) is not int:
            continue
        key=_clip_key(aid,start,end)
        score=row.get("score")
        sport=row.get("activityGroup")
        if (key in template and sport==template[key]
                and type(score) in (float,int)
                and math.isfinite(score) and 0<=score<=1):
            if key in scores:
                raise ValueError("Duplicate frozen study candidate identity")
            scores[key]=float(score)
    by_sport=defaultdict(list)
    dropped=Counter()
    for row in temporal["candidates"]:
        if not isinstance(row,dict) or not _good_clip(row):
            dropped["invalidTemporalCandidate"]+=1
            continue
        aid,start,end=(row[k] for k in ("assetId","startMs","endMs"))
        key=_clip_key(aid,start,end)
        if key not in template:
            dropped["notInFrozenFeatureTemplate"]+=1
            continue
        if row["sport"]!=template[key] or key not in scores:
            dropped["notComparableToFrozenKlipRanking"]+=1
            continue
        visual=row.get("visualTemporal")
        if (row.get("status")!="temporal_visual_metrics_observed"
                or row.get("previewVerified") is not True
                or not isinstance(visual,dict)
                or type(visual.get("frameCount")) is not int
                or visual["frameCount"]<5):
            dropped["noMeasuredVerifiedTemporalFrames"]+=1
            continue
        video,receipt=preview_paths(workspace,aid,start,end)
        if not _check_cached(video,receipt,aid=aid,start_ms=start,end_ms=end):
            dropped["staleOrTamperedPreview"]+=1
            continue
        observations={}
        source_metrics=row.get("cachedMotionAcoustics")
        embeddings=row.get("semanticTemporal")
        for label,container,keyname in (
            ("frameChangeMedian",visual,"frameChangeMedian"),
            ("frameChangeP90",visual,"frameChangeP90"),
            ("longestHighChangeRunSec",visual,"longestHighChangeRunSec"),
            ("spatialGradientEnergyMedian",visual,"spatialGradientEnergyMedian"),
            ("darkPixelFractionMedian",visual,"darkPixelFractionMedian"),
            ("brightPixelFractionMedian",visual,"brightPixelFractionMedian"),
            ("sourceMotionMedian",source_metrics,"motionChangeMedian"),
            ("sourceAudioRmsMedian",source_metrics,"audioRmsMedian"),
            ("semanticAdjacentCosine",embeddings,"medianAdjacentCosineSimilarity"),
        ):
            value=container.get(keyname) if isinstance(container,dict) else None
            observations[label]=round(float(value),6) if (
                type(value) in (int,float) and math.isfinite(value)) else None
        by_sport[row["sport"]].append({
            "clipId":key,"assetId":aid,"startMs":start,"endMs":end,
            "sport":row["sport"],"klipScore":round(scores[key],6),
            "verifiedPreview":str(video),"observedEvidence":observations,
        })
    return by_sport,{
        "templateCandidates":len(template),"rankedTemplateCandidates":len(scores),
        "measuredCandidates":len(temporal["candidates"]),
        "eligibleUnique":sum(len(v) for v in by_sport.values()),
        "excluded":dict(sorted(dropped.items())),
    }


def _pair_candidates(clips:list[dict],*,seed:int,sport:str)->list[tuple[dict,dict,str]]:
    """Promote ranking contrasts *and* close scores within one sport.

    Sources must differ and a clip appears in at most two pairs. Scores
    select review assignments, not human outcomes.
    """
    ranked=sorted(clips,key=lambda c:(-c["klipScore"],c["clipId"]))
    suggestions=[]
    for i,a in enumerate(ranked):
        for b in ranked[i+1:]:
            if a["assetId"]==b["assetId"]:
                continue
            diff=abs(a["klipScore"]-b["klipScore"])
            suggestions.append((a,b,"rank_contrast" if diff>=.12 else "close_rank",
                                diff))
    rng=random.Random(f"{VERSION}:{sport}:{seed}")
    rng.shuffle(suggestions)
    suggestions.sort(key=lambda p:(
        0 if p[2]=="rank_contrast" else 1,
        -p[3] if p[2]=="rank_contrast" else p[3],
    ))
    used=Counter()
    matches=[]
    pairs_seen=set()
    # Pairing alternates contrast, near ties when possible; this avoids
    # spending the whole budget on obvious top-versus-bottom shots.
    contrast=[s for s in suggestions if s[2]=="rank_contrast"]
    hard=[s for s in suggestions if s[2]=="close_rank"]
    while contrast or hard:
        selected=None
        for group in (contrast,hard) if len(matches)%2==0 else (hard,contrast):
            while group:
                a,b,role,_=group.pop(0)
                identity=tuple(sorted((a["clipId"],b["clipId"])))
                if (identity in pairs_seen or used[a["clipId"]]>=2
                        or used[b["clipId"]]>=2):
                    continue
                selected=a,b,role
                break
            if selected is not None:
                break
        if selected is None:
            break
        a,b,role=selected
        used[a["clipId"]]+=1
        used[b["clipId"]]+=1
        pairs_seen.add(tuple(sorted((a["clipId"],b["clipId"]))))
        matches.append((a,b,role))
        if len(matches)>=MAX_PAIRS:
            break
    return matches


def make_pair_study(workspace:Path,study_file:Path,features_file:Path,
                    temporal_file:Path,*,seed:int=104,max_pairs:int=20)->tuple[dict,list[dict]]:
    if type(seed) is not int or not 0<=seed<=2**31-1:
        raise ValueError("Invalid pairing seed")
    if type(max_pairs) is not int or not 1<=max_pairs<=MAX_PAIRS:
        raise ValueError("Pair budget must be 1..40")
    root=Path(workspace).expanduser().resolve()
    docs=[]
    for p in (study_file,features_file,temporal_file):
        path=Path(p).expanduser().resolve()
        if path==root or not path.is_relative_to(root) or (
                path.relative_to(root).parts[0] not in ("reports","exports")):
            raise ValueError("Every study input must be inside workspace reports/ or exports/")
        docs.append(_bounded_json(path))
    (study,study_sha),(features,features_sha),(temporal,temporal_sha)=docs
    if temporal["sourceTemplateSha256"]!=features_sha:
        raise ValueError("Temporal evidence was generated from a different feature template; cannot join")
    groups,coverage=_ready_clips(root,study,features,temporal)
    pools={sport:_pair_candidates(groups[sport],seed=seed,sport=sport)
           for sport in SPORTS}
    selected=[]
    positions=Counter()
    for _ in range(max_pairs):
        available=[sport for sport in SPORTS if pools[sport]]
        if not available:
            break
        sport=min(available,key=lambda s:(positions[s],SPORTS.index(s)))
        a,b,role=pools[sport].pop(0)
        # Unbiased presentation: deterministic 50/50 A/B assignment, with
        # score never emitted in the UI's blind review data.
        swap=random.Random(f"{VERSION}:side:{seed}:{a['clipId']}:{b['clipId']}"
                          ).randint(0,1)==1
        if swap:
            a,b=b,a
        pid=sha256((
            f"{VERSION}:{seed}:{sport}:{a['clipId']}:{b['clipId']}"
        ).encode()).hexdigest()[:24]
        selected.append({
            "pairId":pid,"sport":sport,
            "a":a,"b":b,
            "selectionContrast":role,
            "higherKlipSide":(
                "a" if a["klipScore"]>b["klipScore"] else
                "b" if b["klipScore"]>a["klipScore"] else "tie"),
            "sourceOutingSplitVerified":False,
            "blindToReviewer":True,
        })
        positions[sport]+=1
    # Scramble sport order so review UI is not fixed by a ranking rule.
    random.Random(f"{VERSION}:order:{seed}").shuffle(selected)
    report={
        "version":VERSION,"seed":seed,"maxPairsRequested":max_pairs,
        "pairCount":len(selected),
        "pairsBySport":dict(sorted(Counter(x["sport"] for x in selected).items())),
        "pairTypes":dict(sorted(Counter(x["selectionContrast"] for x in selected).items())),
        "coverage":coverage,"studySha256":study_sha,
        "featureTemplateSha256":features_sha,
        "temporalEvidenceSha256":temporal_sha,
        "pairs":selected,
        "humanVotesCollected":0,"reviewIsBlinded":True,
        "originalMediaOpened":False,"newDecodes":0,"externalUploads":0,
        "estimatedArchiveRecall":None,"personalRankingAccuracy":None,
        "limits":[
            "Only high/low ranked *candidates* with existing verified previews are compared; no missed-source recall",
            "At most two pairs may reuse a clip; pair votes are correlated and cannot be treated as independent",
            "Scores select pairs and may reflect earlier curation; this is not a held-out unbiased model trial",
            "Different recordings do not certify different outings, people, camera mounts or capture conditions",
            "Two clips in the same sport can still differ in setting and subject; engagement is not measured",
            "The blind UI contains no Klip scores or model suggestions, but workspace JSON is owner-readable",
            "No source deletion, automatic Keep, training or external network access",
        ],
    }
    return report,selected


def _review_html(report:dict,*,root:Path,dest:Path,manifest_sha:str)->str:
    blind=[]
    for item in report["pairs"]:
        sides={}
        for side in ("a","b"):
            row=item[side]
            source=Path(row["verifiedPreview"])
            if not source.is_relative_to(root/"reports"/"studio-preview-cache-v1"):
                raise ValueError("Pair preview escaped private verified cache")
            relative=os.path.relpath(source,dest)
            sides[side]=quote(relative,safe="/.")
        blind.append({
            "pairId":item["pairId"],
            "sport":item["sport"],
            "aUrl":sides["a"],"bUrl":sides["b"],
        })
    script_data=json.dumps(blind,ensure_ascii=True,separators=(",",":"))
    # The only strings interpolated into HTML/JS are deterministic hex IDs,
    # sport enum strings and relative URL-quoted derived cache paths.
    script="""const pairs=__PAIRS__;
const expected="__DIGEST__";
const decisions=new Map();
let active=0;
const $=id=>document.getElementById(id);
const reasons=["sustained_action","clear_subject","better_framing",
 "coherent_sequence","interesting_event","visual_novelty",
 "less_repetition","clearer_image","other"];
function stopBoth(){for(const v of document.querySelectorAll('video')){v.pause();}}
function show(){
 if(!pairs.length){$('stage').hidden=true;$('empty').hidden=false;return;}
 const p=pairs[active];stopBoth();
 $('sport').textContent=p.sport.toUpperCase();
 $('count').textContent=(active+1)+" / "+pairs.length;
 $('left').src=p.aUrl;$('right').src=p.bUrl;
 const entry=decisions.get(p.pairId);
 $('note').value=entry?.notes||"";
 for(const r of document.querySelectorAll('.reason'))
   r.checked=!!entry?.reasons?.includes(r.value);
 $('choice').textContent=entry?
   "Saved choice: "+entry.choice:"Choose the clip worth keeping";
 $('progress').textContent=decisions.size+" / "+pairs.length+" assessed";
 $('previous').disabled=active===0;$('next').disabled=active===pairs.length-1;
}
function choose(choice){
 const pair=pairs[active];
 decisions.set(pair.pairId,{
  pairId:pair.pairId,choice:choice,
  reasons:[...document.querySelectorAll('.reason:checked')].map(e=>e.value),
  notes:$('note').value.trim().slice(0,500)});
 $('choice').textContent="Saved choice: "+choice;
 $('progress').textContent=decisions.size+" / "+pairs.length+" assessed";
}
function move(n){active=Math.max(0,Math.min(pairs.length-1,active+n));show();}
function exportReviews(){
 const body={version:"social-blind-pair-reviews-v1",
             manifestSha256:expected,
             reviews:[...decisions.values()]};
 const blob=new Blob([JSON.stringify(body,null,2)+"\\n"],
                     {type:"application/json"});
 const url=URL.createObjectURL(blob);
 const a=document.createElement("a");
 a.href=url;a.download="klip-paired-review-"+expected.slice(0,12)+".json";
 document.body.append(a);a.click();a.remove();
 setTimeout(()=>URL.revokeObjectURL(url),1000);
}
async function loadReviews(ev){
 const file=ev.target.files?.[0];
 if(!file)return;
 try{
  if(file.size>250000)throw Error("Review export too large");
  const body=JSON.parse(await file.text());
  if(body.version!=="social-blind-pair-reviews-v1"||
     body.manifestSha256!==expected||!Array.isArray(body.reviews))
    throw Error("Review export is for a different study");
  const ids=new Set(pairs.map(x=>x.pairId)),next=new Map();
  for(const item of body.reviews){
   if(!ids.has(item.pairId)||!["a","b","tie","neither"].includes(item.choice)
      ||next.has(item.pairId)||!Array.isArray(item.reasons)
      ||item.reasons.some(r=>!reasons.includes(r))
      ||typeof item.notes!=="string"||item.notes.length>500)
       throw Error("Invalid reviewed-pair record");
   next.set(item.pairId,item);
  }
  decisions.clear();for(const [key,value] of next)decisions.set(key,value);
  show();$('message').textContent="Imported "+next.size+" prior reviews.";
 }catch(error){$('message').textContent=String(error.message||error);}
}
for(const b of document.querySelectorAll('[data-choice]'))
 b.addEventListener("click",()=>choose(b.dataset.choice));
function updateChosenFields(){
 const entry=decisions.get(pairs[active]?.pairId);
 if(entry)choose(entry.choice);
}
for(const el of document.querySelectorAll('.reason'))
 el.addEventListener('change',updateChosenFields);
$('note').addEventListener('change',updateChosenFields);
$('previous').addEventListener("click",()=>move(-1));
$('next').addEventListener("click",()=>move(1));
$('export').addEventListener("click",exportReviews);
$('import').addEventListener("change",loadReviews);
for(const video of document.querySelectorAll("video"))
 video.addEventListener("play",()=>{for(const other of document.querySelectorAll("video"))
  if(other!==video)other.pause();});
document.addEventListener("keydown",e=>{
 if(["TEXTAREA","INPUT"].includes(document.activeElement?.tagName))return;
 if(e.key==="1")choose("a");
 if(e.key==="2")choose("b");
 if(e.key==="0")choose("tie");
 if(e.key==="-")choose("neither");
 if(e.key==="ArrowLeft")move(-1);
 if(e.key==="ArrowRight")move(1);
});
show();"""
    script=script.replace("__PAIRS__",script_data).replace("__DIGEST__",manifest_sha)
    reasons="".join(
        '<label><input class="reason" type="checkbox" value="'+escape(tag)+'"> '+
        escape(tag.replace("_"," "))+"</label>" for tag in sorted(REASONS))
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Klip · Blind paired clip review</title><style>'
        ':root{color-scheme:dark}*{box-sizing:border-box}'
        'body{margin:0;background:#0f1723;color:#f1f4f8;font:15px system-ui}'
        'header,main{max-width:1180px;margin:auto;padding:20px}'
        'h1{font-size:1.45rem;margin:4px 0}p{color:#aebdca}'
        '.muted{color:#aebdca;font-size:.87rem}.media{display:grid;'
        'grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}'
        'section,article{background:#172435;border:1px solid #324256;'
        'border-radius:14px;padding:14px}video{width:100%;aspect-ratio:16/9;'
        'background:#070c12;border-radius:9px;object-fit:contain}'
        '.choice{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}'
        'button{border:1px solid #52657b;border-radius:10px;padding:12px 17px;'
        'background:#27384b;color:#fff;cursor:pointer;font:inherit}'
        'button:hover{border-color:#f0aa99}button:disabled{opacity:.35;cursor:default}'
        'button[data-choice="a"],button[data-choice="b"]{background:#36445d}'
        'textarea{width:100%;border-radius:9px;background:#121c2b;color:white;'
        'border:1px solid #53657b;padding:10px;margin-top:8px}'
        '.reasons{display:flex;flex-wrap:wrap;gap:7px}'
        '.reasons label{border-radius:9px;padding:9px;background:#23344a}'
        '.tools{display:flex;flex-wrap:wrap;gap:12px;align-items:center}'
        '@media(max-width:700px){.media{grid-template-columns:1fr}}'
        '</style></head><body><header><h1>Klip · A/B preference study</h1>'
        '<p>Watch both clips, then choose which original moment you would save. '
        'This is a private blind comparison. No model scores, platform engagement '
        'claims, source edits or automatic labels.</p>'
        '<div class="tools"><span id="progress">0 assessed</span>'
        '<span id="count"></span><span id="sport"></span></div></header>'
        '<main><div id="empty" hidden><p>No independent verified clip pairs '
        'qualified. Run the temporal study on more different source recordings.</p></div>'
        '<div id="stage"><div class="media">'
        '<article><strong>A</strong><video id="left" controls preload="metadata" '
        'playsinline></video></article>'
        '<article><strong>B</strong><video id="right" controls preload="metadata" '
        'playsinline></video></article></div>'
        '<section><strong id="choice">Choose the clip worth keeping</strong>'
        '<div class="choice"><button type="button" data-choice="a">1 · Prefer A</button>'
        '<button type="button" data-choice="b">2 · Prefer B</button>'
        '<button type="button" data-choice="tie">0 · Tie</button>'
        '<button type="button" data-choice="neither">− · Neither</button></div>'
        '<p class="muted">Optional: why was the preferred clip better?</p>'
        '<div class="reasons">'+reasons+'</div>'
        '<textarea id="note" rows="2" maxlength="500" placeholder="Optional notes…"></textarea>'
        '<div class="tools"><button id="previous" type="button">Previous</button>'
        '<button id="next" type="button">Next</button></div></section></div>'
        '<section><h2>Local review export</h2>'
        '<p>Export periodically to save your decisions. You can reimport the '
        'same file to resume. Move the downloaded JSON anywhere convenient '
        'and pass it to the results command; do not rename videos.</p>'
        '<div class="tools"><button id="export" type="button">Export JSON reviews</button>'
        '<label>Resume from JSON <input id="import" type="file" accept=".json,application/json"></label>'
        '</div><p id="message" role="status" class="muted"></p></section>'
        '</main><script>'+script+'</script></body></html>'
    )


def write_pair_study(workspace:Path,study_file:Path,features_file:Path,
                     temporal_file:Path,output_dir:Path,*,seed:int=104,
                     max_pairs:int=20)->dict:
    root=Path(workspace).expanduser().resolve()
    dest=_new_folder(root,output_dir)
    report,pairs=make_pair_study(root,study_file,features_file,temporal_file,
                                 seed=seed,max_pairs=max_pairs)
    dest.mkdir(mode=0o700,parents=True,exist_ok=False)
    payload=json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+"\n"
    with (dest/"pairs.json").open("x",encoding="utf-8") as f:
        f.write(payload)
    fingerprint=sha256(payload.encode()).hexdigest()
    with (dest/"index.html").open("x",encoding="utf-8") as f:
        f.write(_review_html(report,root=root,dest=dest,manifest_sha=fingerprint))
    return {
        "status":"blind_paired_review_created" if pairs else "no_comparable_verified_pairs",
        "manifest":str(dest/"pairs.json"),"html":str(dest/"index.html"),
        "manifestSha256":fingerprint,"pairs":len(pairs),
        "pairsBySport":report["pairsBySport"],
        "coverage":report["coverage"],
        "originalVideosRead":0,"newModelCalls":0,"externalUploads":0,
        "humanLabelsWritten":0,
    }


def evaluate_pair_reviews(manifest_file:Path,reviews_file:Path)->dict:
    path=Path(manifest_file).expanduser().resolve()
    manifest,manifest_sha=_bounded_json(path)
    if manifest.get("version")!=VERSION or not isinstance(manifest.get("pairs"),list):
        raise ValueError("Expected frozen blind paired review manifest")
    export=_json(Path(reviews_file).expanduser().resolve(),limit=250000)
    if (export.get("version")!=REVIEW_VERSION
            or export.get("manifestSha256")!=manifest_sha
            or not isinstance(export.get("reviews"),list)
            or len(export["reviews"])>MAX_PAIRS):
        raise ValueError("Review export version or manifest fingerprint mismatch")
    assignments={}
    for pair in manifest["pairs"]:
        if not isinstance(pair,dict) or not isinstance(pair.get("pairId"),str):
            raise ValueError("Invalid paired assignment")
        pid=pair["pairId"]
        if pid in assignments:
            raise ValueError("Duplicate pair identity")
        if pair.get("sport") not in SPORTS:
            raise ValueError("Invalid frozen sport")
        for side in ("a","b"):
            clip=pair.get(side)
            if not isinstance(clip,dict) or not _good_clip({
                "assetId":clip.get("assetId"),"startMs":clip.get("startMs"),
                "endMs":clip.get("endMs"),"sport":clip.get("sport")}):
                raise ValueError("Invalid frozen clip identity")
            if clip["sport"]!=pair["sport"]:
                raise ValueError("Cross-sport pair rejected")
        assignments[pid]=pair
    assessed={}
    for record in export["reviews"]:
        if not isinstance(record,dict):
            raise ValueError("Invalid review record")
        pid=record.get("pairId")
        choice=record.get("choice")
        why=record.get("reasons")
        note=record.get("notes")
        if (pid not in assignments or pid in assessed
                or choice not in CHOICES or not isinstance(why,list)
                or len(why)>len(REASONS) or len(set(why))!=len(why)
                or any(x not in REASONS for x in why)
                or type(note) is not str or len(note)>500):
            raise ValueError("Invalid or duplicate blind review response")
        assessed[pid]=record
    sport_counts=defaultdict(Counter)
    wins=Counter()
    top_reason=Counter()
    disagreement=[]
    clips_seen=set()
    for pid,record in assessed.items():
        pair=assignments[pid]
        sport=pair["sport"]
        choice=record["choice"]
        preferred=pair["higherKlipSide"]
        label=(
            "tie_or_both_unwanted" if choice in ("tie","neither") else
            "score_tie_unresolved" if preferred=="tie" else
            "agrees_with_klip" if choice==preferred else
            "disagrees_with_klip")
        sport_counts[sport][label]+=1
        sport_counts[sport]["assessed"]+=1
        wins[label]+=1
        top_reason.update(record["reasons"])
        clips_seen.update((pair["a"]["assetId"],pair["b"]["assetId"]))
        if label=="disagrees_with_klip":
            preferred_item=pair[choice]
            nonpreferred_item=pair["b" if choice=="a" else "a"]
            differences={}
            for field,value in preferred_item.get("observedEvidence",{}).items():
                other=nonpreferred_item.get("observedEvidence",{}).get(field)
                differences[field]=(round(value-other,6)
                                    if type(value) in (int,float)
                                    and type(other) in (int,float) else None)
            disagreement.append({
                "pairId":pid,"sport":sport,
                "chosenSide":choice,"klipHigherSide":preferred,
                "scoreDifference":round(abs(pair["a"]["klipScore"]-
                                            pair["b"]["klipScore"]),6),
                "observedCueDeltasChosenMinusRejected":differences,
                "reasons":record["reasons"],
                "notes":record["notes"],
            })
    preferred_total=wins["agrees_with_klip"]+wins["disagrees_with_klip"]
    stats={}
    for sport in SPORTS:
        s=sport_counts[sport]
        if not s["assessed"]:
            continue
        decided=s["agrees_with_klip"]+s["disagrees_with_klip"]
        stats[sport]={
            **dict(s),
            "decisiveComparisons":decided,
            "klipAgreementOnDecisivePairs":round(s["agrees_with_klip"]/decided,4)
                if decided else None,
        }
    return {
        "version":"social-blind-pair-results-v1",
        "pairStudySha256":manifest_sha,
        "pairsAssigned":len(assignments),"pairsAssessed":len(assessed),
        "pairsUnreviewed":len(assignments)-len(assessed),
        "distinctSourceAssetsAssessed":len(clips_seen),
        "decisiveVotes":preferred_total,
        "agreementWithKlipOnDecisivePairs":(
            round(wins["agrees_with_klip"]/preferred_total,4)
            if preferred_total else None),
        "outcomeCounts":dict(sorted(wins.items())),
        "reasonCounts":dict(sorted(top_reason.items())),
        "perSport":stats,"disagreements":disagreement,
        "modelTrainingPerformed":False,"videoFilesOpened":0,
        "humanVotesWrittenToCatalog":0,"externalUploads":0,
        "accuracyClaimValid":False,
        "limitations":[
            "This is a convenience sample of verified candidates, not a full archive test",
            "A/B pairs reuse some clips and may share outings; independence is not established",
            "The baseline already influenced candidate nomination; true discovery recall is not measured",
            "Small, sport-specific denominators and ambiguous ties must be shown without statistical claims",
            "Reviewer votes are personal preferences, not observed external social engagement",
            "Observed cue differences in a disagreement are descriptive uncalibrated proxies, not explanations of preference or causal mechanisms",
            "The disagreement list is a prioritization queue, not evidence the model should be reweighted automatically",
        ],
    }


def write_pair_results(workspace:Path,manifest_file:Path,reviews_file:Path,
                       output_dir:Path)->dict:
    root=Path(workspace).expanduser().resolve()
    dest=_new_folder(root,output_dir)
    manifest_path=Path(manifest_file).expanduser().resolve()
    if not manifest_path.is_relative_to(root) or (
            manifest_path.relative_to(root).parts[0] not in ("reports","exports")):
        raise ValueError("Paired review manifest must be inside workspace reports/ or exports/")
    result=evaluate_pair_reviews(manifest_path,reviews_file)
    dest.mkdir(mode=0o700,parents=True,exist_ok=False)
    with (dest/"results.json").open("x",encoding="utf-8") as f:
        json.dump(result,f,indent=2,sort_keys=True,allow_nan=False)
        f.write("\n")
    return {
        "status":"blind_pair_preferences_evaluated",
        "report":str(dest/"results.json"),
        "pairsAssessed":result["pairsAssessed"],
        "unreviewed":result["pairsUnreviewed"],
        "agreementOnDecisivePairs":result["agreementWithKlipOnDecisivePairs"],
        "disagreements":len(result["disagreements"]),
        "trainedModels":0,"sourceVideoReads":0,"catalogReviewWrites":0,
    }

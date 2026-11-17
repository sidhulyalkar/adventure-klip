"""Blind marine-encounter review labels, separated from VLM guesses.

Human-assessed animal presence, watchability and story completeness can promote
a clip in review ranking. Nothing here verifies species or measures probabilities.
"""
from __future__ import annotations

from collections import Counter

from .scene_review_narrative import (
    NARRATIVE_FIELDS, extract_narrative, empty_narrative,
)
from hashlib import sha256
from html import escape
import json
import re

VERSION="klip-marine-blind-review-v1"
LEGACY_LABEL_VERSION="klip-marine-owner-labels-v1"
LABEL_VERSION="klip-marine-owner-labels-v2"
PRESENCE=frozenset(("present","possible","absent","unclear"))
ANIMAL_TYPES=frozenset(("ray","fish","turtle","shark","octopus","other","unknown","none"))
BEHAVIOR=frozenset(("swimming","passing","schooling","feeding","interaction","stationary","none","unclear"))
DECISIONS=frozenset(("keep","skip","unsure"))
RATINGS=("visibility","framing","technicalQuality","narrativeCompleteness","behaviorInterest")
WEIGHTS={"visibility":0.28,"framing":0.22,"technicalQuality":0.16,
         "narrativeCompleteness":0.20,"behaviorInterest":0.14}


def _canonical_sha(x: object)->str:
    return sha256(json.dumps(x,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def manifest(candidates: list[dict], plan_sha: str, receipts_sha: str,
             pilot_media_rel: str) -> dict:
    """Frozen source IDs and local preview paths, with NO model guesses in UI."""
    if not isinstance(pilot_media_rel,str):
        raise ValueError("Invalid media path")
    parts=pilot_media_rel.split("/")
    if (len(parts)!=3 or parts[0]!=".." or parts[2]!="media"
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}",parts[1])):
        raise ValueError("Only a sibling frozen pilot media folder is allowed")
    entries=sorted([
        {"recordId":x["id"],"sourceSha256":x["sourceSha256"],
         "sourceStartMs":x["sourceStartMs"],"sourceEndMs":x["sourceEndMs"],
         "media":pilot_media_rel+"/"+x["id"]+".mp4"}
        for x in candidates if x["origin"]=="autoscout_seven_stills"
        and x["activity"]=="snorkeling"
    ],key=lambda x:sha256(("blind-marine-v1:"+x["recordId"]).encode()).hexdigest())
    # The file fingerprint is a stable identifier for merging owner exports,
    # NOT a secret or security signature.
    meta={"version":VERSION,"planSha256":plan_sha,
          "receiptsSha256":receipts_sha,"clips":entries}
    meta["sampleId"]=_canonical_sha(meta)
    return meta


def validate_review(m:dict,doc:dict)->list[dict]:
    if (not isinstance(doc,dict)
            or doc.get("version") not in (LABEL_VERSION, LEGACY_LABEL_VERSION)
            or doc.get("sampleId")!=m["sampleId"]
            or not isinstance(doc.get("reviews"),list)):
        raise ValueError("Marine review must match exactly one frozen sample")
    known={x["recordId"]:x for x in m["clips"]}
    if len(doc["reviews"])>len(known):
        raise ValueError("Too many marine reviews")
    expected={"recordId","sourceSha256","decision","animalPresence","animalType",
              "behavior",*RATINGS,"eventStartMs","eventEndMs"}
    legacy=doc["version"]==LEGACY_LABEL_VERSION
    used=set()
    result=[]
    for item in doc["reviews"]:
        if not isinstance(item,dict) or not expected <= set(item):
            raise ValueError("Missing or unexpected marine owner label fields")
        extra=set(item)-expected
        if extra and (legacy or extra != NARRATIVE_FIELDS):
            raise ValueError("Unrecognized or incomplete human scene fields")
        if extra:
            extract_narrative(item)
        rid=item["recordId"]
        if rid not in known or rid in used or item["sourceSha256"]!=known[rid]["sourceSha256"]:
            raise ValueError("Duplicated, unknown, or mismatched owner record identity")
        used.add(rid)
        if (item["decision"] not in DECISIONS
                or item["animalPresence"] not in PRESENCE
                or item["animalType"] not in ANIMAL_TYPES
                or item["behavior"] not in BEHAVIOR):
            raise ValueError("Invalid animal review label")
        if item["animalPresence"]=="absent" and item["animalType"]!="none":
            raise ValueError("No-animal clips require type none")
        if item["animalPresence"]=="present" and item["animalType"] in ("none","unknown"):
            # Owner may be sure a creature is visible but unsure of its type.
            if item["animalType"]=="none":
                raise ValueError("Visible animal cannot have type none")
        for key in RATINGS:
            rating=item[key]
            if rating is not None and (type(rating) is not int or not 1<=rating<=5):
                raise ValueError("Human ratings must be 1..5 or null")
        a,b=item["eventStartMs"],item["eventEndMs"]
        if (a is None)!=(b is None):
            raise ValueError("Animal event boundaries must be provided together")
        row=known[rid]
        if a is not None and (type(a) is not int or type(b) is not int
             or not row["sourceStartMs"]<=a<b<=row["sourceEndMs"]):
            raise ValueError("Animal encounter interval must lie inside verified proxy")
        result.append(item)
    return result


def human_quality_score(review:dict)->float | None:
    if any(review[key] is None for key in RATINGS):
        return None
    # Explicit owner Likert judgements, not model probabilities.
    return round(sum(WEIGHTS[k]*(review[k]-1)/4 for k in RATINGS),5)


def merge_owner(candidates:list[dict],m:dict,labels:dict)->tuple[list[dict],dict]:
    reviews=validate_review(m,labels)
    by_id={r["recordId"]:r for r in reviews}
    enriched=[]
    counts=Counter()
    for x in candidates:
        item=dict(x)
        r=by_id.get(item["id"])
        if r is not None:
            counts["reviewed"]+=1
            counts["decision_"+r["decision"]]+=1
            counts["presence_"+r["animalPresence"]]+=1
            if (item["activity"]!="snorkeling"
                    or item["sourceSha256"]!=r["sourceSha256"]):
                raise ValueError("Marine review refers to a non-marine candidate")
            if r["animalPresence"]=="absent" and "marine_ray" in item["categoryProposals"]:
                counts["reviewed_model_ray_no_visible_animal"]+=1
            if r["decision"]=="keep" and r["animalPresence"]=="present":
                counts["confirmed_visible_animal_keep"]+=1
            if r["decision"]=="keep":
                item.update(
                    ownerReviewed=True,humanKeepConfirmed=True,
                    candidateTitle="Human-kept underwater wildlife encounter"
                    if r["animalPresence"]=="present" else
                    "Human-kept underwater scene",
                )
            interpretation=extract_narrative(r)
            if interpretation is not None:
                counts["reviews_with_narrative_schema"]+=1
                if interpretation["sceneDescription"].strip():
                    counts["reviews_with_scene_description"]+=1
                if interpretation["whyKeep"].strip():
                    counts["reviews_with_personal_reason"]+=1
                if r["decision"]=="keep" and r["animalPresence"]!="present":
                    counts["kept_scenes_without_confirmed_animal"]+=1
            item["humanSceneInterpretation"]=interpretation
            item["humanMarineReview"]=r
            item["humanMarineQualityScore"]=human_quality_score(r)
            item["humanConfirmedVisibleAnimal"]=r["animalPresence"]=="present"
            item["humanAnimalTypeClass"]=r["animalType"]
            item["humanSpeciesVerified"]=False
            item["humanReviewDecision"]=r["decision"]
        enriched.append(item)
    counts["unreviewed"]=len(m["clips"])-len(reviews)
    return enriched,{
        "version":VERSION,"reviewedCount":len(reviews),
        "eligibleSampleCount":len(m["clips"]),
        "counts":dict(sorted(counts.items())),
        "qualityRatingsAreHumanOrdinalNotCalibratedModelConfidence":True,
        "animalClassIsNotSpeciesVerification":True,
        "freeTextNotAutomaticallyScoredByModel":True,
        "descriptionNotEquivalentToAnimalPresence":True,
        "supportsLegacyV1AndSceneFirstV2":True,
        "unreviewedNotNegative":True,
    }


def blind_review_html(m:dict)->str:
    """Client-side blind labeling; drafts stay in this browser, JSON export is the record."""
    payload=[{
        "id":x["recordId"],"source":x["sourceSha256"],
        "start":x["sourceStartMs"],"end":x["sourceEndMs"],
        "video":x["media"],
    } for x in m["clips"]]
    values=json.dumps(payload,separators=(",",":")).replace("<","\\u003c")
    ident=json.dumps(m["sampleId"])
    # No model-generated tag, prior rating, or species suggestion is exposed.
    return r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; media-src 'self' file:; connect-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'none'; object-src 'none'">
<title>Klip / Blind marine encounters</title><style>
:root{color-scheme:dark;background:#0d1d25;color:#eff8f8;font:14px/1.5 system-ui}
*{box-sizing:border-box}body{max-width:1160px;margin:auto;padding:24px}
h1{font-size:24px}h2{font-size:16px;margin-top:0}
p,.muted{color:#b8cdd1}.notice{padding:12px;background:#213d46;border-left:3px solid #91d5c8}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:15px}
.panel{padding:15px;border:1px solid #3e6874;background:#182f38;border-radius:12px;margin:12px 0}
.row{display:flex;gap:9px;align-items:center;flex-wrap:wrap;justify-content:space-between}
video{width:100%;background:#000;aspect-ratio:16/9;object-fit:contain;border-radius:9px}
button,select,input,textarea{background:#25454f;color:#eff8f8;border:1px solid #678893;padding:9px;border-radius:7px;font:inherit}
button{cursor:pointer}button:hover{border-color:#aae2d3}
select,input[type=number],textarea{width:100%}textarea{min-height:95px;resize:vertical}
label{display:block;color:#c5d8da;font-size:13px;margin:7px 0 4px}
.first{border-color:#76bbad;background:#19373c}
.error{color:#ffb09a}.hint{color:#a4cbc5}
@media(max-width:740px){.grid{grid-template-columns:1fr}body{padding:12px}}
</style></head><body>
<h1>Klip / Describe the scene first</h1>
<p class="notice">Your first task is to explain the scene and whether it matters.
A beautiful reef, moment of exploration, or personal memory may be worth
saving even when there is no animal. Wildlife is an optional separate
observation. Existing model guesses are hidden; this review doesn't retrain
anything automatically. Nothing uploads. Export your annotations to preserve them.</p>
<div class="panel"><div class="row"><h2 id="position">Clip</h2>
<div><button id="prev">Previous</button> <button id="next">Next</button></div></div>
<video id="player" controls preload="metadata" playsinline></video>
<p class="muted">Video is source-relative. The reviewed proxy might omit the approach
or the continuation. Notes should identify what you actually watched.</p></div>
<section class="panel first"><h2>1. Describe why this moment matters</h2>
<label for="sceneDescription">What is happening in the scene? (your own words)</label>
<textarea id="sceneDescription" maxlength="2400" placeholder="e.g. Swimming over a shallow reef; the camera approaches a colorful coral formation before turning away."></textarea>
<label for="whyKeep">Why is this worth saving, or why would you skip it?</label>
<textarea id="whyKeep" maxlength="1800" placeholder="e.g. A beautiful reveal with great visibility and a smooth approach; good establishing scene for the reef chapter."></textarea>
<div class="grid">
<div><label for="sceneType">Main scene interpretation</label>
<select id="sceneType"><option value="uncertain">Uncertain / unclassified</option>
<option value="wildlife_encounter">Wildlife encounter</option>
<option value="reef_exploration">Reef exploration</option>
<option value="scenic_underwater">Beautiful underwater scenery</option>
<option value="movement_action">Movement / action</option>
<option value="social_personal">People / personal</option>
<option value="discovery">Unexpected discovery</option>
<option value="other">Other scene</option></select></div>
<div><label for="storyRole">Role in the final story</label>
<select id="storyRole"><option value="unassigned">Not decided</option>
<option value="key_moment">Key highlight</option>
<option value="supporting_sequence">Supporting sequence</option>
<option value="establishing_scene">Establishing context</option>
<option value="transition">Transition</option>
<option value="atmosphere">Atmosphere / immersion</option>
<option value="personal_memory">Personal memory</option></select></div>
<div><label for="filmingIntent">Was this filming intentional?</label>
<select id="filmingIntent"><option value="unknown">Unclear</option>
<option value="intentional">Deliberately captured</option>
<option value="incidental">Incidental / accidental capture</option></select></div>
<div><label for="personalImportance">Personal significance</label>
<select id="personalImportance"><option value="unsure">Unsure</option>
<option value="high">High</option><option value="medium">Medium</option>
<option value="low">Low</option></select></div>
</div>
<div class="grid"><div><label for="decision">Keep, skip or investigate?</label>
<select id="decision"><option value="">Choose after watching</option>
<option value="keep">Keep, meaningful scene</option>
<option value="skip">Skip</option>
<option value="unsure">Unsure / needs more context</option></select></div>
<div><label for="sceneInterest">How engaging is the scene? (optional)</label>
<select id="sceneInterest"><option value="">Not rated</option>
<option value="1">1 · not engaging</option><option value="2">2</option>
<option value="3">3</option><option value="4">4</option>
<option value="5">5 · exceptional</option></select></div></div>
<p class="muted">A previous Keep judgment is valuable provenance; this page does
not automatically import older curation labels. Describe your reasoning so it
can be linked to those prior experiments later.</p></section>
<div class="grid"><div class="panel"><h2>2. Optional wildlife evidence</h2>
<p class="muted">Animal type is only one feature of the story. Leave presence as
unclear if the scene is interesting for another reason.</p>
<label for="presence">Is an animal clearly visible?</label>
<select id="presence"><option value="unclear">Not sure / not the focus</option><option value="present">Clearly present</option><option value="possible">Possible, not clear</option><option value="absent">No animal visible</option><option value="unclear">Cannot judge</option></select>
<label for="animal">Best observable animal class (not species)</label>
<select id="animal"><option value="unknown">Unknown</option><option value="ray">Ray</option>
<option value="fish">Fish</option><option value="turtle">Turtle</option><option value="shark">Shark</option>
<option value="octopus">Octopus</option><option value="other">Other animal</option><option value="none">None</option></select>
<label for="behavior">What is actually happening?</label>
<select id="behavior"><option value="unclear">Unclear</option><option value="swimming">Swimming</option>
<option value="passing">Passing / crossing</option><option value="schooling">Schooling</option>
<option value="feeding">Feeding</option><option value="interaction">Interesting interaction</option>
<option value="stationary">Stationary</option><option value="none">No behavior observable</option></select></div>
<div class="panel"><h2>3. Filming and story quality (1 poor → 5 outstanding)</h2>
<p class="muted">Choose values based on playback, not model labels.</p>
<div id="ratings"></div></div></div>
<div class="panel"><h2>4. Optional important moment boundaries</h2>
<div class="row"><button id="markIn">Start at playhead</button>
<button id="markOut">End at playhead</button>
<button id="clearMarks">Clear bounds</button></div>
<div class="grid"><div><label for="start">Source start (seconds)</label>
<input id="start" type="number" step="0.001" placeholder="Optional"></div>
<div><label for="end">Source end (seconds)</label>
<input id="end" type="number" step="0.001" placeholder="Optional"></div></div></div>
<div class="panel"><div class="row"><div>
<button id="save">Save this review in memory</button>
<button id="remove">Clear this review</button></div><div>
<button id="import">Import saved JSON</button>
<button id="export">Export reviewed labels</button>
<input id="file" type="file" accept=".json,application/json" hidden></div></div>
<p id="status" class="muted" role="status" aria-live="polite">Drafts are kept in this browser only. Export JSON to keep a durable record.</p>
<strong id="count"></strong></div>
<script>
'use strict';
const clips=__CLIPS__,sampleId=__SAMPLE__;
const ratings=['visibility','framing','technicalQuality','narrativeCompleteness','behaviorInterest'];
const narrativeKeys=['sceneDescription','whyKeep','sceneType','storyRole',
  'filmingIntent','personalImportance','sceneInterest'];
const emptyScene={
  sceneDescription:'',whyKeep:'',sceneType:'uncertain',
  storyRole:'unassigned',filmingIntent:'unknown',
  personalImportance:'unsure',sceneInterest:null
};
const map=new Map(),$=s=>document.getElementById(s);
let index=0,dirty=false,unexported=false;
// Draft safety net only: the exported JSON remains the durable owner record.
const draftKey='klip-marine-draft:'+sampleId;
function persist(){
  unexported=true;
  try{localStorage.setItem(draftKey,JSON.stringify([...map.values()]));}catch(e){}
}
function restore(){
  let rows;
  try{rows=JSON.parse(localStorage.getItem(draftKey)||'[]');}catch(e){return 0;}
  if(!Array.isArray(rows))return 0;
  const known=new Map(clips.map(x=>[x.id,x]));
  for(const r of rows){
    const clip=r&&typeof r==='object'?known.get(r.recordId):null;
    if(clip&&r.sourceSha256===clip.source&&
       ['keep','skip','unsure'].includes(r.decision))map.set(r.recordId,r);
  }
  return map.size;
}
window.addEventListener('beforeunload',e=>{
  if(dirty||unexported){e.preventDefault();e.returnValue='';}
});
for(const key of ratings){
  const line=document.createElement('div'),lab=document.createElement('label');
  lab.htmlFor=key;lab.textContent=key.replace(/([A-Z])/g,' $1');
  const sel=document.createElement('select');sel.id=key;
  for(let v=0;v<=5;v++){
    const opt=document.createElement('option');opt.value=v||'';
    opt.textContent=v?String(v):'Not yet rated';sel.appendChild(opt);
  }
  sel.onchange=()=>{dirty=true};line.append(lab,sel);$('ratings').appendChild(line);
}
const now=()=>clips[index].start+Math.round($('player').currentTime*1000);
function redraw(){
  const x=clips[index],r=map.get(x.id);
  $('position').textContent='Clip '+(index+1)+' of '+clips.length;
  $('count').textContent=map.size+' / '+clips.length+' reviewed';
  $('player').pause();$('player').src=x.video;$('player').load();
  $('decision').value=r?.decision||'';
  for(const key of narrativeKeys){
    const v=r?.[key]??emptyScene[key];
    $(key).value=v===null?'':v;
  }
  $('presence').value=r?.animalPresence||'unclear';
  $('animal').value=r?.animalType||'unknown';
  $('behavior').value=r?.behavior||'unclear';
  for(const k of ratings)$(k).value=r?.[k]??'';
  $('start').value=r?.eventStartMs==null?'':(r.eventStartMs/1000).toFixed(3);
  $('end').value=r?.eventEndMs==null?'':(r.eventEndMs/1000).toFixed(3);
  $('prev').disabled=index===0;$('next').disabled=index===clips.length-1;
  dirty=false;$('status').textContent='Watch fully, then choose labels.';
  $('status').className='muted';
}
for(const id of ['decision','presence','animal','behavior','start','end',...narrativeKeys]){
  $(id).onchange=()=>{dirty=true};
}
$('presence').addEventListener('change',()=>{
  if($('presence').value==='absent')$('animal').value='none';
  else if($('animal').value==='none')$('animal').value='unknown';
  dirty=true;
});
function bounds(id){
  const raw=$(id).value.trim();if(!raw)return null;
  const n=Number(raw);
  if(!Number.isFinite(n))throw Error('Invalid event timestamp');
  return Math.round(n*1000);
}
function review(){
  const x=clips[index],decision=$('decision').value,presence=$('presence').value;
  if(!decision)throw Error('Choose Keep, Skip or Unsure.');
  if(!presence)throw Error('Choose or leave animal presence as Unclear.');
  const animal=$('animal').value;
  if(presence==='absent'&&animal!=='none')
    throw Error('When no animal is visible, choose animal type None.');
  if(presence==='present'&&animal==='none')
    throw Error('Clearly visible animal cannot have type None.');
  const a=bounds('start'),b=bounds('end');
  if((a===null)!==(b===null)||a!==null&&!(x.start<=a&&a<b&&b<=x.end))
    throw Error('Event bounds must be paired and inside source clip.');
  const result={
    recordId:x.id,sourceSha256:x.source,decision,
    animalPresence:presence,animalType:animal,behavior:$('behavior').value,
    eventStartMs:a,eventEndMs:b
  };
  for(const k of ratings)result[k]=$(k).value===''?null:Number($(k).value);
  for(const key of narrativeKeys){
    result[key]=$(key).value;
  }
  result.sceneInterest=$('sceneInterest').value===''?null:Number($('sceneInterest').value);
  if(result.sceneDescription.length>2400||result.whyKeep.length>1800)
    throw Error('Scene notes exceed maximum length.');
  return result;
}
$('save').onclick=()=>{
  try{const r=review();map.set(r.recordId,r);dirty=false;persist();
      $('status').textContent='Draft saved in this browser. Export to keep a durable record.';
      $('count').textContent=map.size+' / '+clips.length+' reviewed';
      $('status').className='muted';}
  catch(e){$('status').className='error';$('status').textContent=e.message;}
};
$('remove').onclick=()=>{map.delete(clips[index].id);persist();redraw();};
function move(by){
  if(dirty){$('status').className='error';
    $('status').textContent='Save or clear your changes before navigating.';return;}
  index=Math.max(0,Math.min(clips.length-1,index+by));redraw();
}
$('prev').onclick=()=>move(-1);$('next').onclick=()=>move(1);
$('markIn').onclick=()=>{$('start').value=(now()/1000).toFixed(3);dirty=true;};
$('markOut').onclick=()=>{$('end').value=(now()/1000).toFixed(3);dirty=true;};
$('clearMarks').onclick=()=>{$('start').value='';$('end').value='';dirty=true;};
$('export').onclick=()=>{
  if(dirty){$('status').className='error';$('status').textContent='Save changes first.';return;}
  const data={version:'__VERSION__',sampleId,reviews:clips.filter(x=>map.has(x.id)).map(x=>map.get(x.id))};
  const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)+'\n'],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;
  a.download='klip-marine-reviewed-'+sampleId.slice(0,12)+'.json';
  document.body.append(a);a.click();a.remove();URL.revokeObjectURL(url);
  unexported=false;
  $('status').className='muted';$('status').textContent='Exported private owner review JSON.';
};
$('import').onclick=()=>$('file').click();
$('file').onchange=async()=>{
  const file=$('file').files?.[0];if(!file)return;
  try{
    if(file.size>2000000)throw Error('Import too large');
    const d=JSON.parse(await file.text());
    if(!['__VERSION__','__LEGACY__'].includes(d.version)||
       d.sampleId!==sampleId||!Array.isArray(d.reviews))
      throw Error('Import does not match this frozen review sample');
    const known=new Map(clips.map(x=>[x.id,x])),next=new Map();
    for(const x of d.reviews){
      const clip=known.get(x.recordId);
      if(!clip||x.sourceSha256!==clip.source||next.has(x.recordId))
        throw Error('Unknown or mismatched source in imported review');
      if(!['keep','skip','unsure'].includes(x.decision)||
         !['present','possible','absent','unclear'].includes(x.animalPresence)||
         !['ray','fish','turtle','shark','octopus','other','unknown','none'].includes(x.animalType)||
         !['swimming','passing','schooling','feeding','interaction','stationary','none','unclear'].includes(x.behavior)||
         ratings.some(k=>x[k]!==null&&!(Number.isInteger(x[k])&&x[k]>=1&&x[k]<=5)))
        throw Error('Invalid label data in import');
      const restored={...emptyScene,...x};
      for(const name of ['sceneDescription','whyKeep']){
        if(typeof restored[name]!=='string'||
           restored[name].length>(name==='sceneDescription'?2400:1800))
           throw Error('Invalid owner description length');
      }
      const domains={
        sceneType:['wildlife_encounter','reef_exploration','scenic_underwater','movement_action','social_personal','discovery','other','uncertain'],
        storyRole:['key_moment','supporting_sequence','establishing_scene','transition','atmosphere','personal_memory','unassigned'],
        filmingIntent:['intentional','incidental','unknown'],
        personalImportance:['high','medium','low','unsure']
      };
      for(const [key,allowed] of Object.entries(domains)){
        if(!allowed.includes(restored[key]))throw Error('Invalid '+key);
      }
      if(restored.sceneInterest!==null&&!(
        Number.isInteger(restored.sceneInterest)&&
        restored.sceneInterest>=1&&restored.sceneInterest<=5))
        throw Error('Invalid personal interest rating');
      next.set(x.recordId,restored);
    }
    map.clear();for(const [k,v] of next)map.set(k,v);
    dirty=false;persist();unexported=false;redraw();
    $('status').textContent='Imported '+map.size+' reviews.';
  }catch(e){$('status').className='error';$('status').textContent=e.message;}
};
const restored=restore();redraw();
if(restored){unexported=true;
  $('status').textContent='Restored '+restored+' draft reviews from this browser. Export to keep them.';}
</script></body></html>""".replace("__CLIPS__",values).replace("__SAMPLE__",ident).replace(
        "__VERSION__",LABEL_VERSION).replace("__LEGACY__",LEGACY_LABEL_VERSION)

"""Private, blind, offline action annotation UI and strict human review importer.

The HTML never loads external scripts, sends network requests or exposes Klip
scores. Only receipt-verified local H.264 derivatives can be played. The
source media and existing frozen manifest are never rewritten.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
import copy
import json
import re

from .highlight_curation_pilot import VERSION, _safe_dest
from .highlight_curation_validate import audit_highlight_curation_manifest

REVIEW_VERSION="klip-highlight-curation-review-export-v1"
_ROLES={"athlete","expert_coach","casual_spectator","filmmaker","editor",
        "nature_specialist","safety_reviewer","participant","other"}
_GOALS={"athletic_skill","story_payoff","visual_quality","humor",
        "personal_memory","nature_encounter","technical_documentation","other"}
_CHOICES={"keep","skip","neither","cannot_tell"}
_OUTCOMES={"completed","failed","partial","not_applicable","unknown"}
_VISIBILITY={"yes","no","ambiguous"}
_IDENT=re.compile(r"^[a-zA-Z0-9_.:-]{4,100}$")


def render_blind_review(manifest:dict,presentation:dict,digest:str)->str:
    """Compact single-clip review, with unassessed-but-missing-proxy state."""
    items=[]
    for record in manifest["records"]:
        rid=record["recordId"]
        visual=presentation[rid]
        url=visual["proxyUrl"]
        # recordId is generated from a digest, url is percent-quoted locally.
        if not re.fullmatch(r"[0-9a-f]{24}",rid):
            raise ValueError("Unsafe review identifier")
        if url is not None and not re.fullmatch(r"media/[a-f0-9]{24}\.mp4",url):
            raise ValueError("Unsafe local review preview URL")
        items.append({"id":rid,"ready":visual["verifiedProxyReady"],
                      "url":url,"sport":record["sportContext"]["sport"],
                      "start":record["event"]["startMs"],"end":record["event"]["endMs"]})
    encoded=json.dumps(items,ensure_ascii=True,separators=(",",":"))
    html=r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; media-src 'self' file:; img-src 'self' data:; connect-src 'none'; object-src 'none'; base-uri 'none'">
<title>Klip | Blind Action Review</title>
<style>
:root{color-scheme:dark;--navy:#101927;--panel:#182536;--line:#2d4056;--ink:#f1f4f8;--muted:#9badbf;--salmon:#eea58e}
*{box-sizing:border-box}body{margin:0;background:var(--navy);font:15px/1.5 system-ui,sans-serif;color:var(--ink)}
header{padding:17px 24px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between}
h1{font-size:16px;margin:0;letter-spacing:.03em}h2{font-size:16px;margin:0 0 12px}
main{width:min(100%,1030px);margin:auto;padding:24px;display:grid;gap:18px}
.eyebrow{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.09em}
.card{border:1px solid var(--line);background:var(--panel);border-radius:14px;padding:18px}
.video-shell{aspect-ratio:16/9;background:#050a10;border-radius:10px;display:grid;place-items:center;overflow:hidden}
video{width:100%;height:100%;object-fit:contain}
button,select,input,textarea{font:inherit}button{border:1px solid var(--line);border-radius:10px;color:var(--ink);background:#203146;padding:9px 14px;cursor:pointer}
button:hover{border-color:var(--salmon)}button:focus-visible,select:focus-visible,textarea:focus-visible,input:focus-visible{outline:2px solid var(--salmon);outline-offset:2px}
button:disabled{opacity:.4;cursor:not-allowed}.primary{background:#b46f61;color:white;border-color:#d48a7b}.selected{border-color:var(--salmon);background:#503c40}
.small{font-size:12px;color:var(--muted)}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.controls{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
label{display:block;color:var(--muted);font-size:13px;margin-bottom:5px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
select,input,textarea{width:100%;border:1px solid var(--line);background:var(--navy);color:var(--ink);border-radius:8px;padding:9px}
textarea{min-height:82px;resize:vertical}
.message{border-left:3px solid var(--salmon);padding-left:11px;color:var(--muted)}
nav{display:flex;gap:10px;justify-content:space-between;align-items:center}
progress{width:140px;accent-color:var(--salmon)}
@media(max-width:650px){main{padding:12px}.grid{grid-template-columns:1fr}header{padding:12px}}
</style></head><body>
<header><h1>Klip <span style="color:var(--salmon)">/ Blind Action Review</span></h1><span class="small">Private, offline, action only</span></header>
<main>
<div class="controls"><strong id="counter"></strong><progress id="progress" value="0" max="1"></progress><span id="review-count" class="small"></span></div>
<section class="card">
<div class="row" style="justify-content:space-between;margin-bottom:10px"><span class="eyebrow">Source-backed candidate / no AI score</span><span id="activity" class="small"></span></div>
<div class="video-shell"><video id="video" controls preload="metadata" playsinline></video><p id="no-video" class="message" hidden>No checksum-verified local preview. This interval remains UNKNOWN. Do not rate without seeing the source.</p></div>
<p class="small">Observe only what is visible. A hidden landing is not a confirmed landing. No model story or reaction commentary is shown.</p>
<div class="row" style="margin-top:8px">
<button type="button" id="mark-peak">Mark action peak at playhead</button>
<button type="button" id="mark-resolution">Mark observed resolution</button>
<button type="button" id="clear-timing">Clear markers</button>
</div><p class="small" id="timing">No action timestamps marked.</p>
</section>
<section class="card" id="rating">
<h2>1 · What would you keep?</h2>
<div class="row" id="choices">
<button type="button" data-choice="keep">Keep</button>
<button type="button" data-choice="skip">Skip</button>
<button type="button" data-choice="neither">Neither</button>
<button type="button" data-choice="cannot_tell">Cannot tell</button>
</div>
<div class="grid" style="margin-top:15px">
<div><label for="outcome">Observed action outcome</label><select id="outcome"><option value="unknown">Unknown</option><option value="completed">Completed</option><option value="failed">Failed</option><option value="partial">Partial</option><option value="not_applicable">No action observed</option></select></div>
<div><label for="outcome-visible">Outcome visible?</label><select id="outcome-visible"><option value="ambiguous">Ambiguous / unknown</option><option value="yes">Yes</option><option value="no">No</option></select></div>
<div><label for="goal">Reason for this highlight</label><select id="goal"><option value="athletic_skill">Athletic achievement</option><option value="story_payoff">Story or payoff</option><option value="visual_quality">Visual quality</option><option value="humor">Humor</option><option value="personal_memory">Personal significance</option><option value="nature_encounter">Nature encounter</option><option value="technical_documentation">Technical documentation</option><option value="other">Other</option></select></div>
</div>
</section>
<section class="card">
<h2>2 · How was it filmed?</h2><p class="small">Evaluate the camera separately from the athlete. Leave any unsupported dimension unassessed.</p>
<div class="grid">
<div><label for="tech">Technical image quality</label><select id="tech"></select></div>
<div><label for="aesth">Cinematography / aesthetics</label><select id="aesth"></select></div>
<div><label for="readability">Action readability</label><select id="readability"></select></div>
</div>
</section>
<section class="card">
<h2>3 · Your perspective</h2>
<div class="grid" style="grid-template-columns:repeat(2,minmax(0,1fr))">
<div><label for="annotator">Reviewer ID (pseudonym, 4+ characters)</label><input id="annotator" value="reviewer-001" maxlength="100" autocomplete="off"></div>
<div><label for="role">Perspective</label><select id="role"><option value="casual_spectator">Casual spectator</option><option value="athlete">Athlete</option><option value="expert_coach">Coach / expert</option><option value="filmmaker">Filmmaker</option><option value="editor">Editor</option><option value="nature_specialist">Nature specialist</option><option value="safety_reviewer">Safety reviewer</option><option value="participant">Participant</option><option value="other">Other</option></select></div>
</div>
<label for="rationale" style="margin-top:14px">What did you actually see, and why does it matter?</label>
<textarea id="rationale" maxlength="1000" placeholder="Timestamp or moment, visible details, uncertainty, and why you would or would not keep it"></textarea>
<p class="small" id="error" role="alert" aria-live="polite"></p>
<div class="row"><button class="primary" id="save" type="button">Save review &amp; next</button><button id="export" type="button">Export JSON reviews</button><button id="import" type="button">Import previously exported JSON</button><input id="file" type="file" accept=".json,application/json" hidden></div>
</section>
<nav><button id="prev" type="button">Previous</button><span class="small">Scores and sampling roles stay hidden until after review</span><button id="next" type="button">Next</button></nav>
</main>
<script>
'use strict';
const allItems=__ITEMS__;
const items=allItems.filter(item=>item.ready);
const missingCount=allItems.length-items.length;
const manifestSha='__MANIFEST_SHA__';
const version='klip-highlight-curation-review-export-v1';
const decisions=new Map();
const $=id=>document.getElementById(id);
let index=0,choice=null,peakMs=null,resolutionMs=null;
const value=(id)=>$(id).value;
const rating=(id)=>value(id)===''?null:Number(value(id));
for(const id of ['tech','aesth','readability']){
  const s=$(id);s.add(new Option('Unassessed',''));
  for(let i=1;i<=5;i++)s.add(new Option(String(i),String(i)));
}
function highlightChoice(){
  for(const b of document.querySelectorAll('[data-choice]'))
    b.classList.toggle('selected',b.dataset.choice===choice);
}
function show(){
  if(!items.length){
    for(const x of ['mark-peak','mark-resolution','clear-timing'])$(x).disabled=true;
    $('counter').textContent='0 / 0 ready';
    $('review-count').textContent=missingCount+' candidates lack verified previews';
    $('video').hidden=true;
    $('no-video').hidden=false;
    $('error').textContent='No verified previews in this pilot. The source footage must remain UNKNOWN until evidence is available.';
    $('prev').disabled=true;$('next').disabled=true;$('save').disabled=true;
    return;
  }
  const item=items[index];
  $('video').pause();$('video').removeAttribute('src');$('video').load();
  $('counter').textContent=(index+1)+' / '+items.length;
  $('activity').textContent=item.sport==='unknown'?'Unclassified activity':item.sport;
  $('progress').max=items.length;$('progress').value=decisions.size;
  $('review-count').textContent=decisions.size+' saved · '+missingCount+' missing verified previews';
  $('video').hidden=!item.ready;$('no-video').hidden=item.ready;
  if(item.ready){$('video').src=item.url;$('video').load();}
  $('save').disabled=!item.ready;
  for(const x of ['mark-peak','mark-resolution','clear-timing'])$(x).disabled=!item.ready;
  const d=decisions.get(item.id)||{};
  peakMs=d.eventPeakMs??null;resolutionMs=d.resolutionMs??null;
  showTiming();
  choice=d.choice||null;highlightChoice();
  $('outcome').value=d.outcome||'unknown';
  $('outcome-visible').value=d.outcomeVisible||'ambiguous';
  $('goal').value=d.goal||'athletic_skill';
  $('tech').value=d.technicalQuality==null?'':String(d.technicalQuality);
  $('aesth').value=d.aestheticQuality==null?'':String(d.aestheticQuality);
  $('readability').value=d.eventReadability==null?'':String(d.eventReadability);
  $('role').value=d.role||'casual_spectator';
  $('annotator').value=d.annotatorId||$('annotator').value||'reviewer-001';
  $('rationale').value=d.rationale||'';
  $('prev').disabled=index===0;$('next').disabled=index===items.length-1;
  $('error').textContent=item.ready?'':'This clip cannot be assessed until a verified preview exists.';
}
for(const b of document.querySelectorAll('[data-choice]'))
  b.addEventListener('click',()=>{choice=b.dataset.choice;highlightChoice();});
function showTiming(){
  if(!items.length)return;
  const sourceStart=items[index].start;
  const fmt=v=>v===null?'unmarked':((v-sourceStart)/1000).toFixed(2)+'s into clip';
  $('timing').textContent='Peak: '+fmt(peakMs)+' / Resolution: '+fmt(resolutionMs);
}
function mark(kind){
  const item=items[index];
  if(!item||!item.ready||!Number.isFinite($('video').currentTime))return;
  const stamp=Math.min(item.end-1,Math.max(item.start,
      item.start+Math.round($('video').currentTime*1000)));
  if(kind==='peak')peakMs=stamp;else resolutionMs=stamp;
  showTiming();
}
$('mark-peak').addEventListener('click',()=>mark('peak'));
$('mark-resolution').addEventListener('click',()=>mark('resolution'));
$('clear-timing').addEventListener('click',()=>{
  peakMs=null;resolutionMs=null;showTiming();
});
function save(){
  const item=items[index];if(!item.ready)return;
  const id=value('annotator').trim();
  if(!/^[a-zA-Z0-9_.:-]{4,100}$/.test(id)){
    $('error').textContent='Use a simple pseudonymous reviewer ID, 4 to 100 characters.';return;
  }
  if(!choice){$('error').textContent='Choose Keep, Skip, Neither, or Cannot tell.';return;}
  const outcome=value('outcome'),visible=value('outcome-visible');
  if((outcome==='completed'||outcome==='failed')&&visible!=='yes'){
    $('error').textContent='A completed or failed outcome must be visible; choose Unknown otherwise.';return;
  }
  if(peakMs!==null&&resolutionMs!==null&&resolutionMs<peakMs){
    $('error').textContent='Resolution cannot precede the marked peak.';return;
  }
  const rationale=value('rationale').trim();
  if((choice==='keep'||choice==='skip')&&rationale.length<10){
    $('error').textContent='Describe at least ten characters of visual evidence for a Keep or Skip judgment.';return;
  }
  decisions.set(item.id,{
    recordId:item.id,annotatorId:id,role:value('role'),goal:value('goal'),
    choice,outcome,outcomeVisible:visible,
    technicalQuality:rating('tech'),aestheticQuality:rating('aesth'),
    eventReadability:rating('readability'),
    eventPeakMs:peakMs,resolutionMs:resolutionMs,rationale
  });
  if(index<items.length-1)index++;
  show();
}
$('save').addEventListener('click',save);
$('prev').addEventListener('click',()=>{if(index>0){index--;show();}});
$('next').addEventListener('click',()=>{if(index<items.length-1){index++;show();}});
function exportReviews(){
 const output={version,manifestSha256:manifestSha,reviews:[...decisions.values()]};
 const blob=new Blob([JSON.stringify(output,null,2)],{type:'application/json'});
 const url=URL.createObjectURL(blob),a=document.createElement('a');
 a.href=url;a.download='klip-curation-reviews-'+manifestSha.slice(0,12)+'.json';
 document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);
}
$('export').addEventListener('click',exportReviews);
$('import').addEventListener('click',()=>$('file').click());
$('file').addEventListener('change',async()=>{
 const file=$('file').files[0];if(!file)return;
 if(file.size>2*1024*1024){$('error').textContent='Review file exceeds 2 MiB';return;}
 try{
  const content=JSON.parse(await file.text());
  if(content.version!==version||content.manifestSha256!==manifestSha||!Array.isArray(content.reviews))
    throw new Error('Review file does not match this frozen pilot');
  const allowed=new Set(items.filter(i=>i.ready).map(i=>i.id));
  for(const row of content.reviews){
    if(!allowed.has(row.recordId)||!['keep','skip','neither','cannot_tell'].includes(row.choice))
      throw new Error('Review refers to missing media or an unsupported choice');
  }
  for(const row of content.reviews)decisions.set(row.recordId,row);
  show();$('error').textContent='Imported draft for this pilot. Export after edits.';
 }catch(e){$('error').textContent='Import failed: '+String(e.message||e);}
 $('file').value='';
});
document.addEventListener('keydown',e=>{
 if(['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName))return;
 if(e.key==='ArrowLeft'&&index>0){index--;show();}
 else if(e.key==='ArrowRight'&&index<items.length-1){index++;show();}
});
show();
</script></body></html>"""
    return html.replace("__ITEMS__",encoded).replace("__MANIFEST_SHA__",digest)


def _read_json(path:Path,limit:int)->dict:
    p=Path(path).expanduser().resolve()
    if not p.is_file() or p.stat().st_size>limit:
        raise ValueError("Missing or oversized local review JSON")
    try:
        value=json.loads(p.read_text(encoding="utf-8"))
    except (OSError,UnicodeError,ValueError) as exc:
        raise ValueError("Invalid local review JSON") from exc
    if not isinstance(value,dict):
        raise ValueError("Review must be a JSON object")
    return value


def _rating(value,name:str):
    if value is None:
        return None
    if type(value) is not int or not 1<=value<=5:
        raise ValueError(f"Invalid {name} rating")
    return value


def _review(row:dict,record:dict,available:bool)->dict:
    if not isinstance(row,dict):
        raise ValueError("Malformed human review")
    required={"recordId","annotatorId","role","goal","choice","outcome",
              "outcomeVisible","technicalQuality","aestheticQuality",
              "eventReadability","rationale"}
    allowed=required|{"eventPeakMs","resolutionMs"}
    if not required.issubset(row) or set(row)-allowed:
        raise ValueError("Human review fields differ from frozen export schema")
    if row["recordId"]!=record["recordId"] or not available:
        raise ValueError("Human review refers to an unsupported or missing verified preview")
    annotator=row["annotatorId"]
    if type(annotator) is not str or not _IDENT.fullmatch(annotator):
        raise ValueError("Invalid reviewer pseudonym")
    if row["role"] not in _ROLES or row["goal"] not in _GOALS:
        raise ValueError("Invalid observer role or goal")
    if row["choice"] not in _CHOICES:
        raise ValueError("Invalid single-clip preference")
    if row["outcome"] not in _OUTCOMES or row["outcomeVisible"] not in _VISIBILITY:
        raise ValueError("Invalid observation or visibility")
    if (row["outcome"] in ("completed","failed")
            and row["outcomeVisible"]!="yes"):
        raise ValueError("Cannot confirm a completed/failed action without a visible outcome")
    start,end=record["event"]["startMs"],record["event"]["endMs"]
    peak,resolution=row.get("eventPeakMs"),row.get("resolutionMs")
    for stamp,label in ((peak,"peak"),(resolution,"resolution")):
        if stamp is not None and (type(stamp) is not int
                                  or not start<=stamp<end):
            raise ValueError(f"Reviewer {label} timestamp outside source interval")
    if peak is not None and resolution is not None and resolution<peak:
        raise ValueError("Reviewer resolution timestamp precedes action peak")
    rationale=row["rationale"]
    if type(rationale) is not str or len(rationale)>1000:
        raise ValueError("Invalid human rationale length")
    if row["choice"] in ("keep","skip") and len(rationale.strip())<10:
        raise ValueError("Keep/Skip judgments require an explanatory visual rationale")
    return {
        "annotatorId":annotator,"role":row["role"],
        "highlightGoal":row["goal"],"blindActionOnly":True,
        "preference":row["choice"],"rationale":rationale,
        # Human-submitted, not expert-adjudicated or externally verified.
        "reviewStatus":"human_verified","reviewedAt":None,
        "observedOutcome":row["outcome"],
        "outcomeVisible":row["outcomeVisible"],
        "technicalQuality":_rating(row["technicalQuality"],"technical"),
        "aestheticQuality":_rating(row["aestheticQuality"],"aesthetic"),
        "eventReadability":_rating(row["eventReadability"],"readability"),
        "eventPeakMs":peak,"resolutionMs":resolution,
    }


def import_curation_reviews(workspace:Path,pilot_dir:Path,reviews_files:list[Path],
                            output_dir:Path)->dict:
    """Freeze independent human observations; no forced consensus or retraining."""
    root=Path(workspace).expanduser().resolve()
    pilot=Path(pilot_dir).expanduser().resolve()
    if (pilot==root or not pilot.is_relative_to(root)
            or pilot.relative_to(root).parts[0] not in ("reports","exports")):
        raise ValueError("Pilot directory must be workspace-local")
    if not isinstance(reviews_files,list) or not 1<=len(reviews_files)<=25:
        raise ValueError("Require 1..25 independently exported review JSON files")
    from .highlight_curation_pilot import MANIFEST_VERSION
    from .highlight_curation_validate import VERSION as RECORD_VERSION
    source_path=pilot/"curation-manifest.json"
    frozen_bytes=source_path.read_bytes()
    frozen_sha=sha256(frozen_bytes).hexdigest()
    frozen=json.loads(frozen_bytes)
    if (frozen.get("version")!=MANIFEST_VERSION
            or not isinstance(frozen.get("records"),list)):
        raise ValueError("Invalid pilot manifest")
    presentation=_read_json(pilot/"presentation.json",4_000_000)
    if (presentation.get("version")!=VERSION
            or presentation.get("manifestSha256")!=frozen_sha
            or not isinstance(presentation.get("records"),dict)):
        raise ValueError("Pilot presentation and frozen manifest fingerprints differ")
    by_id={row["recordId"]:row for row in frozen["records"]}
    if len(by_id)!=len(frozen["records"]) or set(by_id)!=set(presentation["records"]):
        raise ValueError("Pilot record identities differ")
    # Check before opening any supplied review file; no original source media.
    media_ok={}
    for rid,entry in presentation["records"].items():
        if entry.get("verifiedProxyReady"):
            expected=entry.get("proxySha256")
            path=pilot/"media"/(rid+".mp4")
            if (not re.fullmatch(r"[0-9a-f]{64}",str(expected))
                    or not path.is_file() or path.stat().st_size>45*1024*1024
                    or sha256(path.read_bytes()).hexdigest()!=expected):
                raise ValueError(f"Verified review proxy changed for record {rid}")
            media_ok[rid]=True
        else:
            media_ok[rid]=False
    annotations=defaultdict(list)
    seen=set()
    source_hashes=[]
    for reviews_file in reviews_files:
        p=Path(reviews_file).expanduser().resolve()
        document=_read_json(p,2_000_000)
        if (document.get("version")!=REVIEW_VERSION
                or document.get("manifestSha256")!=frozen_sha
                or not isinstance(document.get("reviews"),list)
                or len(document["reviews"])>len(by_id)):
            raise ValueError("Review export does not match the immutable pilot")
        source_hashes.append(sha256(p.read_bytes()).hexdigest())
        for entry in document["reviews"]:
            rid=entry.get("recordId") if isinstance(entry,dict) else None
            if rid not in by_id:
                raise ValueError("Unknown record in review export")
            converted=_review(entry,by_id[rid],media_ok[rid])
            key=(rid,converted["annotatorId"])
            if key in seen:
                raise ValueError("Duplicate reviewer/clip across exports; edit a single export")
            seen.add(key)
            annotations[rid].append(converted)
    if not seen:
        raise ValueError("No human annotations to import")
    dest=_safe_dest(root,output_dir)
    merged=copy.deepcopy(frozen)
    for row in merged["records"]:
        row["observerJudgments"]=annotations[row["recordId"]]
    # Run the structural guard before publishing an immutable second corpus.
    # It validates the same unassigned splits and pending rights.
    dest.mkdir(parents=True,mode=0o700,exist_ok=False)
    curated=dest/"curated-manifest.json"
    with curated.open("x",encoding="utf-8") as handle:
        json.dump(merged,handle,indent=2,sort_keys=True,allow_nan=False)
        handle.write("\n")
    validity=audit_highlight_curation_manifest(curated)
    choices=Counter(r["preference"] for rows in annotations.values() for r in rows)
    roles=Counter(r["role"] for rows in annotations.values() for r in rows)
    disputed=sum(len({r["preference"] for r in rows})>1 or
                 len({r["observedOutcome"] for r in rows})>1
                 for rows in annotations.values())
    report={
        "version":"klip-highlight-curation-human-review-qualification-v1",
        "frozenManifestSha256":frozen_sha,
        "reviewExportSha256":source_hashes,
        "reviewedCandidateWindows":len(annotations),
        "humanSubmittedJudgments":len(seen),
        "distinctReviewers":len({aid for _,aid in seen}),
        "observerRoles":dict(sorted(roles.items())),
        "choices":dict(sorted(choices.items())),
        "multiRaterDisagreements":disputed,
        "humanLocalizedPeaks":sum(r["eventPeakMs"] is not None
                                   for rows in annotations.values() for r in rows),
        "humanLocalizedResolutions":sum(r["resolutionMs"] is not None
                                         for rows in annotations.values() for r in rows),
        "selectionGroundTruthNotImplied":True,
        "sportAndFilmingConsensusNotAutoInferred":True,
        "eventOutcomeAdjudicationRequired":True,
        "legalPermissionIndependentlyVerified":False,
        "trainingEligibleRecords":0,
        "sourceOriginalReads":0,"modelsTrained":0,
        "preflight":validity,
    }
    with (dest/"review-qualification.json").open("x",encoding="utf-8") as handle:
        json.dump(report,handle,indent=2,sort_keys=True,allow_nan=False)
        handle.write("\n")
    return {"status":"curation_human_reviews_imported",
            "curatedManifest":str(curated),
            "qualification":str(dest/"review-qualification.json"),
            "reviewedWindows":len(annotations),
            "humanSubmittedJudgments":len(seen),
            "multiRaterDisagreements":disputed,
            "trainingEligibleRecords":0,
            "originalSourceReads":0,"modelsTrained":0}

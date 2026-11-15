"""Minimal offline, deliberately prediction-blind review interface."""
from __future__ import annotations

import json

from .calibration_v2 import REVIEW_VERSION
from .moment_edit_v2 import TAGS, DEFECTS


def page(manifest: dict) -> str:
    # Only source identity and time are sent to the reviewer. Keep model
    # predictions solely in the sealed manifest for post-review evaluation.
    items = [{
        "id": r["recordId"], "source": r["sourceSha256"],
        "start": r["sourceStartMs"], "end": r["sourceEndMs"],
    } for r in manifest["clips"]]
    payload = json.dumps(items, separators=(",", ":")).replace("<", "\\u003c")
    tags = json.dumps({k: sorted(v) for k,v in TAGS.items()})
    quality = json.dumps(sorted(DEFECTS))
    return r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; media-src 'self' file:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; object-src 'none'; base-uri 'none'">
<title>Klip V2 | Blind Highlight Review</title>
<style>
:root{color-scheme:dark;background:#111920;color:#edf3f5;font:14px/1.5 system-ui}
*{box-sizing:border-box}body{max-width:1040px;margin:0 auto;padding:24px}
header{display:flex;justify-content:space-between;align-items:center;gap:15px}
h1{font-size:23px;margin:0}h2{font-size:16px}.hint{color:#a0b5bd}
.note{background:#25343b;border-left:3px solid #91c1b6;padding:13px}
.box{background:#1a2730;border:1px solid #3a505d;border-radius:10px;padding:16px;margin:14px 0}
video{width:100%;max-height:480px;aspect-ratio:16/9;background:black}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
input,select,button{font:inherit;color:#edf3f5;background:#263e4a;border:1px solid #66818c;border-radius:7px;padding:7px}
button{cursor:pointer}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid #91c1b6}
select{width:100%}.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:8px 0}
.labels{display:flex;gap:7px;flex-wrap:wrap}
.labels label{border:1px solid #3e5663;border-radius:18px;padding:5px 8px}
input[type=checkbox]{margin-right:5px}
input[type=number]{width:155px}label{cursor:pointer}#status{font-size:13px;min-height:24px}
@media(max-width:700px){.grid{grid-template-columns:1fr}body{padding:12px}}
</style></head><body>
<header><h1>Klip / Blind Clip Calibration</h1><span id="progress" class="hint"></span></header>
<p class="note">Watch each source-verified preview and make your own judgment.
Model suggestions, ranking scores and predicted quality flags are intentionally
hidden. Nothing uploads. Explicitly save each clip, then export your reviews
before closing the page. Only reviewed clips count in evaluation.</p>
<section class="box">
<div class="controls">
<button id="prev">Previous</button><button id="next">Next</button>
<span id="info" class="hint"></span></div>
<video id="video" controls playsinline preload="metadata"></video>
</section>
<div class="grid">
<section class="box"><h2>What did you actually see?</h2>
<label>Activity <select id="activity"><option value="">Select after watching</option>
<option value="ski">Skiing</option><option value="snorkeling">Snorkeling</option>
<option value="bike">Bike / riding</option><option value="other">Other</option>
<option value="unknown">Can't tell</option></select></label>
<h2>Confirmed categories (optional)</h2><div id="tags" class="labels"></div>
</section>
<section class="box"><h2>Would you keep this for a highlight?</h2>
<label>Viewing preference <select id="decision">
<option value="">Choose a decision</option>
<option value="keep">Keep / potentially a good highlight</option>
<option value="skip">Skip / not highlight-worthy</option>
<option value="unsure">Unsure / needs more footage</option>
</select></label>
<h2>Actually visible defects (optional)</h2>
<div id="quality" class="labels"></div>
</section></div>
<section class="box"><h2>Event core (optional, source-relative seconds)</h2>
<div class="controls">
<button id="markIn">Mark event start at playhead</button>
<button id="markOut">Mark event end at playhead</button>
<button id="clearTimes">Clear</button></div>
<div class="controls">
<label>Start <input type="number" id="eventStart" step=".001" min="0"></label>
<label>End <input type="number" id="eventEnd" step=".001" min="0"></label>
</div></section>
<section class="box">
<div class="controls">
<button id="save">Save reviewed clip</button><button id="export">Export review JSON</button>
<button id="import">Import prior JSON</button><input type="file" id="file" accept=".json,application/json" hidden>
</div><p id="status" role="status" class="hint">Unsaved reviews exist only in this browser tab.</p>
</section>
<script>
'use strict';
const clips=__ITEMS__, tagsets=__TAGS__, qlist=__QUALITY__;
const sampleId='__SAMPLE_ID__', version='__REVIEW_VERSION__';
const el=x=>document.getElementById(x), saved=new Map();
let index=0,dirty=false;
function ms(x){return x===null?'':(x/1000).toFixed(3);}
function checkLabels(node,names,selected){
  node.replaceChildren();
  for(const name of names){
    const label=document.createElement('label');
    const cb=document.createElement('input');cb.type='checkbox';
    cb.value=name;cb.checked=selected.includes(name);
    label.append(cb,document.createTextNode(name.replaceAll('_',' ')));
    node.appendChild(label);
  }
}
function checked(id){return Array.from(el(id).querySelectorAll('input:checked')).map(x=>x.value).sort();}
function progress(){el('progress').textContent=saved.size+' / '+clips.length+' explicitly reviewed';}
function render(){
  const c=clips[index],r=saved.get(c.id);
  el('video').pause();el('video').src='media/'+c.id+'.mp4';el('video').load();
  el('info').textContent=(index+1)+' / '+clips.length+' · source '+c.source.slice(0,12)+
    ' · '+ms(c.start)+'–'+ms(c.end)+'s';
  el('activity').value=r?r.activity:'';
  el('decision').value=r?r.decision:'';
  checkLabels(el('tags'),tagsets[el('activity').value]||[],r?r.tags:[]);
  checkLabels(el('quality'),qlist,r?r.quality:[]);
  el('eventStart').value=r?ms(r.eventStartMs):'';
  el('eventEnd').value=r?ms(r.eventEndMs):'';
  el('prev').disabled=index===0;el('next').disabled=index===clips.length-1;
  dirty=false;progress();el('status').textContent='Review and save this clip.';
}
function extract(){
  const c=clips[index],a=el('activity').value,d=el('decision').value;
  if(!a||!d)throw Error('Choose both an activity and Keep / Skip / Unsure.');
  const from=el('eventStart').value.trim(),to=el('eventEnd').value.trim();
  const t=x=>x===''?null:Math.round(Number(x)*1000);
  const start=t(from),end=t(to);
  if((start===null)!==(end===null))throw Error('Mark both event boundaries or neither.');
  if(start!==null&&!(c.start<=start&&start<end&&end<=c.end))
    throw Error('Event time must lie within original source window.');
  return {recordId:c.id,sourceSha256:c.source,activity:a,decision:d,
          tags:checked('tags'),quality:checked('quality'),
          eventStartMs:start,eventEndMs:end};
}
function move(n){
  if(dirty){el('status').textContent='Save this clip before navigating.';return;}
  index=Math.max(0,Math.min(clips.length-1,index+n));render();
}
el('prev').onclick=()=>move(-1);el('next').onclick=()=>move(1);
el('activity').onchange=()=>{checkLabels(el('tags'),tagsets[el('activity').value]||[],[]);dirty=true;};
for(const id of ['decision','eventStart','eventEnd'])el(id).onchange=()=>{dirty=true;};
el('tags').onchange=()=>{dirty=true;};el('quality').onchange=()=>{dirty=true;};
for(const [id,field] of [['markIn','eventStart'],['markOut','eventEnd']]){
  el(id).onclick=()=>{
    const c=clips[index];el(field).value=ms(c.start+Math.round(el('video').currentTime*1000));dirty=true;
  };
}
el('clearTimes').onclick=()=>{el('eventStart').value='';el('eventEnd').value='';dirty=true;};
el('save').onclick=()=>{
  try{const r=extract();saved.set(r.recordId,r);dirty=false;
    progress();el('status').textContent='Saved in this tab. Export regularly.';}
  catch(e){el('status').textContent=e.message;}
};
el('export').onclick=()=>{
  if(dirty){el('status').textContent='Save the current clip before exporting.';return;}
  const doc={version,sampleId,reviews:[...saved.values()]};
  const url=URL.createObjectURL(new Blob([JSON.stringify(doc,null,2)+'\n'],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;
  a.download='klip-calibration-review-'+sampleId.slice(0,12)+'.json';
  document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);
  el('status').textContent='Downloaded private review JSON. No information uploaded.';
};
el('import').onclick=()=>el('file').click();
el('file').onchange=async()=>{
  try{
    const f=el('file').files[0];if(!f)return;
    if(f.size>2000000)throw Error('Review exceeds size limit.');
    const doc=JSON.parse(await f.text());
    if(doc.version!==version||doc.sampleId!==sampleId||!Array.isArray(doc.reviews))
      throw Error('Import is not from this frozen sample.');
    const known=new Map(clips.map(x=>[x.id,x])), fresh=new Map();
    for(const r of doc.reviews){
      const c=known.get(r.recordId);
      if(!c||c.source!==r.sourceSha256||fresh.has(r.recordId))
        throw Error('Duplicate or mismatched review.');
      fresh.set(r.recordId,r);
    }
    saved.clear();for(const [id,r] of fresh)saved.set(id,r);
    render();el('status').textContent='Imported '+saved.size+' saved reviews.';
  }catch(e){el('status').textContent=e.message;}
};
render();
</script></body></html>""".replace("__ITEMS__",payload).replace(
        "__TAGS__",tags).replace("__QUALITY__",quality).replace(
        "__SAMPLE_ID__",manifest["sampleId"]).replace("__REVIEW_VERSION__",REVIEW_VERSION)

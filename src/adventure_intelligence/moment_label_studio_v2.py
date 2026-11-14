"""Local offline owner annotation UI for source-backed Klip V2 proxy clips.

No network, no automatic classification, no unapproved original video I/O.
Annotations live in memory until explicitly exported as private JSON.
"""
from __future__ import annotations

import json
from html import escape

from .moment_edit_v2 import ANNOTATION_VERSION, TAGS, SHA, ID


def render_label_studio(rows: list[dict], manifest_sha: str) -> str:
    if not SHA.fullmatch(manifest_sha):
        raise ValueError("Invalid frozen packet manifest")
    playlist = []
    for row in rows:
        rid = row["recordId"]
        if (not ID.fullmatch(str(rid)) or not SHA.fullmatch(str(row["sourceAssetSha256"]))
                or row["sport"] not in (*TAGS, "unknown")
                or type(row["sourceStartMs"]) is not int
                or type(row["sourceEndMs"]) is not int
                or not 0 <= row["sourceStartMs"] < row["sourceEndMs"] <= 86_400_000):
            raise ValueError("Unsafe source-linked UI candidate")
        playlist.append({
            "id": rid, "source": row["sourceAssetSha256"],
            "sport": row["sport"], "start": row["sourceStartMs"],
            "end": row["sourceEndMs"],
            "humanPreference": row.get("humanPreference"),
            "url": "media/" + rid + ".mp4",
        })
    payload = json.dumps(playlist, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")
    tags = json.dumps({k: sorted(v) for k, v in TAGS.items()}, separators=(",", ":"))
    return r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; media-src 'self' file:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; object-src 'none'; base-uri 'none'">
<title>Klip V2 | Moment Label Studio</title>
<style>
:root{color-scheme:dark;--bg:#0d151b;--panel:#18232c;--line:#324654;--ink:#edf2f4;--muted:#9db1bc;--accent:#8cd4c8}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 system-ui,sans-serif}
main{max-width:1140px;margin:auto;padding:24px}h1{font-size:23px;margin:0 0 4px}h2{font-size:15px;margin:0 0 11px}
header{border-bottom:1px solid var(--line);padding-bottom:16px;margin-bottom:20px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:14px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:15px}
video{width:100%;max-height:480px;aspect-ratio:16/9;background:#050a0f;border-radius:8px;object-fit:contain}
button,select,input{background:#243542;color:var(--ink);border:1px solid #4c6776;border-radius:8px;padding:8px 11px;font:inherit}
button{cursor:pointer}button:hover{border-color:var(--accent)}button:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--accent)}
button:disabled{opacity:.45}label{display:block;color:var(--muted);font-size:12px;margin-bottom:5px}
input[type=number],input[type=text],select{max-width:100%;width:100%}
.tags{display:flex;gap:7px;flex-wrap:wrap}.tag{display:inline-flex;gap:5px;align-items:center;border:1px solid #3c5564;border-radius:999px;padding:5px 9px}
.tag label{margin:0;color:var(--ink)}input[type=checkbox]{width:auto}
.row{display:flex;gap:9px;align-items:center;flex-wrap:wrap}.spaced{justify-content:space-between}
.muted{color:var(--muted)}.note{color:#c7ded5;border-left:3px solid var(--accent);padding-left:11px}
code{color:#c4f0eb}small{color:var(--muted)}strong{color:#f3f8fa}
#defects li{padding:4px}.error{color:#f1afa9}#export{background:#38685f}
@media(max-width:740px){.grid{grid-template-columns:1fr}main{padding:12px}}
</style></head><body><main>
<header><div class="row spaced"><div><h1>Klip / Moment Label Studio</h1>
<div class="muted">Private, offline. Tag scenes and preserve the complete event.</div></div>
<div id="progress" class="muted"></div></div></header>
<div class="panel"><p class="note">These are approximate-seek local proxy previews.
All timestamps below are absolute source-relative seconds, not original camera PTS.
Labels remain in this browser session until you export them; nothing uploads or publishes.</p>
<div class="row spaced"><div class="row"><label for="sport">Filter</label>
<select id="sport"><option value="all">All activities</option><option value="ski">Ski</option>
<option value="snorkeling">Snorkeling</option><option value="bike">Bike</option>
<option value="unknown">Unknown</option></select></div>
<div class="row"><button id="prev">Previous</button><button id="next">Next</button></div></div>
<p id="clipMeta" class="muted"></p><video id="video" controls playsinline preload="metadata"></video></div>
<div class="grid"><section class="panel"><h2>1. What is visible?</h2>
<p class="muted">Select as many applicable categories as you can actually verify.
Uncertain sightings and unseen landings should stay untagged.</p>
<div id="tags" class="tags"></div>
<div style="margin-top:12px"><label for="region">Region / outing label (only if personally verified)</label>
<input id="region" type="text" maxlength="100" placeholder="Optional owner-provided place or outing"></div>
</section>
<section class="panel"><h2>2. Full event timing</h2>
<p class="muted">Mark the start of the actual action/encounter and its visible
outcome or departure. Leave both blank if the event is not identifiable.</p>
<div class="row"><button id="markStart">Mark start at playhead</button>
<button id="markEnd">Mark end at playhead</button><button id="clearEvent">Clear</button></div>
<div class="grid" style="margin-top:10px"><div><label for="eventStart">Start (source seconds)</label>
<input id="eventStart" type="number" min="0" step="0.001" placeholder="Unknown"></div>
<div><label for="eventEnd">End (source seconds)</label>
<input id="eventEnd" type="number" min="0" step="0.001" placeholder="Unknown"></div></div>
</section></div>
<section class="panel"><h2>3. Filming defects</h2>
<p class="muted">Mark only directly visible intervals. A quality defect inside
the essential event is flagged for review, never silently cut away.</p>
<div class="row"><select id="defect" style="width:190px">
<option value="shake">Shaky framing</option><option value="waterline">Out of water / waterline</option>
<option value="blur">Out of focus or motion blur</option><option value="occlusion">Lens/subject obscured</option>
<option value="water_drop">Water drop on lens</option><option value="low_visibility">Poor visibility</option>
<option value="flat_traverse">Uneventful traverse</option></select>
<button id="defectStart">Mark defect in</button><button id="defectEnd">Mark defect out</button>
<button id="addDefect">Add defect span</button><span id="defectStatus" class="muted"></span></div>
<ul id="defects"></ul></section>
<div class="panel"><div class="row spaced">
<div><button id="save">Save labels for this clip</button><button id="clear">Clear this clip</button></div>
<div><button id="import">Import JSON</button><button id="export">Export JSON labels</button>
<input id="importFile" type="file" accept=".json,application/json" hidden></div></div>
<p id="status" role="status" class="muted">No labels have been saved yet. Export before closing this page.</p></div>
</main><script>
'use strict';
const items=__ITEMS__;
const allowed=__TAGS__;
const manifestSha='__MANIFEST__';
const version='__VERSION__';
const decisions=new Map();
const el=(id)=>document.getElementById(id);
let filtered=[],index=0,defStart=null,defEnd=null,defects=[],dirty=false;
function blank(x){return {recordId:x.id,sourceAssetSha256:x.source,tags:[],
  eventStartMs:null,eventEndMs:null,qualitySpans:[],regionLabel:null};}
function seconds(v){return v===null?'':(v/1000).toFixed(3);}
function current(){
  const x=filtered[index]; if(!x)return null;
  return x.start+Math.round((el('video').currentTime||0)*1000);
}
function rebuild(){
  const previous=filtered[index]?.id;
  const f=el('sport').value;
  filtered=items.filter(x=>f==='all'||x.sport===f);
  index=Math.max(0,filtered.findIndex(x=>x.id===previous));
  render();
}
function exportCurrent(){
  const x=filtered[index];if(!x)return null;
  const d=blank(x);
  d.tags=Array.from(document.querySelectorAll('input[data-tag]:checked')).map(n=>n.value).sort();
  const a=el('eventStart').value.trim(),b=el('eventEnd').value.trim();
  const parse=(v)=>v===''?null:Math.round(Number(v)*1000);
  d.eventStartMs=parse(a);d.eventEndMs=parse(b);
  d.qualitySpans=defects.slice();
  d.regionLabel=el('region').value.trim()||null;
  if((d.eventStartMs===null)!==(d.eventEndMs===null))throw new Error('Set both event bounds or neither');
  if(d.eventStartMs!==null&&!(x.start<=d.eventStartMs&&d.eventStartMs<d.eventEndMs&&d.eventEndMs<=x.end))
    throw new Error('Event start/end must lie inside this source clip');
  for(const span of d.qualitySpans){
    if(!(x.start<=span.startMs&&span.startMs<span.endMs&&span.endMs<=x.end))
      throw new Error('Defect outside clip bounds');
  }
  return d;
}
function save(){
  try{
    const d=exportCurrent();if(!d)return;
    decisions.set(d.recordId,d);dirty=false;
    el('status').textContent='Saved in memory. Export JSON to preserve these labels.';
    progress();
  }catch(e){el('status').textContent=String(e.message);el('status').className='error';}
}
function progress(){el('progress').textContent=decisions.size+' / '+items.length+' records saved';}
function renderDefects(){
  const ul=el('defects');ul.replaceChildren();
  defects.forEach((s,i)=>{
    const li=document.createElement('li');
    li.textContent=s.kind+' · '+seconds(s.startMs)+' to '+seconds(s.endMs)+'s ';
    const b=document.createElement('button');b.textContent='Remove';
    b.onclick=()=>{defects.splice(i,1);dirty=true;renderDefects();};
    li.appendChild(b);ul.appendChild(li);
  });
}
function render(){
  progress(); const x=filtered[index];
  if(!x){el('clipMeta').textContent='No matching clips';el('video').removeAttribute('src');return;}
  el('video').pause();el('video').src=x.url;el('video').load();
  el('clipMeta').textContent=(index+1)+' / '+filtered.length+' · '+x.sport+
    ' · source '+x.source.slice(0,12)+' · '+seconds(x.start)+'–'+seconds(x.end)+'s'+
    (x.humanPreference?' · review: '+x.humanPreference:' · not reviewed');
  const d=decisions.get(x.id)||blank(x);
  el('eventStart').value=seconds(d.eventStartMs);
  el('eventEnd').value=seconds(d.eventEndMs);
  el('region').value=d.regionLabel||'';
  const box=el('tags');box.replaceChildren();
  for(const name of (allowed[x.sport]||[])){
    const wrap=document.createElement('span');wrap.className='tag';
    const checkbox=document.createElement('input');checkbox.type='checkbox';
    checkbox.value=name;checkbox.dataset.tag='1';checkbox.checked=d.tags.includes(name);
    checkbox.id='tag-'+name;
    const label=document.createElement('label');label.htmlFor=checkbox.id;
    label.textContent=name.replaceAll('_',' ');
    wrap.append(checkbox,label);box.append(wrap);
  }
  defects=d.qualitySpans.slice();defStart=null;defEnd=null;
  el('defectStatus').textContent='';renderDefects();
  el('prev').disabled=index===0;el('next').disabled=index===filtered.length-1;
  el('status').className='muted';el('status').textContent='Mark and save this clip; export all saved labels as JSON.';
  dirty=false;
}
el('sport').onchange=rebuild;
function move(delta){if(dirty){el('status').textContent='Unsaved changes: save this clip before navigating.';return;}
  index=Math.min(filtered.length-1,Math.max(0,index+delta));render();}
el('prev').onclick=()=>move(-1);el('next').onclick=()=>move(1);
for(const id of ['eventStart','eventEnd','region'])el(id).oninput=()=>{dirty=true;};
el('tags').onchange=()=>{dirty=true;};
el('markStart').onclick=()=>{const t=current();if(t!==null){el('eventStart').value=seconds(t);dirty=true;}};
el('markEnd').onclick=()=>{const t=current();if(t!==null){el('eventEnd').value=seconds(t);dirty=true;}};
el('clearEvent').onclick=()=>{el('eventStart').value='';el('eventEnd').value='';dirty=true;};
el('defectStart').onclick=()=>{defStart=current();el('defectStatus').textContent='In: '+seconds(defStart)+'s';};
el('defectEnd').onclick=()=>{defEnd=current();el('defectStatus').textContent='Out: '+seconds(defEnd)+'s';};
el('addDefect').onclick=()=>{
  const x=filtered[index];if(!x)return;
  if(defStart===null||defEnd===null||!(x.start<=defStart&&defStart<defEnd&&defEnd<=x.end)){
    el('defectStatus').textContent='Mark valid in/out timestamps first.';return;}
  defects.push({kind:el('defect').value,startMs:defStart,endMs:defEnd});
  defStart=null;defEnd=null;dirty=true;renderDefects();el('defectStatus').textContent='Added.';
};
el('save').onclick=save;
el('clear').onclick=()=>{const x=filtered[index];if(x){decisions.delete(x.id);render();}};
el('export').onclick=()=>{
  if(dirty){el('status').textContent='Save this clip before exporting.';return;}
  const doc={version:version,manifestSha256:manifestSha,moments:[...decisions.values()]};
  const blob=new Blob([JSON.stringify(doc,null,2)+'\n'],{type:'application/json'});
  const url=URL.createObjectURL(blob);const a=document.createElement('a');
  a.href=url;a.download='klip-v2-owner-annotations-'+manifestSha.slice(0,12)+'.json';
  document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);
  el('status').textContent='Exported local JSON. Save this file within the private workspace reports directory.';
};
el('import').onclick=()=>el('importFile').click();
el('importFile').onchange=async()=>{
  const file=el('importFile').files[0];if(!file)return;
  try{
    if(file.size>2000000)throw new Error('Too large');
    const doc=JSON.parse(await file.text());
    if(doc.version!==version||doc.manifestSha256!==manifestSha||!Array.isArray(doc.moments))
      throw new Error('Not the same frozen packet annotation export');
    const byId=new Map(items.map(x=>[x.id,x]));const fresh=new Map();
    for(const d of doc.moments){
      const x=byId.get(d.recordId);
      if(!x||d.sourceAssetSha256!==x.source||fresh.has(d.recordId))
        throw new Error('Unknown/duplicate source-linked annotation');
      fresh.set(d.recordId,d);
    }
    decisions.clear();for(const [id,d] of fresh)decisions.set(id,d);
    dirty=false;render();el('status').textContent='Imported '+fresh.size+' records. Review and export again before closing.';
  }catch(e){el('status').textContent='Import blocked: '+String(e.message);}
};
rebuild();
</script></body></html>""".replace("__ITEMS__", payload).replace(
        "__TAGS__", tags).replace("__MANIFEST__", manifest_sha).replace(
        "__VERSION__", ANNOTATION_VERSION)

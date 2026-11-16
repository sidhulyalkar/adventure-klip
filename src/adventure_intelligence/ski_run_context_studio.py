"""Offline run-context playback and owner feature-marker interface.

A first-person video preview is for human verification of the full source
sequence, not evidence that every second of this context is a ski run.
"""
from __future__ import annotations
import json
from .ski_run_context import OWNER_VERSION, FEATURES, owner_annotation_template


def page(plan:dict) -> str:
    header = {
        "source": plan["sourceAssetSha256"],
        "record": plan["recordId"],
        "contextA": plan["contextSourceStartMs"],
        "contextB": plan["contextSourceEndMs"],
        "oldA": plan["anchorSourceStartMs"],
        "oldB": plan["anchorSourceEndMs"],
    }
    js=json.dumps(header,separators=(",",":")).replace("<","\\u003c")
    choices=json.dumps(sorted(FEATURES),separators=(",",":"))
    template=json.dumps(owner_annotation_template(plan),separators=(",",":"))
    return r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; media-src 'self' file:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; object-src 'none'">
<title>Klip / Full Run Context</title>
<style>
:root{color-scheme:dark;background:#0d1720;color:#eff5f6;font:14px/1.55 system-ui}
*{box-sizing:border-box}body{max-width:1080px;margin:0 auto;padding:20px}
h1{font-size:24px;margin:0 0 4px}h2{font-size:16px;margin:0 0 10px}
.muted{color:#b2c4ca}.panel{padding:17px;background:#1a2932;border:1px solid #37515c;border-radius:12px;margin:15px 0}
.note{border-left:3px solid #99cbbc;background:#20353f;padding:11px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:13px}
video{width:100%;height:auto;aspect-ratio:16/9;object-fit:contain;background:black;border-radius:8px}
button,select,input{color:#eff5f6;background:#263b48;border:1px solid #5d7a85;padding:9px;font:inherit;border-radius:7px}
button{cursor:pointer}label{display:block;color:#b3c7cb;margin:8px 0 4px}
input[type=number],select{width:100%}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
#events li{margin:8px 0}button:hover{border-color:#9de0c9}
strong{color:#eff5f6}@media(max-width:700px){.grid{grid-template-columns:1fr}}
</style></head><body>
<header><h1>Explore the complete ski line</h1>
<p class="muted">Longer context around an original 20-second selection. Local and private.</p>
</header>
<p class="note">This longer preview belongs to a single original source recording.
It is not automatically proven to be a continuous downhill run. Run start/end
and individual terrain features must be reviewed from playback. Source-relative
times are approximate due to preview seeking and re-encoding. No original media
is changed, no training labels are submitted and nothing is published.</p>
<div class="panel">
<p id="summary"></p><video id="video" controls preload="metadata" playsinline
src="media/run-context.mp4"></video>
<p class="muted">Old selection: <span id="old"></span>.
This preview may show approach, multiple features and ride-away past that cutoff.
If the real run extends past this context too, regenerate a larger bounded preview.</p>
</div>
<div class="grid">
<section class="panel"><h2>1. Mark the continuous run</h2>
<div class="row"><button id="markIn">Mark run start at playhead</button>
<button id="markOut">Mark run end at playhead</button>
<button id="clearTimes">Clear</button></div>
<label for="start">Run start (absolute source seconds)</label><input id="start" type="number" step=".001">
<label for="end">Run end (absolute source seconds)</label><input id="end" type="number" step=".001">
<label for="kind">Run type</label>
<select id="kind"><option value="unverified">Unverified context</option>
<option value="terrain_park">Terrain park line</option>
<option value="tree_line">Tree line</option>
<option value="speed_line">Speed/flow line</option>
<option value="mixed">Mixed skiing</option><option value="transit">Transit / not a run</option></select>
<label for="decision">Would you save this full run?</label>
<select id="decision"><option value="unsure">Unsure</option>
<option value="keep">Keep run</option><option value="skip">Skip / not noteworthy</option></select>
<label><input id="verified" type="checkbox"> I verified run start/end from continuous playback</label>
</section>
<section class="panel"><h2>2. Mark individual features in sequence</h2>
<p class="muted">Each feature is a moment inside the run. Mark all jumps, rails,
boxes, transitions and tree sections rather than squeezing the line into one
20-second highlight.</p>
<label for="feature">Observed feature or transition</label>
<select id="feature"></select>
<button id="addFeature">Add at playhead</button><ul id="events"></ul>
</section>
</div>
<section class="panel"><div class="row">
<button id="export">Export source-linked run annotations</button>
<button id="import">Import annotations</button>
<input type="file" id="file" accept=".json,application/json" hidden>
</div>
<p id="status" class="muted" role="status">Annotations are local until exported.</p>
</section>
<script>
'use strict';
const context=__CONTEXT__,knownFeatures=__FEATURES__,baseline=__BASELINE__;
const data=JSON.parse(JSON.stringify(baseline));
const el=id=>document.getElementById(id);
const fmt=ms=>(ms/1000).toFixed(3);
const now=()=>context.contextA+Math.round(el('video').currentTime*1000);
el('summary').textContent='Verified source '+context.source.slice(0,12)+
 ' · expanded context '+fmt(context.contextA)+'–'+fmt(context.contextB)+'s';
el('old').textContent=fmt(context.oldA)+'–'+fmt(context.oldB)+'s';
for(const kind of knownFeatures){
  const node=document.createElement('option');node.value=kind;
  node.textContent=kind.replaceAll('_',' ');el('feature').append(node);
}
function refresh(){
  el('start').value=data.runStartMs===null?'':fmt(data.runStartMs);
  el('end').value=data.runEndMs===null?'':fmt(data.runEndMs);
  el('kind').value=data.runKind;el('decision').value=data.preference;
  el('verified').checked=data.reviewerVerifiedContinuousRun;
  const ul=el('events');ul.replaceChildren();
  data.featureMarkers.sort((a,b)=>a.sourceMs-b.sourceMs||a.kind.localeCompare(b.kind));
  for(let i=0;i<data.featureMarkers.length;i++){
    const p=data.featureMarkers[i],li=document.createElement('li');
    li.textContent=fmt(p.sourceMs)+'s · '+p.kind.replaceAll('_',' ')+' ';
    const button=document.createElement('button');button.textContent='Remove';
    button.onclick=()=>{data.featureMarkers.splice(i,1);refresh();};
    li.append(button);ul.append(li);
  }
}
el('markIn').onclick=()=>{data.runStartMs=now();refresh();};
el('markOut').onclick=()=>{data.runEndMs=now();refresh();};
el('clearTimes').onclick=()=>{
  data.runStartMs=null;data.runEndMs=null;data.reviewerVerifiedContinuousRun=false;refresh();
};
el('kind').onchange=()=>{data.runKind=el('kind').value;};
el('decision').onchange=()=>{data.preference=el('decision').value;};
el('verified').onchange=()=>{data.reviewerVerifiedContinuousRun=el('verified').checked;};
for(const [field,key] of [['start','runStartMs'],['end','runEndMs']]){
  el(field).onchange=()=>{
    const v=el(field).value.trim();data[key]=v===''?null:Math.round(Number(v)*1000);
  };
}
el('addFeature').onclick=()=>{
  const at=now();
  if(at<context.contextA||at>context.contextB){
    el('status').textContent='Feature must lie inside the expanded source range.';return;
  }
  data.featureMarkers.push({kind:el('feature').value,sourceMs:at});refresh();
};
function validate(){
  const a=data.runStartMs,b=data.runEndMs;
  if((a===null)!==(b===null)||
     (a!==null&&!(context.contextA<=a&&a<b&&b<=context.contextB)))
     throw Error('Select a complete run inside the context or leave both bounds blank.');
  if(data.reviewerVerifiedContinuousRun&&a===null)
     throw Error('Cannot verify a run without marking its boundaries.');
  if(data.featureMarkers.some(x=>x.sourceMs<context.contextA||x.sourceMs>context.contextB))
     throw Error('Feature markers must remain inside context.');
}
el('export').onclick=()=>{
  try{
    validate();
    const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)+'\n'],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;
    a.download='klip-run-'+context.source.slice(0,12)+'-'+context.oldA+'.json';
    document.body.append(a);a.click();a.remove();URL.revokeObjectURL(url);
    el('status').textContent='Exported local source-linked run annotation.';
  }catch(err){el('status').textContent=err.message;}
};
el('import').onclick=()=>el('file').click();
el('file').onchange=async()=>{
  try{
    const file=el('file').files?.[0];if(!file)return;
    if(file.size>300000)throw Error('Annotation too large');
    const incoming=JSON.parse(await file.text());
    if(incoming.version!==data.version||incoming.sourceAssetSha256!==context.source
       ||incoming.anchorRecordId!==context.record
       ||incoming.contextSourceStartMs!==context.contextA
       ||incoming.contextSourceEndMs!==context.contextB)
       throw Error('Run annotations come from a different source/context.');
    for(const key of Object.keys(data)){if(!(key in incoming))throw Error('Missing '+key);}
    for(const key of Object.keys(data))data[key]=incoming[key];
    validate();refresh();el('status').textContent='Imported; review before exporting.';
  }catch(err){el('status').textContent=err.message;}
};
refresh();
</script></body></html>""".replace("__CONTEXT__",js).replace(
        "__FEATURES__",choices).replace("__BASELINE__",template)

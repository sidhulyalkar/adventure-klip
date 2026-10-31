'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const token = document.querySelector('meta[name="klip-session"]').content;
  const state = {view:'review',assets:[],queue:[],page:0,activity:'all',index:0,
    hasNext:false,totalPages:0,totalAssets:0,current:null,selectedAsset:null,
    details:new Map(),folder:{kind:'all',value:''},seen:new Set(),busy:false,
    expanded:new Set(['day']),allExpanded:new Set(),visibleLimit:60,refreshing:false,
    previewBounds:null};
  const player = $('focusPlayer');
  const secs = ms => Number(ms||0)/1000;
  const mmss = s => {s=Math.max(0,Math.floor(Number(s)||0));return String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0');};
  const titleCase = s => String(s||'unknown').replace(/_/g,' ').replace(/\b\w/g,v=>v.toUpperCase());
  const dateOf = a => a.captureTime && /^\d{4}-\d{2}-\d{2}/.test(a.captureTime) ? a.captureTime.slice(0,10) : 'Unknown date';
  const videoUrl = id => '/api/media/'+encodeURIComponent(id)+'?token='+encodeURIComponent(token);
  const identity = item => [item.assetId,item.startMs,item.endMs,item.label].join(':');
  const element = (tag,cls,text) => {const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n;};
  function report(text,error=false){const m=$('reviewMessage');m.textContent=text;m.classList.toggle('error',error);}
  async function api(path,body) {
    const response=await fetch(path,{method:body?'POST':'GET',headers:{'X-Klip-Token':token,...(body?{'Content-Type':'application/json'}:{})},
      ...(body?{body:JSON.stringify(body)}:{}),cache:'no-store'});
    let data={};try{data=await response.json();}catch{}
    if(!response.ok)throw Error(data.error||'Unable to access local catalog');
    return data;
  }
  function page(name) {
    state.view=name;
    $('reviewPage').hidden=name!=='review';$('libraryPage').hidden=name!=='library';
    $('tabReview').classList.toggle('active',name==='review');$('tabLibrary').classList.toggle('active',name==='library');
    $('tabReview').setAttribute('aria-current',name==='review'?'page':'false');
    $('tabLibrary').setAttribute('aria-current',name==='library'?'page':'false');
    if(name==='library'){player.pause();renderLibrary();}
    else display();
    try{history.replaceState(null,'','#'+name);}catch{}
  }
  async function getDetail(id){
    if(!state.details.has(id))state.details.set(id,await api('/api/asset/'+encodeURIComponent(id)));
    return state.details.get(id);
  }
  function updatePanel(item) {
    window.KlipBoundaryInspector?.select(item,state.assets.find(a=>a.id===item.assetId));
    state.previewBounds=null;
    $('fieldStart').value=secs(item.startMs).toFixed(3);
    $('fieldEnd').value=secs(item.endMs).toFixed(3);
    $('fieldLabel').value=item.label||'moment';
    $('fieldDecision').value='uncertain';$('fieldHighlight').value='neutral';
    $('fieldNote').value='';
    const suggestion=item.trimSuggestion;
    $('trimProposal').hidden=!suggestion;
    if(suggestion){
      $('trimRangeLabel').textContent=mmss(secs(suggestion.startMs))+' – '+mmss(secs(suggestion.endMs));
      $('trimReason').textContent=[...(suggestion.reasons||[]),...(suggestion.warnings||[])].join(' ');
      $('applyTrim').hidden=!suggestion.canApply;
    }
  }
  function activeRange(){
    return state.previewBounds||state.current||{startMs:0,endMs:0};
  }
  function setSelected(item) {
    state.current=item;
    updatePanel(item);
    display();
  }
  function display(){
    const item=state.current;
    $('keepClip').disabled=!item||state.busy;$('maybeClip').disabled=!item||state.busy;$('skipClip').disabled=!item||state.busy;
    $('saveDetails').disabled=!item||state.busy;
    $('previousClip').disabled=state.busy||state.current?.manual||(!state.page&&state.index<=0);
    $('nextClip').disabled=state.busy||(!state.current?.manual&&!state.hasNext&&state.index>=state.queue.length-1);
    $('refreshCatalog').disabled=state.refreshing;
    $('focusFullscreen').disabled=!item;
    $('replayClip').disabled=!item;
    $('emptyNext').hidden=!state.hasNext;
    $('indexedCount').textContent=state.assets.length+' indexed';
    const count=state.queue.length;
    $('clipCounter').textContent=item&&item.manual?'From library':count?(state.index+1)+' of '+count+' in batch':'0 of 0';
    $('batchProgress').textContent='Review queue';
    $('batchDetail').textContent=state.totalAssets+' recordings indexed · batch '+(state.page+1)+' of '+Math.max(1,state.totalPages);
    if(!item){
      window.KlipBoundaryInspector?.select(null,null);
      $('clipTitle').textContent='Nothing to review yet';$('clipDescription').textContent='Browse your source recordings, or process additional video.';
      $('clipIn').textContent='00:00';$('clipOut').textContent='00:00';$('clipEvidence').textContent='No candidate selected';
      $('clipActivity').textContent='CLIP REVIEW';$('playerEmpty').hidden=false;
      $('emptyExplanation').textContent=state.assets.length
        ? 'No suggestions in this batch. More recordings may still be processing. Try the next batch or open Library.'
        : 'No indexed recordings yet. Index footage first, then refresh the catalog.';
      if(player.hasAttribute('src')){player.pause();player.removeAttribute('src');player.load();}
      return;
    }
    const asset=state.assets.find(a=>a.id===item.assetId);
    $('playerEmpty').hidden=true;
    $('clipTitle').textContent=titleCase(item.label)+' · '+(asset?asset.title:item.sourceName||'Recording');
    const shown=activeRange();
    $('clipDescription').textContent=(asset?dateOf(asset):'Unknown date')+' · '+
      (asset&&asset.region?asset.region+' · ':'')+
      (state.previewBounds?'Previewing selected cut, ':'Candidate interval, ')+
      mmss(secs(shown.endMs-shown.startMs))+' long';
    $('clipActivity').textContent=(item.activity||asset?.activity||'footage').toUpperCase();
    const range=activeRange();
    $('clipIn').textContent=mmss(secs(range.startMs));$('clipOut').textContent=mmss(secs(range.endMs));
    $('clipEvidence').textContent=item.manual?'Manually selected interval':'Suggestion · '+(item.lane||'unverified').replace(/_/g,' ')+' · not confirmed';
    const url=videoUrl(item.assetId);
    if(player.getAttribute('src')!==url){
      player.pause();player.src=url;
      player.load();
    }else seekStart();
  }
  function seekStart(){
    if(!state.current||!Number.isFinite(player.duration))return;
    const t=Math.min(secs(activeRange().startMs),Math.max(0,player.duration-0.1));
    try{player.currentTime=t;}catch{}
  }
  async function loadQueue({resetIndex=true,skipEmpty=true}={}){
    // Cap empty-page skips: 315 indexed recordings must not trigger 50+ requests
    // simply because analysis has not produced candidate intervals yet.
    let skipped=0;
    while(true){
      const data=await api('/api/queue?activity='+encodeURIComponent(state.activity)+'&page='+state.page);
      state.hasNext=data.hasNext;state.totalPages=data.totalPages;state.totalAssets=data.totalAssets;
      state.queue=data.moments.filter(x=>!state.seen.has(identity(x)));
      if(state.queue.length||!state.hasNext||!skipEmpty||skipped>=2)break;
      state.page++;skipped++;
    }
    if(resetIndex)state.index=0;
    else state.index=Math.min(state.index,Math.max(0,state.queue.length-1));
    state.current=state.queue[state.index]||null;
    if(state.current)updatePanel(state.current);
    display();
  }
  async function move(delta){
    if(state.busy)return;
    if(state.current&&state.current.manual){
      await loadQueue();return;
    }
    const next=state.index+delta;
    if(next>=0&&next<state.queue.length){state.index=next;setSelected(state.queue[next]);return;}
    if(next>=state.queue.length&&state.hasNext){state.page++;await loadQueue();return;}
    if(next<0&&state.page>0){state.page--;await loadQueue({skipEmpty:false});state.index=Math.max(0,state.queue.length-1);setSelected(state.queue[state.index]||null);}
  }
  async function save(highlight,{stay=false}={}){
    const item=state.current;if(!item||state.busy)return;
    const start=Number($('fieldStart').value),end=Number($('fieldEnd').value);
    const label=$('fieldLabel').value.trim();
    const asset=state.assets.find(x=>x.id===item.assetId);
    const limit=asset?Number(asset.duration)||Infinity:Infinity;
    if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<=start||end>limit){report('Choose a valid start and end time.',true);return;}
    if(!/^[a-z][a-z0-9_.:-]{0,79}$/.test(label)){report('Use a lowercase scene label (letters, numbers, underscores).',true);return;}
    state.busy=true;display();
    try{
      const result=await api('/api/reviews',{assetId:item.assetId,startSeconds:start,endSeconds:end,
        label,decision:$('fieldDecision').value,highlight,note:$('fieldNote').value.trim()});
      report(result.status==='unchanged'?'Already saved.':'Saved '+highlight+' to your local review history.');
      state.details.delete(item.assetId);
      // Preserve the editorial decision independently from the model's scene-label accuracy.
      state.seen.add(identity(item));
      const fresh=await api('/api/overview');state.assets=fresh.assets;
      if(stay){renderLibrary();return;}
      if(item.manual){state.page=0;await loadQueue();return;}
      const next=state.queue.filter(x=>!state.seen.has(identity(x)));
      state.queue=next;state.index=Math.min(state.index,Math.max(0,next.length-1));
      if(!next.length&&state.hasNext){state.page++;await loadQueue();return;}
      state.current=next[state.index]||null;
      if(state.current)updatePanel(state.current);
    }catch(e){report(e.message,true);}
    finally{state.busy=false;display();}
  }
  async function refreshCatalog(){
    if(state.busy||state.refreshing)return;
    state.refreshing=true;$('refreshCatalog').disabled=true;
    $('refreshCatalog').setAttribute('aria-busy','true');
    try{
      const data=await api('/api/overview');
      const before=state.assets.length;
      state.assets=data.assets;
      state.details.clear();
      if(state.view==='library'){
        const selected=state.selectedAsset;
        renderLibrary();
        if(selected&&state.assets.some(a=>a.id===selected))await openAsset(selected);
      }else if(!state.current?.manual){
        const previous=state.current?identity(state.current):null;
        await loadQueue({skipEmpty:false});
        const index=state.queue.findIndex(x=>identity(x)===previous);
        if(index>=0){state.index=index;setSelected(state.queue[index]);}
      }else display();
      report('Catalog refreshed: '+state.assets.length+' indexed recordings'+(state.assets.length>before?' (+'+(state.assets.length-before)+')':'')+'.');
    }catch(e){report('Could not refresh catalog: '+e.message,true);}
    finally{state.refreshing=false;$('refreshCatalog').disabled=false;$('refreshCatalog').removeAttribute('aria-busy');}
  }
  function folderItems(kind){
    const map=new Map();
    for(const a of state.assets){
      const value=kind==='day'?dateOf(a):kind==='activity'?titleCase(a.activity):
        kind==='folder'?(a.folder||'Unknown source'):kind==='region'?(a.region||'Unassigned'):
        a.reviews?'Reviewed':'Not reviewed';
      map.set(value,(map.get(value)||0)+1);
    }
    return [...map.entries()].sort((a,b)=>kind==='day'?b[0].localeCompare(a[0]):a[0].localeCompare(b[0]));
  }
  function renderFolders(){
    const root=$('folderTree');root.replaceChildren();
    const all=element('button','folder-item','All recordings');
    all.type='button';all.classList.toggle('active',state.folder.kind==='all');
    all.onclick=()=>selectFolder('all','');
    root.append(all);
    for(const [kind,title] of [['day','By day'],['folder','Source folders'],['region','Regions'],['activity','Activities'],['status','Review status']]){
      const group=element('section','folder-group');
      const entries=folderItems(kind);
      const expanded=state.expanded.has(kind);
      const heading=element('button','folder-heading');
      heading.type='button';
      heading.setAttribute('aria-expanded',String(expanded));
      heading.setAttribute('aria-label',title+', '+entries.length+' groups');
      heading.append(element('span','',title),element('small','',expanded?'⌄':'›'));
      heading.onclick=()=>{
        if(expanded)state.expanded.delete(kind);else state.expanded.add(kind);
        renderFolders();
      };
      group.append(heading);
      if(expanded){
        const limit=state.allExpanded.has(kind)?entries.length:Math.min(8,entries.length);
        for(const [value,count] of entries.slice(0,limit)){
          const row=element('button','folder-item');row.type='button';row.title=value;
          row.append(element('span','',value),element('small','',count));
          row.classList.toggle('active',state.folder.kind===kind&&state.folder.value===value);
          row.onclick=()=>selectFolder(kind,value);
          group.append(row);
        }
        if(entries.length>8){
          const more=element('button','folder-more',state.allExpanded.has(kind)?'Show fewer':'Show all '+entries.length);
          more.type='button';
          more.setAttribute('aria-expanded',String(state.allExpanded.has(kind)));
          more.onclick=()=>{
            if(state.allExpanded.has(kind))state.allExpanded.delete(kind);
            else state.allExpanded.add(kind);
            renderFolders();
          };
          group.append(more);
        }
      }
      root.append(group);
    }
  }
  function selectFolder(kind,value){state.folder={kind,value};state.selectedAsset=null;state.visibleLimit=60;renderLibrary();}
  function folderMatch(a){
    const {kind,value}=state.folder;
    return kind==='all'||(kind==='day'&&dateOf(a)===value)||
      (kind==='activity'&&titleCase(a.activity)===value)||
      (kind==='folder'&&(a.folder||'Unknown source')===value)||
      (kind==='region'&&(a.region||'Unassigned')===value)||
      (kind==='status'&&(a.reviews?'Reviewed':'Not reviewed')===value);
  }
  function renderLibrary(){
    renderFolders();
    const search=$('librarySearch').value.trim().toLowerCase();
    const list=state.assets.filter(a=>folderMatch(a)&&
      [a.title,a.region,dateOf(a),a.folder,a.activity].some(x=>String(x||'').toLowerCase().includes(search)))
      .sort((a,b)=>(b.captureTime||'').localeCompare(a.captureTime||'')||a.title.localeCompare(b.title));
    $('libraryTotal').textContent=state.assets.length+' recordings';
    $('breadcrumb').textContent=state.folder.kind==='all'?'All recordings':
      ({day:'Days',folder:'Source folders',region:'Regions',activity:'Activities',status:'Review status'}[state.folder.kind]||'All')+' / '+state.folder.value;
    const pane=$('fileList');pane.replaceChildren();
    if(!list.length)pane.append(element('p','no-files','No recordings in this view.'));
    for(const a of list.slice(0,state.visibleLimit)){
      const row=element('button','file-row');row.type='button';
      row.classList.toggle('selected',state.selectedAsset===a.id);
      const name=element('span','file-name');
      name.append(element('span','file-icon','▣'));
      const content=element('span','name-content');
      content.append(element('strong','',a.title),element('small','',a.region||titleCase(a.activity)));
      name.append(content);
      row.append(name,element('span','',dateOf(a)),element('span','',mmss(a.duration)),element('span','',a.reviews||'0'));
      row.onclick=()=>openAsset(a.id);
      pane.append(row);
    }
    if(list.length>state.visibleLimit){
      const more=element('button','files-more','Show '+Math.min(60,list.length-state.visibleLimit)+' more · '+list.length+' matches');
      more.type='button';
      more.onclick=()=>{state.visibleLimit+=60;renderLibrary();};
      pane.append(more);
    }
    if(!state.selectedAsset){
      $('assetDetail').replaceChildren();
      const div=element('div','detail-empty');div.append(element('span','empty-icon','▤'),element('p','','Select a recording to see its details and review moments.'));
      $('assetDetail').append(div);
    }
  }
  async function openAsset(id){
    state.selectedAsset=id;renderLibrary();
    const side=$('assetDetail');side.replaceChildren();
    side.append(element('p','hint','Loading recording…'));
    try{
      const detail=await getDetail(id);if(state.selectedAsset!==id)return;
      const asset=state.assets.find(a=>a.id===id);side.replaceChildren();
      if(window.matchMedia&&window.matchMedia('(max-width:650px)').matches){
        side.scrollIntoView({behavior:'smooth',block:'start'});
      }
      side.append(element('span','overline','RECORDING DETAILS'),element('h2','',asset.title));
      side.append(element('div','detail-label','DATE'),element('div','detail-value',dateOf(asset)));
      side.append(element('div','detail-label','SOURCE FOLDER'),element('div','detail-value',asset.folder||'Unknown'));
      side.append(element('div','detail-label','REGION · USER-ASSIGNED'));
      const editor=element('div','detail-input');
      const input=element('input');input.type='text';input.maxLength=80;input.placeholder='e.g. Lake Tahoe';input.value=asset.region||'';input.setAttribute('aria-label','Region name');
      const saveButton=element('button','secondary','Save');saveButton.type='button';
      saveButton.onclick=async()=>{saveButton.disabled=true;
        try{
          await api('/api/region',{assetId:id,region:input.value.trim()});
          const fresh=await api('/api/overview');state.assets=fresh.assets;
          renderLibrary();report('Region saved.');if(state.selectedAsset===id)openAsset(id);
        }catch(e){
          const warning=element('p','hint','Could not save region: '+e.message);
          warning.setAttribute('role','alert');editor.append(warning);
        }finally{saveButton.disabled=false;}
      };
      editor.append(input,saveButton);side.append(editor);
      side.append(element('div','detail-label','SUGGESTED MOMENTS'));
      const moments=element('div','detail-moments');
      if(!detail.suggestions.length)moments.append(element('p','hint','No model proposals for this recording. You can still mark a moment manually.'));
      for(const candidate of detail.suggestions.slice(0,15)){
        const button=element('button','moment-link',titleCase(candidate.kind));
        button.append(element('span','',mmss(secs(candidate.startMs))+' – '+mmss(secs(candidate.endMs))));
        button.onclick=()=>inspect({...candidate,assetId:id,activity:asset.activity,label:candidate.kind,manual:true});
        moments.append(button);
      }
      side.append(moments);
      if(detail.reviews&&detail.reviews.length){
        side.append(element('div','detail-label','REVIEWED MOMENTS'));
        for(const review of detail.reviews.slice(0,30)){
          const button=element('button','moment-link',titleCase(review.label)+' · '+titleCase(review.highlight));
          button.append(element('span','',mmss(secs(review.startMs))+' – '+mmss(secs(review.endMs))));
          button.onclick=()=>inspect({assetId:id,activity:asset.activity,label:review.label,
            startMs:review.startMs,endMs:review.endMs,manual:true});
          side.append(button);
        }
      }
      const inspectButton=element('button','primary detail-button','Review this recording');
      inspectButton.onclick=()=>inspect({assetId:id,activity:asset.activity,label:'moment',startMs:0,endMs:Math.round(Math.min(15,Number(asset.duration)||15)*1000),manual:true});
      side.append(inspectButton,element('p','hint','Virtual folders never move, rename, or modify source video files.'));
    }catch(e){side.replaceChildren(element('p','hint','Could not load recording: '+e.message));}
  }
  function inspect(item){state.current=item;state.index=0;updatePanel(item);page('review');report('Choose Keep, Later or Skip, or refine the interval and scene label.');}
  function toggleAnnotation(show){
    $('annotationPanel').hidden=!show;
    $('annotateToggle').setAttribute('aria-expanded',String(show));
    if(show)$('fieldLabel').focus();
  }
  function previewMarkedRange(){
    const item=state.current;if(!item)return;
    const start=Number($('fieldStart').value),end=Number($('fieldEnd').value);
    const asset=state.assets.find(a=>a.id===item.assetId);
    if(!asset||!Number.isFinite(start)||!Number.isFinite(end)||
       start<0||end<=start||end>(Number(asset.duration)||0)){
      report('Check start and end: they must be inside the source recording.',true);
      return;
    }
    state.previewBounds={startMs:Math.round(start*1000),endMs:Math.round(end*1000)};
    display();seekStart();player.play().catch(()=>{});
    report('Previewing your in/out selection. Save the annotation to keep these boundaries.');
  }
  function previewTrim(){
    const item=state.current;
    const suggestion=item?.trimSuggestion;
    if(!suggestion?.canApply)return;
    const start=suggestion.startMs,end=suggestion.endMs;
    if(!(Number.isInteger(start)&&Number.isInteger(end)&&start>=0&&end>start))return;
    $('fieldStart').value=secs(start).toFixed(3);
    $('fieldEnd').value=secs(end).toFixed(3);
    state.previewBounds={startMs:start,endMs:end};
    display();
    seekStart();
    player.play().catch(()=>{});
    report('Previewing suggested boundaries; save only after checking the first and final frames.');
  }
  function fullscreenPlayer(){
    if(!state.current)return;
    const node=player;
    if(node.requestFullscreen){
      try{
        const result=node.requestFullscreen();
        if(result&&typeof result.catch==='function'){
          result.catch(()=>report('Fullscreen is unavailable in this browser.',true));
        }
      }catch(e){report('Fullscreen is unavailable in this browser.',true);}
    }
    else if(node.webkitEnterFullscreen){node.webkitEnterFullscreen();}
    else report('Fullscreen is unavailable in this browser.',true);
  }
  function bind(){
    $('tabReview').onclick=()=>page('review');
    $('tabLibrary').onclick=()=>page('library');
    $('emptyLibrary').onclick=()=>page('library');
    $('emptyNext').onclick=()=>move(1).catch(e=>report(e.message,true));
    $('previousClip').onclick=()=>move(-1).catch(e=>report(e.message,true));
    $('nextClip').onclick=()=>move(1).catch(e=>report(e.message,true));
    $('keepClip').onclick=()=>save('keep');
    $('maybeClip').onclick=()=>save('neutral');
    $('skipClip').onclick=()=>save('skip');
    $('replayClip').onclick=()=>{seekStart();player.play().catch(()=>{});};
    $('applyTrim').onclick=previewTrim;
    $('previewMarked').onclick=previewMarkedRange;
    player.addEventListener('loadedmetadata',seekStart);
    $('annotateToggle').onclick=()=>toggleAnnotation($('annotationPanel').hidden);
    $('closeAnnotation').onclick=()=>toggleAnnotation(false);
    $('annotationForm').onsubmit=e=>{e.preventDefault();save($('fieldHighlight').value,{stay:true});};
    $('markStart').onclick=()=>{$('fieldStart').value=player.currentTime.toFixed(1);};
    $('markEnd').onclick=()=>{$('fieldEnd').value=player.currentTime.toFixed(1);};
    $('librarySearch').oninput=()=>{state.visibleLimit=60;renderLibrary();};
    $('refreshCatalog').onclick=refreshCatalog;
    $('focusFullscreen').onclick=fullscreenPlayer;
    $('reviewActivity').onchange=()=>{state.activity=$('reviewActivity').value;state.page=0;state.seen.clear();loadQueue().catch(e=>report(e.message,true));};
    $('helpToggle').onclick=()=>{$('shortcutHelp').hidden=!$('shortcutHelp').hidden;};
    player.addEventListener('timeupdate',()=>{
      if(!state.current)return;
      const range=activeRange();
      const fraction=Math.max(0,Math.min(1,(player.currentTime-secs(range.startMs))/
        Math.max(.001,secs(range.endMs-range.startMs))));
      $('rangeFill').style.width=(100*fraction).toFixed(2)+'%';
      if(player.currentTime>=secs(range.endMs)-0.06&&!player.paused){player.pause();}
    });
    player.addEventListener('error',()=>report('Video could not play in this browser. Try Safari for DJI HEVC recordings.',true));
    document.addEventListener('keydown',e=>{
      if(e.metaKey||e.ctrlKey||e.altKey||state.view!=='review')return;
      const t=e.target;
      if(t&&(/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)||t.isContentEditable))return;
      if(t&&t.tagName==='BUTTON'&&(e.key===' '||e.key==='Enter'))return;
      if(e.key===' '){e.preventDefault();if(player.paused)player.play().catch(()=>{});else player.pause();}
      else if(e.key==='ArrowLeft'){e.preventDefault();move(-1).catch(err=>report(err.message,true));}
      else if(e.key==='ArrowRight'){e.preventDefault();move(1).catch(err=>report(err.message,true));}
      else if(e.key.toLowerCase()==='k'){e.preventDefault();save('keep');}
      else if(e.key.toLowerCase()==='x'){e.preventDefault();save('skip');}
      else if(e.key.toLowerCase()==='m'){e.preventDefault();save('neutral');}
      else if(e.key.toLowerCase()==='f'){e.preventDefault();fullscreenPlayer();}
      else if(e.key==='['){e.preventDefault();$('fieldStart').value=player.currentTime.toFixed(1);toggleAnnotation(true);}
      else if(e.key===']'){e.preventDefault();$('fieldEnd').value=player.currentTime.toFixed(1);toggleAnnotation(true);}
    });
  }
  async function start(){
    bind();
    try{
      const overview=await api('/api/overview');state.assets=overview.assets;
      await loadQueue();
      if(location.hash==='#library')page('library');
    }catch(e){report('Could not open Klip catalog: '+e.message,true);$('clipTitle').textContent='Catalog unavailable';}
  }
  start();
})();
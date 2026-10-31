'use strict';
/* Klip Focus: a single large source preview, then one deliberate decision. */
(() => {
  const $ = id => document.getElementById(id);
  const token = document.querySelector('meta[name="klip-session"]').content;
  const state = {view:'review',page:0,activity:'all',items:[],position:0,current:null,
    library:[],group:'day',active:null,detail:null,tab:'reviews',queueGeneration:0,seen:new Set()};
  const mm = ms => {const n=Math.floor(Math.max(0,Number(ms)||0)/1000);
    return String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0');};
  const human = str => String(str||'moment').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
  const media = id => '/api/media/'+encodeURIComponent(id)+'?token='+encodeURIComponent(token);
  const elem=(type,cls,value)=>{const n=document.createElement(type);if(cls)n.className=cls;
    if(value!==undefined)n.textContent=value;return n;};
  let notificationTimer=0,streamGeneration=0;
  function toast(message,error=false){
    const n=$('toast');n.textContent=message;n.hidden=false;n.classList.toggle('error',error);
    clearTimeout(notificationTimer);notificationTimer=setTimeout(()=>n.hidden=true,4200);
  }
  async function api(path,opts={}){
    const response=await fetch(path,{...opts,cache:'no-store',
      headers:{'X-Klip-Token':token,...(opts.headers||{})}});
    const body=await response.json();
    if(!response.ok)throw Error(body.error||('Request failed: '+response.status));
    return body;
  }
  const post=(path,data)=>api(path,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  const fail=e=>toast(e.message||String(e),true);
  function cinema(enabled){
    const active=Boolean(enabled)&&state.view==='review';
    document.body.classList.toggle('cinema-active',active);
    $('cinemaToggle').setAttribute('aria-pressed',String(active));
    $('cinemaToggle').title=active?'Return to standard layout (F)':'Expand the playback stage (F)';
  }
  function progress(index,total){
    const percent=total?Math.min(100,Math.round(100*(index+1)/total)):0;
    $('reviewProgress').setAttribute('aria-valuenow',String(percent));
    $('reviewProgress').setAttribute('aria-valuetext',total?
      (index+1)+' of '+total+' proposed moments in this batch':'No proposed moments in this batch');
    $('reviewProgressFill').style.width=percent+'%';
  }
  function replay(){
    if(!state.current)return;
    const player=$('reviewPlayer');
    player.currentTime=Number($('clipIn').value)||state.current.startMs/1000;
    player.play().catch(()=>toast('This browser cannot play the source codec. Try Safari.',true));
  }
  function view(name){
    state.view=name;
    for(const key of ['review','library','sequence']){
      $(key+'View').classList.toggle('hidden',name!==key);
      const b=document.querySelector('[data-view="'+key+'"]');
      b.classList.toggle('nav-current',name===key);
      if(name===key)b.setAttribute('aria-current','page');
      else b.removeAttribute('aria-current');
    }
    if(name!=='review'){
      $('reviewPlayer').pause();
      cinema(false);
    }
    if(name!=='library')$('libraryPlayer').pause();
    if(name==='library')refreshLibrary().catch(fail);
    if(name==='sequence')window.dispatchEvent(new Event('klip-sequence-visible'));
  }
  async function totals(){
    const d=await api('/api/overview');$('reviewedTotal').textContent=d.reviewed+' reviewed';
  }
  async function loadQueue(selectLast=false){
    const generation=++state.queueGeneration;
    const data=await api('/api/queue?activity='+encodeURIComponent(state.activity)+'&page='+state.page);
    if(generation!==state.queueGeneration)return;
    state.items=data.moments.filter(c=>!state.seen.has(c.assetId+'|'+c.startMs+'|'+c.endMs));
    $('queueExplanation').textContent='Suggestions, not verified actions · '+data.totalAssets+
      ' recordings · '+data.totalPages+' pages';
    $('nextBatch').disabled=!data.hasNext;
    if(state.items.length)show(selectLast?state.items.length-1:0);
    else {
      state.current=null;
      $('reviewPosition').textContent='0 / 0';$('reviewSource').textContent='No moments in this batch';
      $('reviewMeta').textContent='Try the next batch or a different activity.';
      $('clipHeading').textContent='Nothing to review here';
      const video=$('reviewPlayer');video.pause();video.removeAttribute('src');video.load();
      $('emptyScreen').classList.remove('hidden');
      $('stageState').classList.add('hidden');
      $('replayClip').disabled=true;
      progress(0,0);
      for(const id of ['keepClip','skipClip','wrongClip'])$(id).disabled=true;
    }
  }
  function show(index){
    const c=state.items[index];if(!c)return;
    state.position=index;state.current=c;
    $('reviewPosition').textContent=(index+1)+' / '+state.items.length;
    progress(index,state.items.length);
    $('reviewSource').textContent=c.sourceName||'Indexed recording';
    $('reviewMeta').textContent='Indexed activity: '+human(c.activity)+' · unverified · '+human(c.lane);
    $('clipSpan').textContent=mm(c.startMs)+' — '+mm(c.endMs);
    $('clipNature').textContent='Proposed: '+human(c.label)+' · not confirmed';
    $('clipHeading').textContent='Would you keep this?';
    $('clipIn').value=(c.startMs/1000).toFixed(1);
    $('clipOut').value=(c.endMs/1000).toFixed(1);
    $('clipLabel').value='highlight_moment';
    $('evidenceSummary').textContent=(c.reasons||[]).join(' · ')||'Camera motion is not verified action';
    $('clipObjectEvidence').textContent='Open Details to inspect cached object and scene evidence.';
    $('feedbackForm').classList.add('hidden');$('detailsPanel').classList.add('hidden');
    $('detailsToggle').setAttribute('aria-expanded','false');
    for(const id of ['keepClip','skipClip','wrongClip','replayClip'])$(id).disabled=false;
    $('previousClip').disabled=index===0&&state.page===0;
    const player=$('reviewPlayer'),generation=++streamGeneration;
    player.pause();player.src=media(c.assetId);player.load();
    $('emptyScreen').classList.add('hidden');
    $('stageState').textContent='Opening verified local footage…';
    $('stageState').classList.remove('hidden');
    player.onerror=()=>{
      if(generation!==streamGeneration)return;
      $('stageState').textContent='Playback unavailable here · try Safari for DJI footage';
      $('stageState').classList.remove('hidden');
    };
    player.onloadedmetadata=()=>{
      if(generation!==streamGeneration)return;
      $('stageState').classList.add('hidden');
      player.currentTime=c.startMs/1000;
    };
  }
  function next(){
    if(state.position+1<state.items.length)show(state.position+1);
    else if(!$('nextBatch').disabled){state.page++;loadQueue().catch(fail);}
    else toast('You have reached the end of this review selection.');
  }
  function previous(){
    if(state.position>0)show(state.position-1);
    else if(state.page>0){state.page--;loadQueue(true).catch(fail);}
  }
  async function loadClipMetadata(){
    if(!state.current)return;
    const snapshot=state.current;
    const node=$('clipObjectEvidence');
    node.textContent='Checking cached observations…';
    try{
      const start=Math.round(Number($('clipIn').value)*1000);
      const end=Math.round(Number($('clipOut').value)*1000);
      const query='/api/clip-metadata?asset='+encodeURIComponent(snapshot.assetId)+
        '&startMs='+start+'&endMs='+end;
      const result=await api(query);
      if(state.current!==snapshot || $('detailsPanel').classList.contains('hidden'))return;
      const regions=[['Entrance',result.head],['Exit',result.tail]];
      node.replaceChildren();
      for(const [name,entry] of regions){
        const section=elem('div','clip-edge-metadata');
        section.append(elem('strong','',name+' · '+mm(entry.startMs)+'–'+mm(entry.endMs)));
        const labels=entry.objectObservations.map(o=>o.label).slice(0,5);
        const contexts=entry.sceneContext.map(o=>o.label).slice(0,2);
        section.append(elem('span','',labels.length?
          'Object queries: '+labels.join(', ')+' (unverified)':'Object detection: unknown'));
        section.append(elem('span','',contexts.length?
          'Scene hints: '+contexts.join(', ')+' (relative similarity)':'Scene context: unknown'));
        section.append(elem('small','',
          entry.objectSampleCount+' object frames · '+
          entry.contextSampleCount+' context frames · '+
          entry.embeddingSamples+' embedding frames'));
        node.append(section);
      }
    }catch(e){node.textContent='No cached object evidence available: '+(e.message||String(e));}
  }
  function selectedRange(){
    const c=state.current;
    if(!c)throw Error('Select a clip first');
    const start=Number($('clipIn').value),end=Number($('clipOut').value);
    if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end<=start)
      throw Error('Please choose valid start and end seconds');
    return {assetId:c.assetId,startSeconds:start,endSeconds:end};
  }
  async function afterDecision(){
    // The queue can shift after each review. Preserve the next position,
    // rather than jumping back to the first candidate every time.
    const index=state.position;
    if(state.current)state.seen.add(state.current.assetId+'|'+state.current.startMs+'|'+state.current.endMs);
    await totals();
    const data=await api('/api/queue?activity='+encodeURIComponent(state.activity)+'&page='+state.page);
    state.items=data.moments.filter(c=>!state.seen.has(c.assetId+'|'+c.startMs+'|'+c.endMs));
    $('nextBatch').disabled=!data.hasNext;
    if(state.items.length)show(Math.min(index,state.items.length-1));
    else if(data.hasNext){state.page++;await loadQueue();}
    else await loadQueue();
    window.dispatchEvent(new Event('klip-review-changed'));
  }
  async function decide(kind){
    if(!state.current)return;
    if(kind==='keep'){
      try {
        await post('/api/reviews',{...selectedRange(),
          label:$('clipLabel').value||'highlight_moment',
          decision:'confirmed',highlight:'keep',note:'Approved in focused review'});
        toast('Kept. This moment is now in your approved shelf.');
        await afterDecision();
      }catch(e){fail(e);}
    }else {
      $('feedbackForm').classList.remove('hidden');
      $('feedbackForm').dataset.kind=kind;
      $('feedbackTitle').textContent=kind==='wrong'?
        'What did the detector get wrong?':'Why skip this clip?';
      $('feedbackReason').value='boring';$('feedbackNote').value='';
      $('feedbackNote').focus();
    }
  }
  async function submitFeedback(event){
    event.preventDefault();
    const reason=$('feedbackReason').value,note=$('feedbackNote').value.trim(),
      kind=$('feedbackForm').dataset.kind;
    if(note.length<5)return toast('Add a short explanation to help improve the model.',true);
    const meanings={lift:'ski_lift',flat:'slow_flat_skiing',surface:'surface_break',
      camera:'camera_adjustment',duplicate:'duplicate_scene',
      boring:'non_action',other:'non_action'};
    try {
      await post('/api/reviews',{...selectedRange(),
        label:kind==='wrong'?state.current.label:meanings[reason],
        decision:kind==='wrong'?'rejected':'confirmed',
        highlight:kind==='wrong'?'neutral':'skip',
        note:reason+': '+note});
      toast(kind==='wrong'?'Incorrect scene label recorded for reclassification.':
        'Skipped, with your explanation saved.');
      await afterDecision();
    }catch(e){fail(e);}
  }
  // A virtual hierarchy: file names and dates are metadata, never
  // modifications to the physical SSD directory.
  async function refreshLibrary(){
    const data=await api('/api/library');
    state.library=data.items;
    $('libraryCount').textContent=data.total+' recordings';
    if(state.active){
      const match=state.library.find(x=>x.assetId===state.active.assetId);
      if(match)state.active=match;
    }
    renderTree();
  }
  function groupName(record){
    switch($('libraryGroup').value){
      case 'day':return record.day||'Undated';
      case 'region':return record.region||'Unassigned region';
      case 'activity':return human(record.activity)+(record.activityReviewed?' · corrected':' · indexed');
      case 'folder':return record.sourceFolder||'Unknown folder';
      case 'collection':return record.collection||'Uncategorized';
    }
    return 'Other';
  }
  function renderTree(){
    const term=$('librarySearch').value.trim().toLowerCase();
    const items=state.library.filter(record=>
      !term||[record.title,record.day,record.activity,record.region,record.place,
        record.collection,record.sourceFolder,record.note].some(x=>
        String(x||'').toLowerCase().includes(term)));
    const groups=new Map();
    for(const record of items){
      const group=groupName(record);
      if(!groups.has(group))groups.set(group,[]);
      groups.get(group).push(record);
    }
    const entries=[...groups].sort((a,b)=>$('libraryGroup').value==='day'?
      b[0].localeCompare(a[0]):a[0].localeCompare(b[0]));
    const tree=$('libraryTree');tree.replaceChildren();
    $('treeCount').textContent='('+items.length+')';
    if(!entries.length)tree.append(elem('p','secondary-copy','No matching recordings.'));
    for(const [group,records] of entries){
      const folder=elem('details','tree-group');
      folder.open=entries.length<4;
      const head=elem('summary');head.append(elem('span','',group),
        elem('span','group-count',String(records.length)));
      folder.append(head);
      records.sort((a,b)=>a.title.localeCompare(b.title));
      for(const record of records){
        const b=elem('button','tree-file'+
          (state.active?.assetId===record.assetId?' active':''),record.title);
        b.type='button';b.title=record.title;
        b.onclick=()=>openRecord(record).catch(fail);
        folder.append(b);
      }
      tree.append(folder);
    }
  }
  async function openRecord(record){
    state.active=record;renderTree();
    $('libraryPlaceholder').classList.add('hidden');$('libraryRecord').classList.remove('hidden');
    $('recordName').textContent=record.title;
    const details=[record.day||'Unknown date',human(record.activity)+
      (record.activityReviewed?' · corrected by you':' · indexed, unverified'),
      record.region,record.place,mm(record.durationMs)];
    $('recordMetadata').textContent=details.filter(Boolean).join(' · ')+
      (record.dayProvenance==='filename_estimate'?' · date from filename':'');
    const v=$('libraryPlayer');v.pause();v.src=media(record.assetId);v.load();
    state.detail=await api('/api/asset/'+record.assetId);
    state.tab='reviews';
    renderRecordMoments();$('organizeForm').classList.add('hidden');
  }
  function renderRecordMoments(){
    const pane=$('recordMoments');pane.replaceChildren();
    $('recordClipsTab').classList.toggle('subtab-current',state.tab==='reviews');
    $('recordSuggestedTab').classList.toggle('subtab-current',state.tab==='suggestions');
    const rows=(state.tab==='reviews'?state.detail?.reviews:state.detail?.suggestions)||[];
    if(!rows.length)pane.append(elem('p','secondary-copy','No moments in this view yet.'));
    for(const r of rows.slice(0,180)){
      const b=elem('button','record-moment');
      b.append(elem('span','',human(r.label||r.kind)),
        elem('small','',mm(r.startMs)+' – '+mm(r.endMs)+
          (r.highlight?' · '+r.highlight:' · proposed')));
      b.onclick=()=>{
        $('libraryPlayer').currentTime=r.startMs/1000;
        $('libraryPlayer').play().catch(()=>toast('Try Safari if the DJI codec cannot play.',true));
      };
      pane.append(b);
    }
  }
  function openOrganization(){
    const r=state.active;if(!r)return;
    $('organizeRegion').value=r.region||'';$('organizePlace').value=r.place||'';
    $('organizeCollection').value=r.collection||'';
    $('organizeActivity').value=r.activityReviewed?r.activity:'';
    $('organizeNote').value=r.note||'';
    $('organizeForm').classList.remove('hidden');
  }
  async function saveOrganization(e){
    e.preventDefault();if(!state.active)return;
    try{
      const assetId=state.active.assetId;
      await post('/api/library/organize',{assetId,
        region:$('organizeRegion').value.trim(),place:$('organizePlace').value.trim(),
        collection:$('organizeCollection').value.trim(),
        activity:$('organizeActivity').value,note:$('organizeNote').value.trim()});
      await refreshLibrary();
      const current=state.library.find(x=>x.assetId===assetId);
      await openRecord(current);
      toast('Library labels saved. Original video unchanged.');
    }catch(err){fail(err);}
  }
  function bind(){
    document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>view(b.dataset.view));
    $('previousClip').onclick=previous;$('nextClip').onclick=next;
    $('cinemaToggle').onclick=()=>cinema(!document.body.classList.contains('cinema-active'));
    $('replayClip').onclick=replay;
    $('shortcutsButton').onclick=()=>$('shortcutsDialog').showModal();
    $('closeShortcuts').onclick=()=>$('shortcutsDialog').close();
    $('nextBatch').onclick=()=>{state.page++;loadQueue().catch(fail);};
    $('activityFilter').onchange=()=>{state.activity=$('activityFilter').value;state.page=0;loadQueue().catch(fail);};
    $('keepClip').onclick=()=>decide('keep');$('skipClip').onclick=()=>decide('skip');
    $('wrongClip').onclick=()=>decide('wrong');
    $('feedbackForm').onsubmit=submitFeedback;
    $('cancelFeedback').onclick=$('backFeedback').onclick=()=>$('feedbackForm').classList.add('hidden');
    $('detailsToggle').onclick=()=>{
      const show=$('detailsPanel').classList.toggle('hidden')===false;
      $('detailsToggle').setAttribute('aria-expanded',String(show));
      if(show)loadClipMetadata();
    };
    $('reviewPlayer').ontimeupdate=()=>{
      if(!state.current)return;
      const end=Number($('clipOut').value);
      if(end>0&&$('reviewPlayer').currentTime>=end-.06)$('reviewPlayer').pause();
    };
    $('viewOriginal').onclick=async()=>{
      if(!state.current)return;
      const moment=state.current;
      view('library');try{
        await refreshLibrary();
        const record=state.library.find(x=>x.assetId===moment.assetId);
        if(record){
          await openRecord(record);
          $('libraryPlayer').currentTime=moment.startMs/1000;
        }
      }catch(e){fail(e);}
    };
    $('libraryGroup').onchange=renderTree;$('librarySearch').oninput=renderTree;
    $('recordClipsTab').onclick=()=>{state.tab='reviews';renderRecordMoments();};
    $('recordSuggestedTab').onclick=()=>{state.tab='suggestions';renderRecordMoments();};
    $('recordOrganize').onclick=openOrganization;
    $('organizeForm').onsubmit=saveOrganization;
    $('closeOrganize').onclick=()=>$('organizeForm').classList.add('hidden');
    window.addEventListener('keydown',e=>{
      if(state.view!=='review'||document.querySelector('dialog[open]')||
        document.activeElement?.matches('input,select,textarea,button'))return;
      if(e.key==='1'){e.preventDefault();decide('keep');}
      if(e.key==='2'){e.preventDefault();decide('skip');}
      if(e.key==='3'){e.preventDefault();decide('wrong');}
      if(e.key.toLowerCase()==='f'){e.preventDefault();cinema(!document.body.classList.contains('cinema-active'));}
      if(e.key.toLowerCase()==='r'){e.preventDefault();replay();}
      if(e.key==='Escape'&&document.body.classList.contains('cinema-active')){e.preventDefault();cinema(false);}
      if(e.key==='ArrowRight'){e.preventDefault();next();}
      if(e.key==='ArrowLeft'){e.preventDefault();previous();}
      if(e.code==='Space'){
        e.preventDefault();const v=$('reviewPlayer');
        if(v.paused)v.play().catch(fail);else v.pause();
      }
    });
  }
  window.KlipFocus=Object.freeze({api,post,media,toast,fail,view});
  async function start(){
    bind();await Promise.all([totals(),loadQueue()]);
    window.dispatchEvent(new Event('klip-focus-ready'));
  }
  start().catch(fail);
})();
'use strict';
(function () {
  const el = id => document.getElementById(id);
  const token = document.querySelector('meta[name="klip-session"]').content;
  const state = {assets:[],details:new Map(),asset:null,selected:null,selectedIndex:-1,
    filter:'all',sort:'quality',decision:'confirmed',highlight:'neutral',mode:'review',
    vote:null,range:null};
  const fmt = raw => {let v=Math.max(0,Math.floor(Number(raw)||0));return String(Math.floor(v/60)).padStart(2,'0')+':'+String(v%60).padStart(2,'0');};
  const sec = ms => Math.round(ms/100)/10;
  const human = value => String(value||'moment').replace(/_/g,' ').replace(/\b\w/g,x=>x.toUpperCase());
  const short = name => String(name).replace(/\.[^.]+$/,'').replace(/^DJI[_-]/,'').replace(/_/g,' ');
  const safe = value => String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const media = id => '/api/media/'+id+'?token='+encodeURIComponent(token);
  const activityName = a => ({ski:'Skiing',snorkeling:'Snorkeling',bike:'Biking',unknown:'Uncategorized'}[a]||a);
  const categories = {
    ski:['skiing','ski_lift','slow_flat_skiing','waiting','carving_flow','jump','tree_weave','good_run','smooth_flow','crash','fall','near_miss','unstable_landing','camera_adjustment','non_action'],
    bike:['riding','bike_stopped','easy_traverse','berm','rock_garden','jump','crash','near_miss','smooth_flow','non_action'],
    snorkeling:['submerged','underwater_stable','surface_break','surface_transition','animal_presence','wildlife_encounter','underwater_encounter','camera_adjustment','non_action'],
    unknown:['good_run','smooth_flow','non_action','camera_adjustment','wildlife_encounter','crash']
  };
  let timer;
  function toast(message,fail=false) {
    const node=el('toast');node.textContent=message;node.hidden=false;
    node.classList.toggle('error',fail);clearTimeout(timer);
    timer=setTimeout(()=>{node.hidden=true;},3900);
  }
  async function api(path,options={}) {
    const response=await fetch(path,Object.assign({},options,{cache:'no-store',
      headers:Object.assign({'X-Klip-Token':token},options.headers||{})}));
    const body=await response.json();
    if(!response.ok)throw Error(body.error||'Request failed');
    return body;
  }
  const post=(path,payload)=>api(path,{method:'POST',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const currentDetail=()=>state.details.get(state.asset?.id);
  function stats(data) {
    el('libraryCount').textContent=data.assets.length;
    el('reviewedCount').textContent=data.reviewed;
    el('comparisonCount').textContent=data.comparisons;
    el('conflictCount').textContent=data.conflicts.length;
    el('topProgress').textContent=data.reviewed+' reviewed';
  }
  function library() {
    const area=el('library');area.replaceChildren();
    const rows=state.assets.filter(a=>state.filter==='all'||a.activity===state.filter);
    if(!rows.length){const p=document.createElement('p');p.className='muted-paragraph';p.textContent='No recordings in this view.';area.append(p);}
    for(const a of rows) {
      const b=document.createElement('button');b.type='button';
      b.className='library-row'+(state.asset?.id===a.id?' selected':'');
      b.title=a.title;
      const completed=Math.min(100,100*a.reviews/Math.max(1,a.proposals));
      b.innerHTML='<div class="label-top"><span class="file-name">'+safe(short(a.title))+'</span>'+
        (a.reviews?'<span class="rec-marker">✓</span>':'')+'</div>'+
        '<div class="meta"><span>'+safe(activityName(a.activity))+'</span><span>'+fmt(a.duration)+
        '</span><span>'+a.proposals+' cues</span></div>'+
        '<div class="thumb-line"><span style="width:'+completed+'%"></span></div>';
      b.addEventListener('click',()=>openAsset(a.id));area.append(b);
    }
  }
  async function loadDetail(id) {
    if(!state.details.has(id))state.details.set(id,await api('/api/asset/'+id));
    return state.details.get(id);
  }
  function labels(activity) {
    const node=el('reviewLabel');node.replaceChildren();
    const start=document.createElement('option');start.value='';start.textContent='Choose the action or scene';node.append(start);
    for(const name of categories[activity]||categories.unknown) {
      const opt=document.createElement('option');opt.value=name;opt.textContent=human(name);node.append(opt);
    }
    node.value='';
  }
  async function openAsset(id) {
    const asset=state.assets.find(a=>a.id===id);if(!asset)return;
    state.asset=asset;state.selected=null;state.selectedIndex=-1;
    el('clipName').textContent=asset.title;
    el('activityTag').textContent=asset.activity==='snorkeling'?'WATER':asset.activity.toUpperCase();
    el('durationLabel').textContent=fmt(asset.duration);
    el('timelineDuration').textContent=fmt(asset.duration);
    el('rulerEnd').textContent=fmt(asset.duration);
    el('rulerHalf').textContent=fmt(asset.duration/2);
    el('viewerEmpty').classList.add('hidden');
    const player=el('player');player.pause();player.src=media(asset.id);player.load();
    el('playbackStatus').textContent='Opening indexed recording…';
    el('saveReview').disabled=true;el('playSelected').disabled=true;
    el('momentTitle').textContent='Finding moments…';
    labels(asset.activity);library();
    try{
      await loadDetail(id);renderCurrent();
      if(currentDetail().suggestions.length)selectMoment(0);
      else selectRange(0,Math.min(15,asset.duration),'');
    }catch(error){toast(error.message,true);el('momentTitle').textContent='Analysis unavailable';}
  }
  function ordered() {
    const items=currentDetail()?.suggestions||[];
    return state.sort==='time'?[...items].sort((a,b)=>a.startMs-b.startMs):
      [...items].sort((a,b)=>b.score-a.score||a.startMs-b.startMs);
  }
  function renderCurrent() {
    if(!currentDetail())return;
    const moments=ordered(),area=el('moments');area.replaceChildren();
    el('momentCount').textContent=moments.length;
    if(!moments.length){
      const p=document.createElement('p');p.className='muted-paragraph';
      p.textContent='No proposals yet. Select your own in and out times in the Inspector.';
      area.append(p);
    }
    moments.forEach((m,i)=>{
      const b=document.createElement('button');b.type='button';
      b.className='moment'+(i===state.selectedIndex?' active':'');
      b.innerHTML='<div class="moment-header"><span class="moment-number">'+
        String(i+1).padStart(2,'0')+'</span><span class="moment-quality">editorial '+m.score.toFixed(2)+
        ' · heuristic</span></div><strong class="moment-title">'+safe(human(m.kind))+
        '</strong><div class="moment-foot"><span>'+fmt(m.startMs/1000)+' – '+
        fmt(m.endMs/1000)+'</span><span>'+
        (m.reasonCodes?.includes('camera_disturbance_requires_review')?'⚑ Review':'↗ Inspect')+'</span></div>';
      b.addEventListener('click',()=>selectMoment(i));
      area.append(b);
    });
    const d=currentDetail();
    timeline(moments,[...d.diagnostics.contextStates,...d.diagnostics.cameraDisturbances]);
    history(d.reviews);
  }
  function timeline(moments,cues) {
    const duration=Math.max(1,state.asset?.duration*1000||1);
    for(const id of ['suggestionTrack','contextTrack'])el(id).replaceChildren();
    const add=(id,c,i,warning)=>{
      const parent=el(id),start=100*Math.max(0,c.startMs)/duration;
      const end=100*Math.min(duration,c.endMs)/duration;
      const btn=document.createElement('button');
      btn.type='button';btn.className='track-segment'+(warning?' warning':'');
      btn.style.left=start+'%';btn.style.width=Math.max(.2,end-start)+'%';
      btn.title=human(c.kind||c.label)+' '+fmt(c.startMs/1000)+'–'+fmt(c.endMs/1000);
      btn.setAttribute('aria-label',btn.title);
      btn.addEventListener('click',event=>{
        event.stopPropagation();
        if(warning)selectRange(c.startMs/1000,c.endMs/1000,c.label);
        else selectMoment(i);
      });
      parent.append(btn);
    };
    moments.forEach((m,i)=>add('suggestionTrack',m,i,false));
    cues.slice(0,150).forEach((m,i)=>add('contextTrack',m,i,true));
    for(const id of ['suggestionTrack','contextTrack']){
      el(id).onclick=event=>{
        const rect=event.currentTarget.getBoundingClientRect();
        const x=(event.clientX-rect.left)/Math.max(1,rect.width);
        if(state.asset)el('player').currentTime=Math.max(0,Math.min(1,x))*state.asset.duration;
      };
    }
  }
  function selectMoment(index) {
    const list=ordered();if(!list.length)return;
    const i=Math.min(list.length-1,Math.max(0,index));const m=list[i];
    state.selectedIndex=i;state.selected=m;
    selectRange(sec(m.startMs),sec(m.endMs),m.kind,true);
    el('evidenceText').textContent=m.reasonCodes?.length?
      m.reasonCodes.map(human).join(' · '):
      'Motion-derived proposal. Inspect the original video before confirming.';
    pills(['Editorial '+m.score.toFixed(2),'Motion is unverified',
      ...(m.reasonCodes||[]).slice(0,4).map(human)]);
    el('player').currentTime=sec(m.startMs);
    renderCurrent();
  }
  function selectRange(start,end,label='',preserveSelection=false){
    if(!preserveSelection){state.selectedIndex=-1;state.selected=null;}
    const duration=state.asset?.duration||0;
    start=Math.max(0,Math.min(duration,Number(start)||0));
    end=Math.max(start,Math.min(duration,Number(end)||0));
    state.range={start,end};
    el('reviewStart').value=start.toFixed(1);
    el('reviewEnd').value=end.toFixed(1);
    el('focusIn').textContent=fmt(start);el('focusOut').textContent=fmt(end);
    const node=el('reviewLabel');
    node.value=Array.from(node.options).some(o=>o.value===label)?label:'';
    el('saveReview').disabled=!state.asset;
    el('playSelected').disabled=!state.asset||end<=start;
    if(!preserveSelection){
      el('momentTitle').textContent=human(label||'Your selection');
      el('evidenceText').textContent='Your selection from the original video. The decision belongs to you.';
      pills(['Manually selected','Human review required']);renderCurrent();
    }else{
      el('momentTitle').textContent=human(label);
    }
  }
  function pills(items) {
    const pane=el('evidencePills');pane.replaceChildren();
    for(const item of items) {
      const p=document.createElement('span');p.className='evidence-pill'+
        (/unverified|review/i.test(item)?' uncertain':'');p.textContent=item;pane.append(p);
    }
  }
  function history(rows) {
    const pane=el('reviewHistory');pane.replaceChildren();
    if(!rows.length){const p=document.createElement('p');p.className='muted-paragraph';
      p.textContent='No reviews yet. Start by selecting a moment.';pane.append(p);return;}
    for(const item of [...rows].sort((a,b)=>b.startMs-a.startMs).slice(0,8)) {
      const b=document.createElement('button');b.type='button';b.className='history-item';b.style.textAlign='left';
      b.innerHTML='<strong>'+safe(human(item.label))+'</strong><span>'+
        fmt(item.startMs/1000)+'–'+fmt(item.endMs/1000)+' · '+
        safe(item.decision)+' · '+safe(item.highlight)+' · v'+item.revisionCount+'</span>';
      b.addEventListener('click',()=>{
        selectRange(item.startMs/1000,item.endMs/1000,item.label);
        state.decision=item.decision;state.highlight=item.highlight;
        syncOptions();el('reviewNote').value=item.note||'';
        el('player').currentTime=item.startMs/1000;
      });
      pane.append(b);
    }
  }
  function syncOptions() {
    for(const key of ['decision','highlight']) {
      document.querySelectorAll('[data-'+key+']').forEach(b=>{
        const yes=b.dataset[key]===state[key];
        b.classList.toggle('selected',yes);b.setAttribute('aria-pressed',String(yes));
      });
    }
  }
  async function saveReview(event){
    event.preventDefault();if(!state.asset)return;
    if(!el('reviewLabel').value){toast('Choose a scene label before saving.',true);return;}
    const btn=el('saveReview');btn.disabled=true;
    try {
      const result=await post('/api/reviews',{assetId:state.asset.id,
        startSeconds:Number(el('reviewStart').value),
        endSeconds:Number(el('reviewEnd').value),label:el('reviewLabel').value,
        decision:state.decision,highlight:state.highlight,note:el('reviewNote').value.trim()});
      state.details.delete(state.asset.id);
      await loadDetail(state.asset.id);
      const overview=await api('/api/overview');state.assets=overview.assets;
      stats(overview);library();renderCurrent();
      toast(result.status==='unchanged'?'Already saved.':'Review saved. Klip will remember this moment.');
    }catch(error){toast(error.message,true);}
    finally{btn.disabled=false;}
  }
  async function mode(next){
    state.mode=next;
    el('reviewWorkspace').classList.toggle('hidden',next!=='review');
    el('compareWorkspace').classList.toggle('hidden',next!=='compare');
    el('reviewMode').classList.toggle('active',next==='review');
    el('compareMode').classList.toggle('active',next==='compare');
    el('reviewMode').setAttribute('aria-selected',String(next==='review'));
    el('compareMode').setAttribute('aria-selected',String(next==='compare'));
    if(next==='compare'){el('player').pause();await initCompare();}
    else{el('videoLeft').pause();el('videoRight').pause();}
  }
  function fillAssetSelect(id,value){
    const node=el(id);node.replaceChildren();
    for(const a of state.assets){
      const opt=document.createElement('option');opt.value=a.id;
      opt.textContent=short(a.title)+' · '+activityName(a.activity);node.append(opt);
    }
    node.value=value;
  }
  async function initCompare(){
    if(!state.assets.length)return;
    const left=el('compareLeftAsset').value||state.asset?.id||state.assets[0].id;
    const right=el('compareRightAsset').value||state.assets.find(x=>x.id!==left)?.id||left;
    fillAssetSelect('compareLeftAsset',left);fillAssetSelect('compareRightAsset',right);
    await Promise.all([loadSide('Left'),loadSide('Right')]);
  }
  async function loadSide(side){
    const asset=el('compare'+side+'Asset').value;
    const data=await loadDetail(asset);
    const items=[...data.suggestions].sort((a,b)=>a.startMs-b.startMs).slice(0,70);
    const select=el('compare'+side+'Clip');select.replaceChildren();
    if(!items.length){
      const option=document.createElement('option');option.value='manual';
      option.textContent='Whole recording';select.append(option);
    }
    items.forEach((item,index)=>{
      const option=document.createElement('option');option.value=String(index);
      option.textContent=fmt(item.startMs/1000)+'–'+fmt(item.endMs/1000)+' · '+human(item.kind);
      select.append(option);
    });
    select.dataset.windows=JSON.stringify(items);
    const video=el(side==='Left'?'videoLeft':'videoRight');
    video.pause();video.src=media(asset);video.load();
    if(side==='Right'&&items.length>1)select.selectedIndex=1;
    setSide(side);
  }
  function compareRange(side){
    const aid=el('compare'+side+'Asset').value;
    const options=JSON.parse(el('compare'+side+'Clip').dataset.windows||'[]');
    const item=options[Number(el('compare'+side+'Clip').value)];
    const asset=state.assets.find(x=>x.id===aid);
    return {assetId:aid,start:item?item.startMs/1000:0,
      end:item?item.endMs/1000:(asset?.duration||0)};
  }
  function setSide(side){
    const segment=compareRange(side),video=el(side==='Left'?'videoLeft':'videoRight');
    el(side==='Left'?'leftRange':'rightRange').textContent=fmt(segment.start)+'–'+fmt(segment.end)+
      ' · '+Math.round(segment.end-segment.start)+' sec';
    if(video.readyState>=1)video.currentTime=segment.start;
    else video.addEventListener('loadedmetadata',()=>{video.currentTime=segment.start;},{once:true});
  }
  async function saveCompare(){
    if(!state.vote){toast('Choose which clip you prefer.',true);return;}
    const left=compareRange('Left'),right=compareRange('Right'),btn=el('saveComparison');
    btn.disabled=true;
    try{
      const result=await post('/api/compare',{leftAssetId:left.assetId,
        leftStart:left.start,leftEnd:left.end,rightAssetId:right.assetId,
        rightStart:right.start,rightEnd:right.end,preference:state.vote,
        reason:el('compareReason').value,note:el('compareNote').value.trim()});
      const overview=await api('/api/overview');state.assets=overview.assets;
      stats(overview);library();
      toast(result.status==='unchanged'?'Already saved.':'Your A/B preference was saved.');
    }catch(error){toast(error.message,true);}
    finally{btn.disabled=false;}
  }
  function syncPlayback(){
    const video=el('player'),duration=state.asset?.duration||0,pos=video.currentTime||0;
    el('currentTime').textContent=fmt(pos);
    el('seekSlider').value=duration?Math.min(1000,Math.round(pos/duration*1000)):0;
    el('playhead').style.left=(duration?Math.min(100,pos/duration*100):0)+'%';
    if(video.dataset.playSelection==='yes'&&state.range&&pos>=state.range.end){
      video.pause();delete video.dataset.playSelection;
    }
  }
  function bind(){
    document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{
      state.filter=b.dataset.filter;
      document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('is-active',x.dataset.filter===state.filter));
      library();
    }));
    el('reviewMode').addEventListener('click',()=>mode('review'));
    el('compareMode').addEventListener('click',()=>mode('compare').catch(e=>toast(e.message,true)));
    el('sortMoments').addEventListener('change',()=>{
      state.sort=el('sortMoments').value;state.selectedIndex=-1;renderCurrent();
    });
    el('reviewForm').addEventListener('submit',saveReview);
    for(const name of ['decision','highlight']){
      document.querySelectorAll('[data-'+name+']').forEach(b=>b.addEventListener('click',()=>{
        state[name]=b.dataset[name];syncOptions();
      }));
    }
    for(const id of ['reviewStart','reviewEnd']){
      el(id).addEventListener('change',()=>{
        if(!state.asset)return;
        const a=Math.max(0,Math.min(state.asset.duration,Number(el('reviewStart').value)||0));
        const b=Math.max(a,Math.min(state.asset.duration,Number(el('reviewEnd').value)||0));
        state.range={start:a,end:b};el('focusIn').textContent=fmt(a);el('focusOut').textContent=fmt(b);
      });
    }
    el('playSelected').addEventListener('click',()=>{
      if(!state.range)return;
      const video=el('player');video.dataset.playSelection='yes';video.currentTime=state.range.start;
      video.play().catch(()=>toast('Browser cannot play this DJI codec. Try Safari.',true));
    });
    const player=el('player');
    player.addEventListener('timeupdate',syncPlayback);
    player.addEventListener('loadedmetadata',()=>{el('playbackStatus').textContent='Streaming original recording · no copies';});
    player.addEventListener('error',()=>{el('playbackStatus').textContent='Playback unavailable. Try Safari for DJI codec support.';});
    el('seekSlider').addEventListener('input',()=>{
      if(state.asset)player.currentTime=state.asset.duration*Number(el('seekSlider').value)/1000;
    });
    for(const side of ['Left','Right']){
      el('compare'+side+'Asset').addEventListener('change',()=>loadSide(side).catch(e=>toast(e.message,true)));
      el('compare'+side+'Clip').addEventListener('change',()=>setSide(side));
      el(side==='Left'?'videoLeft':'videoRight').addEventListener('timeupdate',event=>{
        const segment=compareRange(side);
        if(event.target.currentTime>segment.end+.1)event.target.pause();
      });
    }
    document.querySelectorAll('[data-vote]').forEach(b=>b.addEventListener('click',()=>{
      state.vote=b.dataset.vote;
      document.querySelectorAll('[data-vote]').forEach(x=>x.classList.toggle('active',x.dataset.vote===state.vote));
      el('saveComparison').disabled=false;
    }));
    el('saveComparison').addEventListener('click',saveCompare);
    el('helpBtn').addEventListener('click',()=>el('helpDialog').showModal());
    el('closeHelp').addEventListener('click',()=>el('helpDialog').close());
    document.addEventListener('keydown',event=>{
      if(el('helpDialog').open||state.mode!=='review')return;
      const target=event.target;
      if(target.matches('input,textarea,select')||target.isContentEditable)return;
      const video=el('player');
      if(event.code==='Space'){event.preventDefault();video.paused?video.play():video.pause();}
      if(event.key==='ArrowLeft'){event.preventDefault();video.currentTime=Math.max(0,video.currentTime-3);}
      if(event.key==='ArrowRight'){event.preventDefault();video.currentTime=Math.min(state.asset?.duration||0,video.currentTime+3);}
      if(event.key==='['){el('reviewStart').value=video.currentTime.toFixed(1);el('reviewStart').dispatchEvent(new Event('change'));}
      if(event.key===']'){el('reviewEnd').value=video.currentTime.toFixed(1);el('reviewEnd').dispatchEvent(new Event('change'));}
      if(event.key.toLowerCase()==='j'&&ordered().length)selectMoment(Math.max(0,state.selectedIndex-1));
      if(event.key.toLowerCase()==='k'&&ordered().length)selectMoment(state.selectedIndex+1);
    });
  }
  async function start(){
    bind();syncOptions();
    try {
      const data=await api('/api/overview');state.assets=data.assets;stats(data);library();
      if(data.assets.length)await openAsset(data.assets[0].id);
      else toast('Library is empty. Index DJI videos from your Terminal first.');
    }catch(error){toast('Could not open studio: '+error.message,true);}
  }
  start();
})();

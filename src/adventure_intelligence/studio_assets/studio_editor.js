'use strict';
(function(){
const $=id=>document.getElementById(id), host=()=>window.KlipStudio;
const t=ms=>{const v=Math.floor(Math.max(0,Number(ms)||0)/1000);return String(Math.floor(v/60)).padStart(2,'0')+':'+String(v%60).padStart(2,'0');};
const human=s=>String(s||'moment').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
const html=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const state={queue:[],page:0,activity:'all',lane:'all',selected:null,seq:null,order:[],dirty:false,join:null,playing:false,index:0,inlineVideo:null};
function warn(e){host().toast(e.message||String(e),true);}
async function loadQueue(){
  const result=await host().api('/api/queue?activity='+state.activity+'&page='+state.page);
  state.queue=result.moments;
  $('queuePageCount').textContent=result.moments.length;
  $('queueProgress').textContent=result.totalAssets+' recordings · '+result.totalPages+' pages';
  $('queuePrevious').disabled=state.page===0;$('queueNext').disabled=!result.hasNext;
  $('queuePaginationText').textContent='Page '+(state.page+1)+' of '+Math.max(1,result.totalPages);
  drawQueue();
}
function drawQueue(){
  if(state.inlineVideo){state.inlineVideo.pause();state.inlineVideo.removeAttribute('src');state.inlineVideo.load();state.inlineVideo=null;}
  const box=$('queueCards');box.replaceChildren();
  const rows=state.queue.filter(x=>state.lane==='all'||x.lane===state.lane);
  if(!rows.length){const p=document.createElement('p');p.className='queue-empty';p.textContent='Nothing here. Try another priority or the next batch of recordings.';box.append(p);return;}
  rows.forEach((m,i)=>{
    const card=document.createElement('article');card.className='queue-card';
    const kind={promising:'Potential highlight',needs_review:'Needs your eye',likely_filler:'Likely filler'}[m.lane];
    card.innerHTML='<div class="queue-card-head"><span class="queue-lane '+m.lane+'">'+kind+'</span><span>'+String(i+1).padStart(2,'0')+'</span></div>'+
      '<button type="button" class="queue-card-media"><span class="queue-play">▶</span><span class="queue-range">'+t(m.startMs)+' – '+t(m.endMs)+'</span></button>'+
      '<div class="queue-card-details"><h3>'+html(human(m.label))+'</h3><p>'+html(m.sourceName)+'</p><p class="queue-evidence">'+m.reasons.map(html).join(' · ')+'</p>'+
      (m.similarSceneCount?'<p class="queue-similar">◈ '+m.similarSceneCount+' visually similar candidate(s) · unverified</p>':'')+'</div>'+
      '<div class="queue-card-buttons"><button type="button" class="queue-view">Preview</button><button type="button" class="queue-keep">✓ Keep</button><button type="button" class="queue-reject">✕ Reject</button></div>';
    const preview=()=>host().inspect(m.assetId,m.startMs,m.endMs,m.label).catch(warn);
    card.querySelector('.queue-view').addEventListener('click',preview);
    card.querySelector('.queue-card-media').addEventListener('click',()=>{
      if(state.inlineVideo){state.inlineVideo.pause();state.inlineVideo.removeAttribute('src');state.inlineVideo.load();}
      const video=document.createElement('video');
      video.controls=true;video.playsInline=true;video.preload='none';
      video.className='queue-inline-preview';video.setAttribute('aria-label','Review candidate source segment');
      video.src=host().media(m.assetId);
      const surface=card.querySelector('.queue-card-media');
      surface.replaceWith(video);
      state.inlineVideo=video;
      const start=m.startMs/1000,end=m.endMs/1000;
      video.addEventListener('loadedmetadata',()=>{
        video.currentTime=start;
        video.play().catch(()=>host().toast('Try Safari for DJI playback.',true));
      },{once:true});
      video.addEventListener('timeupdate',()=>{
        if(video.currentTime>=end-.06)video.pause();
      });
      video.load();
    });
    card.querySelector('.queue-keep').addEventListener('click',async()=>{
      try{
        await host().post('/api/reviews',{assetId:m.assetId,startSeconds:m.startMs/1000,endSeconds:m.endMs/1000,
          label:'highlight_moment',decision:'confirmed',highlight:'keep',note:'Approved in editorial inbox'});
        host().toast('Approved. This moment is now in the clip shelf.');
        await refreshAfterReview();
      }catch(e){warn(e);}
    });
    card.querySelector('.queue-reject').addEventListener('click',()=>{
      state.selected=m;$('rejectReason').value='boring';$('rejectComment').value='';$('rejectSceneDialog').showModal();
    });
    box.append(card);
  });
}
async function refreshAfterReview(){
  await host().refresh();await loadQueue();if(!state.dirty)await loadSequence();
}
async function reject(event){
  event.preventDefault();const m=state.selected;if(!m)return;
  const why=$('rejectReason').value,note=$('rejectComment').value.trim();
  if(note.length<5){host().toast('Include a brief explanation.',true);return;}
  const mapping={lift:'ski_lift',flat:'slow_flat_skiing',surface:'surface_break',camera:'camera_adjustment',
    boring:'non_action',duplicate:'duplicate_scene',other:'non_action'};
  const wrong=why==='wrong_label';
  try{
    await host().post('/api/reviews',{assetId:m.assetId,startSeconds:m.startMs/1000,endSeconds:m.endMs/1000,
      label:wrong?m.label:mapping[why],decision:wrong?'rejected':'confirmed',
      highlight:wrong?'neutral':'skip',note:why+': '+note});
    $('rejectSceneDialog').close();state.selected=null;
    host().toast(wrong?'Incorrect label recorded.':'Rejected. Your reason is saved.');
    await refreshAfterReview();
  }catch(e){warn(e);}
}
function bank(){return new Map((state.seq?.bank||[]).map(x=>[x.exampleId,x]));}
function get(id){return bank().get(id)||(state.seq?.entries||[]).find(x=>x.exampleId===id);}
async function loadSequence(){
  if(state.dirty)return;
  state.seq=await host().api('/api/sequence');
  state.order=state.seq.entries.map(x=>x.exampleId);
  $('sequenceName').value=state.seq.name;
  drawSequence();
}
function dirty(){
  state.dirty=true;$('sequenceSaveState').textContent='Unsaved changes';drawSequence();
}
function add(id,index){
  if(state.order.includes(id)){host().toast('Already in sequence.',true);return;}
  const x=get(id);
  if(!x){host().toast('Only approved clips can be added.',true);return;}
  if(state.order.length>=40){host().toast('Maximum 40 clips per reel.',true);return;}
  const total=state.order.reduce((s,k)=>{const v=get(k);return s+(v.endMs-v.startMs);},x.endMs-x.startMs);
  if(total>600000){host().toast('Reel cannot exceed 10 minutes.',true);return;}
  state.order.splice(index,0,id);dirty();
}
function move(from,to){
  if(from<0||from>=state.order.length||to<0||to>=state.order.length||from===to)return;
  const id=state.order.splice(from,1)[0];state.order.splice(to,0,id);dirty();
}
function handleDrop(e,index){
  e.preventDefault();e.stopPropagation();
  const data=e.dataTransfer?.getData('text/plain')||'';
  if(data.startsWith('bank:'))add(data.slice(5),index);
  if(data.startsWith('board:')){
    const from=state.order.indexOf(data.slice(6));
    if(from>=0)move(from,Math.min(state.order.length-1,Math.max(0,index-(from<index?1:0))));
  }
}
function draggable(node,key){
  node.draggable=true;
  node.addEventListener('dragstart',e=>{e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',key);node.classList.add('is-dragged');});
  node.addEventListener('dragend',()=>node.classList.remove('is-dragged'));
}
function drawSequence(){
  if(!state.seq)return;
  const shelf=$('sequenceBank'),board=$('sequenceBoard'),available=bank();
  shelf.replaceChildren();board.replaceChildren();
  $('bankCount').textContent=available.size;$('boardCount').textContent=state.order.length;
  $('sequenceDuration').textContent=t(state.order.reduce((sum,id)=>{const x=get(id);return sum+(x?x.endMs-x.startMs:0);},0))+' / 10:00';
  $('saveSequence').disabled=!state.dirty;
  $('exportSequence').disabled=state.dirty||!state.seq.revisionId||!state.order.length;
  $('playSequence').disabled=!state.order.length;
  if(!available.size){const p=document.createElement('p');p.className='muted-paragraph';p.textContent='Keep your first moment in the Inbox to add it to your shelf.';shelf.append(p);}
  for(const m of available.values()){
    const node=document.createElement('div');node.className='shelf-clip';
    node.innerHTML='<div class="shelf-clip-type">'+html(human(m.label))+'</div><strong>'+html(m.source||m.assetId.slice(0,12))+
      '</strong><span class="shelf-clip-time">'+t(m.startMs)+'–'+t(m.endMs)+'</span>'+
      '<div class="shelf-actions"><button type="button" class="shelf-preview">Preview</button><button type="button" class="shelf-add"'+(state.order.includes(m.exampleId)?' disabled':'')+'>+ Add</button></div>';
    draggable(node,'bank:'+m.exampleId);
    node.querySelector('.shelf-preview').addEventListener('click',()=>preview(m));
    node.querySelector('.shelf-add').addEventListener('click',()=>add(m.exampleId,state.order.length));
    shelf.append(node);
  }
  if(!state.order.length){const p=document.createElement('div');p.className='sequence-empty';p.textContent='Drag approved clips here, or use Add.';board.append(p);}
  state.order.forEach((id,i)=>{
    const m=get(id);if(!m)return;
    const node=document.createElement('div');node.className='board-clip';
    node.innerHTML='<div class="board-number">'+String(i+1).padStart(2,'0')+' <span>⠿</span></div>'+
      '<div class="board-description"><strong>'+html(human(m.label||m.kind))+'</strong><span>'+t(m.startMs)+' – '+t(m.endMs)+'</span></div>'+
      '<div class="board-clip-actions"><button type="button" class="preview">▶</button>'+
      '<button type="button" class="left"'+(i===0?' disabled':'')+'>↑</button>'+
      '<button type="button" class="right"'+(i===state.order.length-1?' disabled':'')+'>↓</button>'+
      '<button type="button" class="remove">×</button></div>';
    draggable(node,'board:'+id);
    node.addEventListener('dragover',e=>{e.preventDefault();node.classList.add('drop-target');});
    node.addEventListener('dragleave',()=>node.classList.remove('drop-target'));
    node.addEventListener('drop',e=>{node.classList.remove('drop-target');handleDrop(e,i);});
    node.querySelector('.preview').onclick=()=>preview(m);
    node.querySelector('.left').onclick=()=>move(i,i-1);
    node.querySelector('.right').onclick=()=>move(i,i+1);
    node.querySelector('.remove').onclick=()=>{state.order.splice(i,1);dirty();};
    board.append(node);
    if(i<state.order.length-1){
      const next=state.order[i+1],feedback=(state.seq.feedback||[]).filter(x=>x.left_example_id===id&&x.right_example_id===next).at(-1);
      const join=document.createElement('div');join.className='join-row';
      const caption=feedback?(feedback.verdict==='approve'?'✓ Accepted join':'⚑ Rejected join'):'◇ Review this join';
      join.innerHTML='<span>· · ·</span><button type="button"'+(state.dirty?' disabled':'')+'>'+caption+'</button><span>· · ·</span>';
      join.querySelector('button').onclick=()=>openJoin(i);
      board.append(join);
    }
  });
  board.ondragover=e=>e.preventDefault();
  board.ondrop=e=>{if(e.target===board||e.target.classList.contains('sequence-empty'))handleDrop(e,state.order.length);};
}
function preview(m){
  const v=$('sequencePlayer'),src=host().media(m.assetId||m.sourceAssetId);
  $('sequencePreviewName').textContent=human(m.label||m.kind)+' · '+t(m.startMs)+'–'+t(m.endMs);
  if(v.getAttribute('src')!==src){v.src=src;v.load();}
  v.dataset.endMs=m.endMs;
  const play=()=>{v.currentTime=m.startMs/1000;v.play().catch(()=>host().toast('Try Safari for DJI codec playback.',true));};
  if(v.readyState>=1)play();else v.addEventListener('loadedmetadata',play,{once:true});
}
function advance(){
  if(!state.playing)return;
  state.index++;
  if(state.index>=state.order.length){state.playing=false;$('sequenceNowPlaying').textContent='Preview complete';$('sequencePlayer').pause();}
  else preview(get(state.order[state.index]));
}
async function save(){
  try{
    const result=await host().post('/api/sequence',{entries:state.order.map(exampleId=>({exampleId})),
      expectedRevision:state.seq?.revisionId||null,name:$('sequenceName').value.trim()||'My highlights'});
    state.dirty=false;await loadSequence();$('sequenceSaveState').textContent='Saved · draft only';
    host().toast(result.status==='unchanged'?'Already saved.':'Sequence saved, with revision history.');
  }catch(e){warn(e);}
}
async function exportPlan(){
  if(state.dirty){host().toast('Save draft first.',true);return;}
  try{
    const result=await host().post('/api/sequence/export',{expectedRevision:state.seq.revisionId});
    $('sequenceSaveState').textContent='Plan exported · MP4 not rendered';
    host().toast('Edit plan created from '+result.clips+' reviewed clips. No originals changed.');
  }catch(e){warn(e);}
}
function openJoin(i){
  if(state.dirty){host().toast('Save the sequence before reviewing a join.',true);return;}
  const left=get(state.order[i]),right=get(state.order[i+1]);if(!left||!right)return;
  state.join={left:left.exampleId,right:right.exampleId};
  $('transitionDescription').textContent=human(left.label||left.kind)+' → '+human(right.label||right.kind)+'. Does this transition work?';
  $('transitionReason').value='continuity';$('transitionComment').value='';$('transitionDialog').showModal();
}
async function saveJoin(verdict){
  const pair=state.join;if(!pair)return;
  const note=$('transitionComment').value.trim();
  if(verdict==='reject'&&note.length<5){host().toast('Explain why the join failed.',true);return;}
  try{
    await host().post('/api/sequence/feedback',{sequenceRevision:state.seq.revisionId,
      leftExampleId:pair.left,rightExampleId:pair.right,verdict,
      reason:$('transitionReason').value,note});
    $('transitionDialog').close();state.join=null;
    state.seq=await host().api('/api/sequence');drawSequence();
    host().toast(verdict==='approve'?'Join approved.':'Join rejected, with your feedback saved.');
  }catch(e){warn(e);}
}
function bind(){
  $('queueActivity').onchange=()=>{state.activity=$('queueActivity').value;state.page=0;loadQueue().catch(warn);};
  $('queueLane').onchange=()=>{state.lane=$('queueLane').value;drawQueue();};
  $('queuePrevious').onclick=()=>{state.page=Math.max(0,state.page-1);loadQueue().catch(warn);};
  $('queueNext').onclick=()=>{state.page++;loadQueue().catch(warn);};
  $('rejectSceneForm').onsubmit=reject;
  $('cancelReject').onclick=()=>$('rejectSceneDialog').close();
  $('saveSequence').onclick=save;$('exportSequence').onclick=exportPlan;
  $('playSequence').onclick=()=>{if(!state.order.length)return;state.playing=true;state.index=0;preview(get(state.order[0]));};
  $('sequenceName').oninput=dirty;
  $('sequencePlayer').addEventListener('timeupdate',()=>{
    const v=$('sequencePlayer'),end=Number(v.dataset.endMs)/1000;
    if(end>0&&v.currentTime>=end-.06){v.pause();if(state.playing)advance();}
  });
  $('sequencePlayer').addEventListener('ended',advance);
  $('cancelTransition').onclick=()=>$('transitionDialog').close();
  $('rejectTransition').onclick=()=>saveJoin('reject');
  $('approveTransition').onclick=()=>saveJoin('approve');
  window.addEventListener('klip-review-changed',()=>{
    loadQueue().catch(warn);if(!state.dirty)loadSequence().catch(warn);
  });
}
bind();
window.addEventListener('klip-ready',()=>{
  Promise.all([loadQueue(),loadSequence()]).catch(warn);
});
})();
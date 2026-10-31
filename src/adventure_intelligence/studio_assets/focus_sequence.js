'use strict';
/* Thin UI layer for existing approved-only, revisioned sequence APIs. */
(() => {
  const $=id=>document.getElementById(id);
  const getHost=()=>window.KlipFocus;
  const st={data:null,order:[],dirty:false,join:null,playing:false,index:0,
    selectedSource:null,playerGeneration:0};
  const format=ms=>{const n=Math.floor(Math.max(0,Number(ms)||0)/1000);
    return String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0');};
  const human=x=>String(x||'moment').replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
  const node=(tag,text,cls)=>{const n=document.createElement(tag);if(cls)n.className=cls;
    if(text!==undefined)n.textContent=text;return n;};
  const approved=()=>new Map((st.data?.bank||[]).map(c=>[c.exampleId,c]));
  const clip=id=>approved().get(id)||(st.data?.entries||[]).find(x=>x.exampleId===id);
  async function load(){
    if(st.dirty)return;
    st.data=await getHost().api('/api/sequence');
    st.order=st.data.entries.map(x=>x.exampleId);
    $('reelTitle').value=st.data.name;
    render();
  }
  function markDirty(){
    st.dirty=true;$('draftState').textContent='Unsaved changes';render();
  }
  function add(id,index=st.order.length){
    if(!approved().has(id))return getHost().toast('Only approved Keep moments can be added.',true);
    if(st.order.includes(id))return getHost().toast('Already in this sequence.',true);
    if(st.order.length>=40)return getHost().toast('The edit is limited to 40 clips.',true);
    const duration=[...st.order,id].reduce((sum,k)=>{
      const c=clip(k);return sum+(c.endMs-c.startMs);},0);
    if(duration>600000)return getHost().toast('Edit cannot exceed 10 minutes.',true);
    st.order.splice(index,0,id);markDirty();
  }
  function move(from,to){
    if(from===to||from<0||to<0||from>=st.order.length||to>=st.order.length)return;
    st.order.splice(to,0,st.order.splice(from,1)[0]);markDirty();
  }
  function drag(node,key){
    node.draggable=true;
    node.ondragstart=e=>{
      e.dataTransfer.setData('text/plain',key);
      node.classList.add('is-dragged');
    };
    node.ondragend=()=>node.classList.remove('is-dragged');
  }
  function drop(e,to){
    e.preventDefault();e.stopPropagation();
    const value=e.dataTransfer.getData('text/plain');
    if(value.startsWith('approved:'))add(value.slice(9),to);
    if(value.startsWith('editing:')){
      const from=st.order.indexOf(value.slice(8));
      if(from>=0)move(from,Math.max(0,Math.min(st.order.length-1,to-(from<to?1:0))));
    }
  }
  function preview(c){
    const player=$('editPlayer');
    if(!c)return;
    st.selectedSource=c;
    $('editNowPlaying').textContent=human(c.label||c.kind)+' · '+format(c.startMs)+'–'+format(c.endMs);
    const source=getHost().media(c.assetId||c.sourceAssetId);
    const generation=++st.playerGeneration;
    const seek=()=>{
      if(generation!==st.playerGeneration)return;
      player.currentTime=c.startMs/1000;
      player.play().catch(()=>getHost().toast('Try Safari to play DJI footage.',true));
    };
    player.dataset.endMs=c.endMs;
    if(player.getAttribute('src')!==source){
      player.src=source;player.load();player.onloadedmetadata=seek;
    }else if(player.readyState>0)seek();
    else player.onloadedmetadata=seek;
  }
  function nextInPreview(){
    if(!st.playing)return;
    st.index++;
    if(st.index>=st.order.length){
      st.playing=false;$('editNowPlaying').textContent='Sequence preview finished';
      $('editPlayer').pause();return;
    }
    preview(clip(st.order[st.index]));
  }
  function render(){
    if(!st.data)return;
    const shelf=$('approvedShelf'),timeline=$('editTimeline');
    shelf.replaceChildren();timeline.replaceChildren();
    const bank=approved();
    $('shelfCount').textContent='('+bank.size+')';
    $('sequenceNumber').textContent=st.order.length;
    $('saveDraft').disabled=!st.dirty;
    $('exportDraft').disabled=st.dirty||!st.data.revisionId||!st.order.length;
    $('playReel').disabled=!st.order.length;
    const duration=st.order.reduce((sum,id)=>{
      const c=clip(id);return sum+(c?c.endMs-c.startMs:0);},0);
    $('editDuration').textContent=format(duration)+' / 10:00';
    const term=$('shelfSearch').value.trim().toLowerCase();
    let shown=0;
    for(const c of bank.values()){
      if(term&&![c.label,c.source,c.note].some(v=>String(v||'').toLowerCase().includes(term)))continue;
      if(shown>=80)break;
      shown++;
      const card=node('div',undefined,'approved-clip');
      card.append(node('strong',human(c.label)),node('small',
        (c.source||c.assetId.slice(0,12))+' · '+format(c.startMs)+'–'+format(c.endMs)));
      const b=node('button',st.order.includes(c.exampleId)?'Already added':'+ Add');
      b.disabled=st.order.includes(c.exampleId);b.onclick=()=>add(c.exampleId);
      card.append(b);drag(card,'approved:'+c.exampleId);shelf.append(card);
    }
    if(!shown)shelf.append(node('p','No approved clips match yet.','secondary-copy'));
    if(bank.size>shown)shelf.append(node('p','Showing '+shown+' of '+bank.size+'. Refine search for more.','secondary-copy'));
    if(!st.order.length)timeline.append(node('p','Drag approved clips here or use Add.','secondary-copy'));
    st.order.forEach((id,i)=>{
      const c=clip(id);if(!c)return;
      const row=node('div',undefined,'edit-clip');
      const desc=node('div');
      desc.append(node('strong',String(i+1).padStart(2,'0')+' · '+human(c.kind||c.label)),
        node('small',format(c.startMs)+' – '+format(c.endMs)));
      row.append(desc);
      const controls=node('div',undefined,'edit-clip-actions');
      for(const [symbol,description,fn,disabled] of [
        ['▶','Preview clip',()=>preview(c),false],
        ['↑','Move earlier',()=>move(i,i-1),i===0],
        ['↓','Move later',()=>move(i,i+1),i===st.order.length-1],
        ['×','Remove from draft',()=>{st.order.splice(i,1);markDirty();},false]]){
        const b=node('button',symbol);b.type='button';b.title=description;
        b.setAttribute('aria-label',description);b.disabled=disabled;b.onclick=fn;
        controls.append(b);
      }
      row.append(controls);
      drag(row,'editing:'+id);
      row.ondragover=e=>{e.preventDefault();row.classList.add('drop-target');};
      row.ondragleave=()=>row.classList.remove('drop-target');
      row.ondrop=e=>{row.classList.remove('drop-target');drop(e,i);};
      timeline.append(row);
      if(i+1<st.order.length){
        const next=st.order[i+1];
        const review=(st.data.feedback||[]).filter(f=>
          f.left_example_id===id&&f.right_example_id===next).at(-1);
        const join=node('div',undefined,'edit-join');
        const button=node('button',review?
          (review.verdict==='approve'?'✓ Approved cut':'⚑ Rejected cut'):'◇ Review this cut');
        button.disabled=st.dirty;
        button.onclick=()=>openJoin(i);
        join.append(button);timeline.append(join);
      }
    });
    timeline.ondragover=e=>e.preventDefault();
    timeline.ondrop=e=>{
      if(e.target===timeline||e.target.tagName==='P')drop(e,st.order.length);
    };
  }
  async function save(){
    try{
      const d=await getHost().post('/api/sequence',{
        entries:st.order.map(exampleId=>({exampleId})),
        expectedRevision:st.data?.revisionId||null,
        name:$('reelTitle').value.trim()||'My highlights'});
      st.dirty=false;await load();
      $('draftState').textContent='Saved to workspace';
      getHost().toast(d.status==='unchanged'?'Draft already saved.':'Revisioned draft saved.');
    }catch(e){getHost().fail(e);}
  }
  async function exportPlan(){
    try{
      const d=await getHost().post('/api/sequence/export',{
        expectedRevision:st.data.revisionId});
      getHost().toast('Edit plan exported for '+d.clips+' clips. No video rendered.');
    }catch(e){getHost().fail(e);}
  }
  function openJoin(index){
    if(st.dirty)return getHost().toast('Save the draft before reviewing a cut.',true);
    const left=clip(st.order[index]),right=clip(st.order[index+1]);
    if(!left||!right)return;
    st.join={left:left.exampleId,right:right.exampleId};
    const warning=(st.data.joinHints||[]).find(h=>
      h.leftExampleId===left.exampleId&&h.rightExampleId===right.exampleId);
    $('joinDescription').textContent=human(left.label||left.kind)+' → '+
      human(right.label||right.kind)+'. '+(warning?warning.hints.join(' · '):'Review the cut.');
    $('joinNote').value='';$('joinDialog').showModal();
  }
  async function judgeJoin(verdict){
    if(!st.join)return;
    const note=$('joinNote').value.trim();
    if(verdict==='reject'&&note.length<5)
      return getHost().toast('Explain what did not work between these scenes.',true);
    try{
      await getHost().post('/api/sequence/feedback',{
        sequenceRevision:st.data.revisionId,leftExampleId:st.join.left,
        rightExampleId:st.join.right,verdict,
        reason:$('joinReason').value,note});
      $('joinDialog').close();
      st.data=await getHost().api('/api/sequence');render();
      getHost().toast('Your transition judgment was saved.');
    }catch(e){getHost().fail(e);}
  }
  function bind(){
    $('reelTitle').oninput=markDirty;
    $('shelfSearch').oninput=render;
    $('saveDraft').onclick=save;
    $('exportDraft').onclick=exportPlan;
    $('playReel').onclick=()=>{
      if(!st.order.length)return;
      st.playing=true;st.index=0;preview(clip(st.order[0]));
    };
    $('editPlayer').ontimeupdate=()=>{
      const video=$('editPlayer'),end=Number(video.dataset.endMs)/1000;
      if(end>0&&video.currentTime>=end-.06){
        video.pause();if(st.playing)nextInPreview();
      }
    };
    $('closeJoin').onclick=()=>$('joinDialog').close();
    $('approveJoin').onclick=()=>judgeJoin('approve');
    $('rejectJoin').onclick=()=>judgeJoin('reject');
    window.addEventListener('klip-sequence-visible',()=>load().catch(getHost().fail));
    window.addEventListener('klip-review-changed',()=>{if(!st.dirty)load().catch(getHost().fail);});
  }
  bind();
})();
'use strict';
(() => {
  const $=id=>document.getElementById(id);
  const token=document.querySelector('meta[name="klip-session"]').content;
  const state={assignments:[],criteria:{},reviews:{},index:0,loading:true,dirty:false};
  const player=$('player');
  const api=async(path,body)=>{
    const res=await fetch(path,{method:body?'POST':'GET',
      headers:{'X-Klip-Token':token,...(body?{'Content-Type':'application/json'}:{})},
      ...(body?{body:JSON.stringify(body)}:{}),cache:'no-store'});
    let data={};try{data=await res.json();}catch{}
    if(!res.ok)throw Error(data.error||'Unable to access local benchmark');
    return data;
  };
  const fmt=ms=>{
    let sec=Math.max(0,Math.round(ms/1000));
    return Math.floor(sec/60)+':'+String(sec%60).padStart(2,'0');
  };
  const questions={
    snorkeling:['Is the scene underwater, surface, sandy seabed or reef?',
      'Is the subject visible even if the water is murky?',
      'Did you actually observe behavior or only an animal-like shape?',
      'Is the approach and departure worth keeping?'],
    ski:['Is this a lift, traverse or sustained descent?',
      'Which maneuver actually occurred, and did we see its outcome?',
      'Does the sequence reveal technique, line choice or scenery?'],
    bike:['Is this a climb, transfer, flow trail or technical feature?',
      'Is the approach, feature execution and exit visible?',
      'Does camera shake hide or exaggerate a maneuver?'],
    unresolved:['Which sport is actually visible?',
      'Does this interval contain a meaningful event that Klip overlooked?',
      'What other evidence would clarify it?']
  };
  function announce(text,error=false){
    $('status').textContent=text;$('status').style.color=error?'#efb4a2':'#b4c7d4';
  }
  function field(id,val){$(id).value=val===null||val===undefined?'':String(val);}
  function renderCriteria(){
    $('criteria').replaceChildren();
    for(const [name,description] of Object.entries(state.criteria)){
      const label=document.createElement('label');
      label.className='rating';
      const caption=document.createElement('span');
      caption.textContent=name;
      const desc=document.createElement('span');
      desc.className='rdesc';desc.textContent=description;
      caption.append(desc);
      const select=document.createElement('select');
      select.dataset.criterion=name;
      for(const [value,text] of [['','Unassessed'],['0','0.00'],['0.25','0.25'],
                                ['0.5','0.50'],['0.75','0.75'],['1','1.00']]){
        const opt=document.createElement('option');
        opt.value=value;opt.textContent=text;select.append(opt);
      }
      label.append(caption,select);$('criteria').append(label);
    }
  }
  function reviewPayload(){
    const a=state.assignments[state.index];
    if(!a)return null;
    const event=$('observedEvent').value;
    const ratings={};
    for(const el of document.querySelectorAll('[data-criterion]')){
      ratings[el.dataset.criterion]=el.value===''?null:Number(el.value);
    }
    return {reviewId:a.reviewId,
      reviewDecision:$('reviewDecision').value||null,
      observedEvent:event===''?null:event==='true',
      observedActivity:$('observedActivity').value||null,
      eventKind:$('eventKind').value||null,
      notes:$('notes').value,ratings};
  }
  function canNavigate(){
    return !state.dirty || window.confirm(
      'You have unsaved observations. Leave this moment without saving?');
  }
  function move(delta){
    const index=state.index+delta;
    if(index<0||index>=state.assignments.length||!canNavigate())return;
    state.index=index;render();
  }
  function render(){
    state.dirty=false;
    const a=state.assignments[state.index];
    if(!a)return;
    $('category').textContent=a.selectionGroup.toUpperCase().replaceAll('_',' ')+
      ' · '+(a.role==='control_systematic'?'SYSTEMATIC CONTROL':'CANDIDATE HYPOTHESIS');
    $('clipTitle').textContent=a.basename;
    $('clipContext').textContent='Sampled reason: '+
      (a.schedulerReasons||[]).join(', ')+' · '+a.analysisCoverage;
    $('range').textContent=fmt(a.startMs)+' – '+fmt(a.endMs);
    $('counter').textContent=(state.index+1)+' / '+state.assignments.length+
      ' · '+Object.keys(state.reviews).length+' reviewed';
    $('previous').disabled=state.index===0;
    $('next').disabled=state.index===state.assignments.length-1;
    $('saveStatus').textContent=state.reviews[a.reviewId]?'Saved review loaded':'Not reviewed';
    const saved=state.reviews[a.reviewId]||{};
    field('reviewDecision',saved.reviewDecision);
    field('observedEvent',saved.observedEvent===true?'true':
        saved.observedEvent===false?'false':'');
    field('observedActivity',saved.observedActivity);
    field('eventKind',saved.eventKind);
    field('notes',saved.notes||'');
    for(const el of document.querySelectorAll('[data-criterion]')){
      const val=saved.ratings?.[el.dataset.criterion];
      el.value=val===null||val===undefined?'':String(val);
    }
    $('prompts').replaceChildren();
    for(const phrase of (questions[a.selectionGroup]||questions.unresolved)){
      const li=document.createElement('li');
      li.textContent=phrase;
      $('prompts').append(li);
    }
    player.pause();
    player.removeAttribute('src');
    player.load();
    // No arbitrary path. This route resolves only indexed, SHA-verified
    // originals through Studio's token-protected, read-only byte-range server.
    player.src='/api/media/'+encodeURIComponent(a.assetId)+
      '?token='+encodeURIComponent(token);
    player.load();
    announce('This is a review assignment, not a confirmed positive or negative.');
  }
  function seekStart(){
    const a=state.assignments[state.index];
    if(!a)return;
    try{player.currentTime=a.startMs/1000;}catch{
      announce('Source is not seekable by the browser; verify the camera codec.',true);
    }
  }
  player.addEventListener('loadedmetadata',seekStart);
  player.addEventListener('seeking',()=>{
    const a=state.assignments[state.index];
    if(!a)return;
    const low=a.startMs/1000;
    const high=a.endMs/1000;
    if(player.currentTime<low-0.1||player.currentTime>high+0.1){
      try{player.currentTime=low;}catch{}
    }
  });
  player.addEventListener('timeupdate',()=>{
    const a=state.assignments[state.index];
    if(a&&player.currentTime>=a.endMs/1000){
      player.pause();
    }
  });
  player.addEventListener('error',()=>announce(
    'Video cannot play here. Check that the DJI SSD is attached and browser codec support.',true));
  $('replay').addEventListener('click',()=>{seekStart();player.play().catch(()=>{});});
  $('previous').addEventListener('click',()=>move(-1));
  $('next').addEventListener('click',()=>move(1));
  document.querySelector('.review-panel').addEventListener('input',()=>{
    state.dirty=true;
    $('saveStatus').textContent='Unsaved review changes';
  });
  document.querySelector('.review-panel').addEventListener('change',()=>{
    state.dirty=true;
    $('saveStatus').textContent='Unsaved review changes';
  });
  $('save').addEventListener('click',async()=>{
    const payload=reviewPayload();if(!payload)return;
    $('save').disabled=true;$('saveStatus').textContent='Saving…';
    try{
      await api('/api/benchmark/review',payload);
      state.reviews[payload.reviewId]=payload;
      state.dirty=false;
      $('saveStatus').textContent='Saved locally · append-only revision';
      $('counter').textContent=(state.index+1)+' / '+state.assignments.length+
        ' · '+Object.keys(state.reviews).length+' reviewed';
    }catch(err){
      $('saveStatus').textContent=err.message;
    }finally{$('save').disabled=false;}
  });
  document.addEventListener('keydown',event=>{
    if(event.target.closest('input,textarea,select,button'))return;
    if(event.key==='ArrowRight'&&!$('next').disabled)move(1);
    if(event.key==='ArrowLeft'&&!$('previous').disabled)move(-1);
  });
  (async()=>{
    try{
      const data=await api('/api/benchmark');
      state.assignments=data.assignments;
      state.criteria=data.criteria;
      state.reviews=data.reviews;
      renderCriteria();
      if(state.assignments.length){render();}
      else announce('No review assignments found.');
    }catch(err){announce(err.message,true);}
  })();
})();
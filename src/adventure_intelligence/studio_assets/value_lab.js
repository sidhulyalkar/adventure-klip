'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const token = document.querySelector('meta[name="klip-session"]').content;
  const query = new URLSearchParams(window.location.search);
  const rawBatch = query.get('batch') || '1';
  const batch = /^\d{1,5}$/.test(rawBatch) && Number(rawBatch)>0 ? Number(rawBatch) : 1;
  const groups = {
    facetsEvent:['eventSignificance','subjectVisibility','actionOutcome','uncertainty','investigationPotential'],
    facetsVisual:['ambientClarity','imageQuality','narrativeCompleteness','visualNovelty'],
    facetsValue:['learningValue','personalMeaning','audienceAppeal','watchAgain']
  };
  const label = {
    eventSignificance:'Observed event significance',
    subjectVisibility:'Subject visibility',
    ambientClarity:'Environmental clarity',
    imageQuality:'Image / camera quality',
    narrativeCompleteness:'Story completeness',
    actionOutcome:'Action or encounter outcome',
    visualNovelty:'Distinctiveness in your library',
    learningValue:'Learning and improvement value',
    personalMeaning:'Personal meaning',
    audienceAppeal:'Audience interest',
    watchAgain:'Want to rewatch',
    investigationPotential:'Worth investigating further',
    uncertainty:'Remaining uncertainty'
  };
  const state = {moments:[],reviews:{},criteria:{},scale:{},index:0,busy:false,start:0,end:0};
  const player = $('player');
  const fmt = ms => {
    const sec=Math.max(0,Math.floor(ms/1000));
    return String(Math.floor(sec/60)).padStart(2,'0')+':'+String(sec%60).padStart(2,'0');
  };
  const titleCase = value => String(value||'unknown').replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());
  function say(s,error=false){$('savedState').textContent=s;$('savedState').style.color=error?'#f0a49f':'#b6d6c2';}
  async function api(path,body){
    const response=await fetch(path,{
      method:body?'POST':'GET',
      headers:{'X-Klip-Token':token,...(body?{'Content-Type':'application/json'}:{})},
      body:body?JSON.stringify(body):undefined,
      cache:'no-store'
    });
    const data=await response.json();
    if(!response.ok) throw new Error(data.error||('Request failed: '+response.status));
    return data;
  }
  function buildRows(){
    Object.entries(groups).forEach(([container,keys])=>{
      $(container).replaceChildren();
      keys.forEach(key=>{
        const row=document.createElement('div');row.className='facet';
        const labelNode=document.createElement('label');
        labelNode.textContent=label[key]||titleCase(key);
        labelNode.setAttribute('for','rate-'+key);
        const hint=document.createElement('small');
        hint.textContent=state.criteria[key]||'Human judgment only';
        labelNode.appendChild(hint);
        const select=document.createElement('select');select.id='rate-'+key;
        select.dataset.ratingKey=key;
        const unset=document.createElement('option');unset.value='';unset.textContent='Not assessed';
        select.appendChild(unset);
        [0,1,2,3,4].forEach(value=>{
          const option=document.createElement('option');
          option.value=String(value);
          option.textContent=value+' · '+state.scale[String(value)];
          select.appendChild(option);
        });
        row.append(labelNode,select);
        $(container).appendChild(row);
      });
    });
  }
  function current(){return state.moments[state.index]||null;}
  function fillReview(m){
    const review=state.reviews[m.momentId]||null;
    $('decision').value=review?.decision||'later';
    $('observed').value=review?.eventObserved||'uncertain';
    $('reviewedActivity').value=review?.reviewedActivity||
      (m.activityConfirmed?m.selectionGroup:'unresolved');
    $('note').value=review?.note||'';
    $('improvement').value=review?.improvementNote||'';
    document.querySelectorAll('select[data-rating-key]').forEach(element=>{
      const rating=review?.ratings?.[element.dataset.ratingKey];
      element.value=rating===null||rating===undefined?'':String(rating);
    });
    say(review?'Previously reviewed; you can revise it.':'Not yet reviewed.');
  }
  function render(){
    const m=current();
    $('batchName').textContent='Batch '+batch;
    $('progress').textContent=Object.keys(state.reviews).length+' / '+state.moments.length;
    $('position').textContent=state.moments.length?(state.index+1)+' / '+state.moments.length:'0 / 0';
    $('prev').disabled=!m||state.index===0;
    $('next').disabled=!m||state.index===state.moments.length-1;
    $('save').disabled=!m;
    $('saveNext').disabled=!m;
    $('replay').disabled=!m;
    $('empty').hidden=Boolean(m);
    if(!m){
      player.removeAttribute('src');player.load();
      $('clipTitle').textContent='No clips in this batch';
      $('clipMeta').textContent='Create a value-lab packet for the frozen archive pilot first.';
      return;
    }
    $('sourceKind').textContent=m.selectionSource.replaceAll('_',' ').toUpperCase();
    $('clipTitle').textContent=titleCase(m.selectionGroup)+' · '+titleCase(m.candidateKind);
    $('clipMeta').textContent='Source '+m.assetId.slice(0,12)+'… · '+(m.activityConfirmed?'confirmed catalog activity':'unverified activity hypothesis');
    $('range').textContent=fmt(m.startMs)+' – '+fmt(m.endMs);
    $('contextText').textContent=(
      m.selectionSource==='random_control'?
      'Randomly sampled control. It is NOT assumed negative or low-value. Look for missed events.' :
      m.selectionSource==='reviewed_reference'?
      'A previous reviewed interval. Rate it independently to establish consistent criteria.' :
      m.selectionSource==='episode_proposal'?
      'Cached episode candidate. Its type and event significance have NOT been verified.' :
      'Scheduled sparse coverage interval; an event may or may not be present.'
    );
    state.start=m.startMs/1000;state.end=m.endMs/1000;
    const url='/api/media/'+encodeURIComponent(m.assetId)+'?token='+encodeURIComponent(token);
    player.pause();
    if(player.getAttribute('src')!==url){player.setAttribute('src',url);player.load();}
    else{try{player.currentTime=state.start;}catch(_){/* metadata not loaded */}}
    fillReview(m);
  }
  player.addEventListener('loadedmetadata',()=>{
    if(current()){
      try{player.currentTime=Math.min(state.start,Math.max(0,player.duration-.05));}
      catch(_){say('Unable to seek this source.',true);}
    }
  });
  player.addEventListener('timeupdate',()=>{
    if(current() && player.currentTime>=state.end){player.pause();}
  });
  player.addEventListener('error',()=>{
    if(current())say('Source unavailable or changed since indexing. Rating can still be left uncertain.',true);
  });
  $('replay').addEventListener('click',()=>{
    if(!current())return;
    player.currentTime=state.start;player.play().catch(()=>{say('Press play to start preview.',true);});
  });
  function navigate(step){const next=state.index+step;if(next<0||next>=state.moments.length)return;state.index=next;render();}
  $('prev').addEventListener('click',()=>navigate(-1));
  $('next').addEventListener('click',()=>navigate(1));
  function collectRatings(){
    const result={};
    document.querySelectorAll('select[data-rating-key]').forEach(element=>{
      result[element.dataset.ratingKey]=element.value===''?null:Number(element.value);
    });
    return result;
  }
  async function save(advance){
    const m=current();if(!m||state.busy)return;
    state.busy=true;$('save').disabled=true;$('saveNext').disabled=true;
    say('Saving review…');
    try{
      const ratings=collectRatings();
      const observed=$('observed').value;
      if(observed==='not_observed' && ratings.eventSignificance!==null && ratings.eventSignificance!==0){
        throw new Error('For no observed event, leave significance unrated or set it to 0.');
      }
      const review={
        batch,momentId:m.momentId,decision:$('decision').value,
        eventObserved:observed,reviewedActivity:$('reviewedActivity').value,ratings,
        note:$('note').value,improvementNote:$('improvement').value
      };
      const saved=await api('/api/value-lab/review',review);
      state.reviews[m.momentId]={...review,revisionId:saved.revisionId};
      $('progress').textContent=Object.keys(state.reviews).length+' / '+state.moments.length;
      say(saved.status==='unchanged'?'No changes to save.':'Review saved.');
      if(advance && state.index<state.moments.length-1)navigate(1);
    }catch(err){say(String(err.message||err),true);}
    finally{state.busy=false;$('save').disabled=false;$('saveNext').disabled=false;}
  }
  $('ratingsForm').addEventListener('submit',event=>{event.preventDefault();save(false);});
  $('saveNext').addEventListener('click',()=>save(true));
  document.addEventListener('keydown',event=>{
    if(event.target instanceof HTMLElement &&
       event.target.closest('input,select,textarea,button,[contenteditable="true"]'))return;
    if(event.key==='ArrowLeft'){event.preventDefault();navigate(-1);}
    if(event.key==='ArrowRight'){event.preventDefault();navigate(1);}
  });
  async function init(){
    try{
      const data=await api('/api/value-lab?batch='+encodeURIComponent(batch));
      state.moments=data.moments;
      state.reviews=data.reviews;
      state.criteria=data.criteria;
      state.scale=data.scale;
      buildRows();
      render();
    }catch(err){
      $('clipTitle').textContent='Cannot open review packet';
      $('clipMeta').textContent=String(err.message||err);
      $('empty').hidden=false;$('empty').textContent='Create the frozen value-lab packet using the Klip CLI, then refresh.';
      say(String(err.message||err),true);
    }
  }
  init();
})();
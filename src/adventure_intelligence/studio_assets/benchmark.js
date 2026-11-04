'use strict';
(() => {
  const $=id=>document.getElementById(id);
  const token=document.querySelector('meta[name="klip-session"]').content;
  const state={assignments:[],criteria:{},reviews:{},previews:{},index:0,loading:true,dirty:false,mode:'original',loadGeneration:0};
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
  function currentAssignment(){return state.assignments[state.index];}
  function mediaUrl(a,mode){
    if(mode==='preview'){
      return '/api/benchmark/preview-file/'+encodeURIComponent(a.reviewId)+
             '?token='+encodeURIComponent(token);
    }
    return '/api/media/'+encodeURIComponent(a.assetId)+
           '?token='+encodeURIComponent(token);
  }
  function loadMedia(mode){
    const a=currentAssignment();if(!a)return;
    state.mode=mode;
    const generation=++state.loadGeneration;
    player.pause();player.removeAttribute('src');player.load();
    $('sourceMode').textContent=mode==='preview'?
      'Short H.264 proxy · local only':'Original DJI file · SHA verification on first open';
    player.src=mediaUrl(a,mode);player.load();
    announce(mode==='preview'?'Loading cached short preview…':
      'Loading source; the first open may verify the full video SHA-256.');
    window.setTimeout(()=>{
      if(generation!==state.loadGeneration || player.readyState>=2)return;
      announce('Playback is taking longer than expected. Try Prepare short preview, or skip this moment for later.',true);
    },12000);
  }
  function showSuggestion(value){
    const el=$('aiSuggestion');
    if(!value){el.textContent='AI triage is optional and never substitutes for human approval.';return;}
    const ratings=value.ratings||{};
    const axis=[
      ['Event',ratings.eventInterest],['Subject',ratings.subjectVisibility],
      ['Clarity',ratings.ambientClarity],['Story',ratings.narrativePotential],
      ['Learning',ratings.learningPotential],['Sharing',ratings.sharingPotential]
    ].filter(x=>x[1]!==null&&x[1]!==undefined)
      .map(x=>x[0]+' '+x[1]+'/3').join(' · ');
    const rationale=value.policyVersion?
      ' · '+(value.needsTemporalReview?'Needs temporal review':'Three-frame assessment')+
      (value.uncertaintyReason?' · uncertainty: '+value.uncertaintyReason:'')+
      (Number.isFinite(value.investigationScore)?
        ' · inspect priority '+value.investigationScore+'/100 (heuristic, not a probability)':'')+
      (axis?' · '+axis:'')+
      (value.policyOverrides?.length?' · conservative gate applied':'')
      :'';
    const sevenFrames=value.policyVersion==='adaptive-temporal-second-look-v1';
    const evidence=sevenFrames?(value.frameEvidence||[]).map(x=>
      'Frame '+(x.index+1)+': '+x.observation).join(' · '):'';
    const comparison=sevenFrames&&value.firstPassComparison?.previousJudgmentAvailable?
      ' · First pass: '+value.firstPassComparison.previousRecommendation+
      ' · Changed: '+(value.firstPassComparison.recommendationChanged?'yes':'no'):'';
    el.textContent='AI suggests '+value.recommendation.toUpperCase()+
      ': '+value.summary+' · '+value.reason+rationale+
      (evidence?' · '+evidence:'')+comparison+
      (sevenFrames?' · Seven separate stills, no continuous motion evidence':
                   ' · Only three still frames')+
      ' · Uncalibrated, provisional, human approval required.';
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
    const info=state.previews[a.reviewId]||{};
    showSuggestion(info.temporalSuggestion||info.suggestion);
    $('temporalLook').disabled=!info.ready || !info.suggestion ||
       info.suggestion.policyVersion!=='adaptive-context-rubric-v2';
    loadMedia(info.ready?'preview':'original');
  }
  function seekStart(){
    const a=state.assignments[state.index];
    if(!a)return;
    try{player.currentTime=state.mode==='preview'?0:a.startMs/1000;}catch{
      announce('Source is not seekable by the browser; verify the camera codec.',true);
    }
  }
  player.addEventListener('loadedmetadata',seekStart);
  player.addEventListener('seeking',()=>{
    const a=state.assignments[state.index];
    if(!a)return;
    if(state.mode==='preview')return;
    const low=a.startMs/1000;
    const high=a.endMs/1000;
    if(player.currentTime<low-0.1||player.currentTime>high+0.1){
      try{player.currentTime=low;}catch{}
    }
  });
  player.addEventListener('timeupdate',()=>{
    const a=state.assignments[state.index];
    if(a&&player.currentTime>=
        (state.mode==='preview'?Math.max(0,player.duration-.05):a.endMs/1000)){
      player.pause();
    }
  });
  player.addEventListener('error',()=>announce(
    'Browser cannot play this media. Prepare the short H.264 preview, or inspect the source path and codec.',true));
  player.addEventListener('canplay',()=>announce('Ready for review · local read-only source'));
  player.addEventListener('stalled',()=>announce(
    'Playback stalled. The source SSD or codec may be slow. Use Prepare short preview.',true));
  $('replay').addEventListener('click',()=>{seekStart();player.play().catch(()=>{});});
  $('original').addEventListener('click',()=>loadMedia('original'));
  $('preparePreview').addEventListener('click',async()=>{
    const a=currentAssignment();if(!a)return;
    const rid=a.reviewId;
    $('preparePreview').disabled=true;
    announce('Verifying original SHA-256 and transcoding a short H.264 interval locally. This can take time.');
    try{
      await api('/api/benchmark/prepare-preview',{reviewId:rid});
      state.previews[rid]={...(state.previews[rid]||{}),ready:true};
      if(currentAssignment()?.reviewId===rid)loadMedia('preview');
    }catch(e){announce(e.message,true);}
    finally{$('preparePreview').disabled=false;}
  });
  $('aiTriage').addEventListener('click',async()=>{
    const a=currentAssignment();if(!a)return;
    const rid=a.reviewId;
    $('aiTriage').disabled=true;
    $('aiSuggestion').textContent='Local vision model is inspecting three frames, not the complete video.';
    try{
      const result=await api('/api/benchmark/local-judge',{reviewId:rid});
      state.previews[rid]={ready:true,suggestion:result.suggestion};
      if(currentAssignment()?.reviewId===rid){
        showSuggestion(result.suggestion);loadMedia('preview');
      }
    }catch(e){$('aiSuggestion').textContent=e.message;}
    finally{$('aiTriage').disabled=false;}
  });
  $('temporalLook').addEventListener('click',async()=>{
    const a=currentAssignment();if(!a)return;
    const rid=a.reviewId;
    $('temporalLook').disabled=true;
    $('aiSuggestion').textContent='Local model is examining seven sampled frames from an already verified short preview.';
    try{
      const result=await api('/api/benchmark/temporal-look',{reviewId:rid});
      state.previews[rid]={
        ...(state.previews[rid]||{}),
        temporalSuggestion:result.suggestion,
      };
      if(currentAssignment()?.reviewId===rid)showSuggestion(result.suggestion);
    }catch(e){$('aiSuggestion').textContent=e.message;}
    finally{$('temporalLook').disabled=false;}
  });
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
  async function saveReview(advance=false){
    const payload=reviewPayload();if(!payload)return false;
    $('save').disabled=true;$('saveStatus').textContent='Saving…';
    try{
      await api('/api/benchmark/review',payload);
      state.reviews[payload.reviewId]=payload;
      state.dirty=false;
      $('saveStatus').textContent='Saved locally · append-only revision';
      $('counter').textContent=(state.index+1)+' / '+state.assignments.length+
        ' · '+Object.keys(state.reviews).length+' reviewed';
      if(advance && state.index<state.assignments.length-1)move(1);
      return true;
    }catch(err){
      $('saveStatus').textContent=err.message;
      return false;
    }finally{$('save').disabled=false;}
  }
  $('save').addEventListener('click',()=>saveReview(false));
  for(const button of document.querySelectorAll('[data-quick]')){
    button.addEventListener('click',async()=>{
      $('reviewDecision').value=button.dataset.quick;
      state.dirty=true;
      await saveReview(true);
    });
  }
  $('nextUnreviewed').addEventListener('click',()=>{
    if(!canNavigate())return;
    const next=state.assignments.findIndex((a,i)=>
      i>state.index && !state.reviews[a.reviewId]);
    if(next>=0){state.index=next;render();}
    else announce('No later unreviewed interval. You can revisit reviewed moments.');
  });
  document.addEventListener('keydown',event=>{
    if(event.target.closest('input,textarea,select,button'))return;
    if(event.key==='ArrowRight'&&!$('next').disabled)move(1);
    if(!event.repeat && ['k','s','u'].includes(event.key.toLowerCase())){
      const action={k:'keep',s:'skip',u:'uncertain'}[event.key.toLowerCase()];
      document.querySelector('[data-quick="'+action+'"]').click();
    }
    if(event.key==='ArrowLeft'&&!$('previous').disabled)move(-1);
  });
  (async()=>{
    try{
      const data=await api('/api/benchmark');
      state.criteria=data.criteria;
      state.reviews=data.reviews;
      state.previews=data.previews||{};
      // Local AI suggestions are only preliminary *triage*. We prioritize
      // high-potential/uncertain clips, retain systematic controls, and keep
      // every AI-skipped interval accessible. Human-reviewed entries go last.
      // Preserve *all* assignments, even when the model labels a
      // systematic control "Skip". Every fourth unreviewed slot remains
      // available to investigate what the selector may have missed.
      const pending=data.assignments.filter(a=>!state.reviews[a.reviewId]);
      const completed=data.assignments.filter(a=>state.reviews[a.reviewId]);
      const priority=a=>{
        const vote=state.previews[a.reviewId]?.suggestion?.recommendation;
        return vote==='keep'?0:vote==='unsure'?1:vote==='skip'?3:2;
      };
      const compare=(a,b)=>{
        const category=priority(a)-priority(b);
        if(category)return category;
        const sa=state.previews[a.reviewId]?.suggestion?.investigationScore;
        const sb=state.previews[b.reviewId]?.suggestion?.investigationScore;
        if(Number.isFinite(sa)&&Number.isFinite(sb))return sb-sa;
        if(Number.isFinite(sa))return -1;
        if(Number.isFinite(sb))return 1;
        return 0;
      };
      const controls=pending.filter(a=>a.role==='control_systematic').sort(compare);
      const other=pending.filter(a=>a.role!=='control_systematic').sort(compare);
      const queue=[];
      while(other.length||controls.length){
        if(queue.length%4===3 && controls.length)queue.push(controls.shift());
        else if(other.length)queue.push(other.shift());
        else queue.push(controls.shift());
      }
      state.assignments=[...queue,...completed];
      $('aiTriage').hidden=!data.localJudgeEnabled;
      $('temporalLook').hidden=!data.temporalSecondLookEnabled;
      renderCriteria();
      if(state.assignments.length){render();}
      else announce('No review assignments found.');
    }catch(err){announce(err.message,true);}
  })();
})();
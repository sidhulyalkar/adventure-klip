'use strict';
/* Opt-in boundary inspector. Never runs inference or silently edits annotations.
 * Decoder is created only after an explicit thumbnail request, 5 seeks maximum.
 */
(() => {
  const $ = id => document.getElementById(id);
  const node = $('boundaryInspector');
  if (!node) return;
  const state = {item:null, asset:null, generation:0, opened:false,
    decoder:null, busy:false, evidence:null};
  const fmt = ms => (ms/1000).toFixed(2)+'s';
  const safeMs = v => Number.isFinite(v)&&v>=0&&v<=Number.MAX_SAFE_INTEGER;
  const clear = () => {
    state.generation++;
    state.busy=false;
    if(state.decoder){
      state.decoder.pause();
      state.decoder.removeAttribute('src');
      state.decoder.load();
      state.decoder.remove();
      state.decoder=null;
    }
    $('boundaryBins').replaceChildren();
    $('boundaryHints').replaceChildren();
    $('boundaryFrames').replaceChildren();
    $('boundaryFrameStatus').textContent='';
    $('loadBoundaryFrames').disabled=false;
    state.evidence=null;
  };
  const note = message => {$('boundaryStatus').textContent=message;};
  function select(item,asset){
    clear();state.item=item;state.asset=asset;state.opened=false;
    node.hidden=true;
    $('openBoundaryInspector').disabled=!item||!asset||!(asset.duration>0);
    $('openBoundaryInspector').setAttribute('aria-expanded','false');
  }
  function seekMain(at){
    const player=$('focusPlayer');
    if(!state.item||!safeMs(at))return;
    try{player.pause();player.currentTime=at/1000;}catch{}
  }
  const make = (tag,cls,content) => {
    const element=document.createElement(tag);element.className=cls;
    if(content!==undefined)element.textContent=String(content);
    return element;
  };
  function plot(doc){
    $('boundaryBins').replaceChildren();
    $('boundaryHints').replaceChildren();
    const left=doc.windowStartMs,right=doc.windowEndMs;
    const span=right-left||1;
    const start=100*(doc.coreStartMs-left)/span;
    const end=100*(doc.coreEndMs-left)/span;
    const bars=$('boundaryBins');
    for(const b of doc.bins){
      const button=make('button','boundary-bin');button.type='button';
      button.style.height=(b.level===null?9:Math.max(9,8+57*b.level))+'px';
      if(b.level===null)button.classList.add('missing');
      const mid=(b.startMs+b.endMs)/2;
      if(mid>=doc.coreStartMs&&mid<doc.coreEndMs)button.classList.add('within-core');
      button.title=fmt(mid)+' · '+(b.level===null?'No cached evidence':
        'Relative image motion '+b.level+', '+b.samples+' samples');
      button.setAttribute('aria-label','Seek to '+fmt(mid)+'; '+button.title);
      button.onclick=()=>seekMain(mid);
      bars.append(button);
    }
    $('boundaryCore').style.left=start.toFixed(2)+'%';
    $('boundaryCore').style.width=Math.max(0,end-start).toFixed(2)+'%';
    $('boundaryWindow').textContent=fmt(left)+' – '+fmt(right);
    $('boundaryIn').textContent='In '+fmt(doc.coreStartMs);
    $('boundaryOut').textContent='Out '+fmt(doc.coreEndMs);
    const c=Math.round(doc.coverageFraction*100);
    note(doc.status==='observed'
      ? c+'% of time bins have cached image-motion evidence. Heights are relative to this window.'
      : 'Insufficient cached motion ('+c+'% bin coverage). No boundary hint offered.');
    for(const hint of doc.hints){
      const button=make('button','boundary-hint',
        'Inspect '+hint.edge+' near '+fmt(hint.atMs));
      button.type='button';button.onclick=()=>seekMain(hint.atMs);
      $('boundaryHints').append(button);
    }
  }
  async function open(){
    if(!state.item||!state.asset)return;
    if(state.opened){node.hidden=true;state.opened=false;
      $('openBoundaryInspector').setAttribute('aria-expanded','false');clear();return;}
    state.opened=true;node.hidden=false;
    $('openBoundaryInspector').setAttribute('aria-expanded','true');
    note('Reading cached evidence…');
    const generation=state.generation;
    const fieldA=Number($('fieldStart').value)*1000;
    const fieldB=Number($('fieldEnd').value)*1000;
    const start=Math.round(fieldA),end=Math.round(fieldB);
    if(!safeMs(start)||!safeMs(end)||end<=start||end-start>180000){
      note('This interval is outside the bounded evidence window. Inspect manually.');
      return;
    }
    try{
      const token=document.querySelector('meta[name="klip-session"]').content;
      const path='/api/boundary/'+encodeURIComponent(state.item.assetId)+
        '?startMs='+Math.round(start)+'&endMs='+Math.round(end);
      const response=await fetch(path,{headers:{'X-Klip-Token':token},cache:'no-store'});
      const body=await response.json();
      if(!response.ok)throw Error(body.error||'Unavailable');
      if(generation!==state.generation||!state.opened)return;
      state.evidence=body;plot(body);
    }catch(e){if(generation===state.generation)note('Cached evidence unavailable: '+e.message);}
  }
  function framePoints(){
    const item=state.item,asset=state.asset;
    if(!item||!asset)return [];
    const fieldA=Number($('fieldStart').value)*1000;
    const fieldB=Number($('fieldEnd').value)*1000;
    const valid=safeMs(fieldA)&&safeMs(fieldB)&&fieldB>fieldA;
    const a=valid?fieldA:item.startMs;
    const b=valid?fieldB:item.endMs;
    const duration=asset.duration*1000;
    if(!safeMs(a)||!safeMs(b)||b<=a||!safeMs(duration))return [];
    const positions=[a,(3*a+b)/4,(a+b)/2,(a+3*b)/4,b];
    return positions.map(x=>Math.max(0,Math.min(duration-250,Math.round(x))));
  }
  function ready(media,type,timeoutMs,generation){
    return new Promise((resolve,reject)=>{
      let settled=false;
      const finish=(error)=>{
        if(settled)return;settled=true;clearTimeout(timer);
        media.removeEventListener(type,onReady);
        media.removeEventListener('error',onError);
        if(error)reject(error);else resolve();
      };
      const onReady=()=>finish();
      const onError=()=>finish(Error('Browser cannot decode this source video'));
      const timer=setTimeout(()=>finish(Error('Frame extraction timed out')),timeoutMs);
      media.addEventListener(type,onReady,{once:true});
      media.addEventListener('error',onError,{once:true});
      if(generation!==state.generation)finish(Error('Clip changed'));
    });
  }
  async function thumbnails(){
    if(state.busy||!state.item||!state.asset)return;
    const points=framePoints();
    if(points.length!==5)return;
    state.busy=true;$('loadBoundaryFrames').disabled=true;
    $('boundaryFrameStatus').textContent='Decoding five preview frames locally…';
    const generation=state.generation;
    const video=document.createElement('video');
    video.muted=true;video.playsInline=true;video.preload='auto';
    video.className='boundary-hidden-video';
    document.body.append(video);state.decoder=video;
    const canvas=document.createElement('canvas');
    canvas.width=220;canvas.height=124;
    const ctx=canvas.getContext('2d');
    try{
      const token=document.querySelector('meta[name="klip-session"]').content;
      const loaded=ready(video,'loadedmetadata',12000,generation);
      video.src='/api/media/'+encodeURIComponent(state.item.assetId)+
        '?token='+encodeURIComponent(token);
      video.load();await loaded;
      if(!ctx||!Number.isFinite(video.duration))throw Error('Video decoder unavailable');
      for(const at of points){
        if(generation!==state.generation)throw Error('Clip changed');
        const target=Math.min(Math.max(0,at/1000),Math.max(0,video.duration-.05));
        if(Math.abs(video.currentTime-target)>.01){
          const seek=ready(video,'seeked',10000,generation);
          video.currentTime=target;
          await seek;
        }else if(video.readyState<2){
          await ready(video,'loadeddata',10000,generation);
        }
        if(generation!==state.generation)throw Error('Clip changed');
        if(video.videoWidth<1||video.videoHeight<1)throw Error('No decoded image');
        ctx.fillStyle='#0c1522';ctx.fillRect(0,0,220,124);
        const scale=Math.min(220/video.videoWidth,124/video.videoHeight);
        const w=video.videoWidth*scale,h=video.videoHeight*scale;
        ctx.drawImage(video,(220-w)/2,(124-h)/2,w,h);
        const card=make('button','boundary-frame');card.type='button';
        const img=document.createElement('img');img.alt='Preview near '+fmt(at);
        img.src=canvas.toDataURL('image/jpeg',.7);
        const label=make('span','',fmt(at));
        card.append(img,label);card.onclick=()=>seekMain(at);
        $('boundaryFrames').append(card);
      }
      $('boundaryFrameStatus').textContent=
        'Preview frames are approximate browser seeks, not verified decoded frame timestamps.';
    }catch(e){
      if(generation===state.generation)
        $('boundaryFrameStatus').textContent=
          'Frames unavailable ('+e.message+'). Use the main player to inspect boundaries.';
    }finally{
      video.pause();video.removeAttribute('src');video.load();video.remove();
      if(state.decoder===video)state.decoder=null;
      if(generation===state.generation){state.busy=false;$('loadBoundaryFrames').disabled=false;}
    }
  }
  $('openBoundaryInspector').onclick=open;
  $('loadBoundaryFrames').onclick=thumbnails;
  window.KlipBoundaryInspector={select,clear};
})();

"""Calibrated, evidence-preserving per-video timelines.

All signals are observational. Image motion is not a confirmed jump, turn, or speed.
"""
from __future__ import annotations
from dataclasses import dataclass
import json
import math
from pathlib import Path
from statistics import median
from .catalog import connect

VERSION='timeline-v1'


def robust_scale(values: list[float]) -> tuple[float,float]:
    if not values:
        return 0.0,1.0
    center=median(values)
    mad=median([abs(v-center) for v in values])
    return center,max(1.4826*mad,0.01)


def normalize(x:float, center:float, scale:float) -> float:
    z=(x-center)/scale
    return round(max(0.,min(1.,0.5+0.18*z)),4)


def timeline_from_series(asset_id:str, duration_ms:int, series:list[dict], *,
                         source:str='pixel-change', bin_ms:int=2000) -> dict:
    if bin_ms <= 0:
        raise ValueError('bin_ms must be positive')
    key='change' if source=='pixel-change' else 'flow_magnitude'
    observations=[]
    for x in series:
        if key not in x:
            continue
        t=x.get('t_ms')
        raw=float(x[key])
        if not isinstance(t,(int,float)) or not math.isfinite(t) or t < 0 or not math.isfinite(raw):
            continue
        if duration_ms and t > duration_ms+1000:
            continue
        observations.append((int(t),raw))
    observations.sort()
    center,scale=robust_scale([v for _,v in observations])
    duration_ms=max(duration_ms,observations[-1][0]+1 if observations else 0)
    bins=[]
    for start in range(0,duration_ms,bin_ms):
        end=min(duration_ms,start+bin_ms)
        vals=[v for t,v in observations if start<=t<end]
        raw=round(sum(vals)/len(vals),6) if vals else None
        normalized=normalize(raw,center,scale) if raw is not None else None
        bins.append({'startMs':start,'endMs':end,'signals':{
            'visualMotion':{'rawMean':raw,'normalized':normalized,'samples':len(vals),
                            'unit':'mean_absolute_pixel_change' if source=='pixel-change'
                                   else 'optical_flow_magnitude',
                            'interpretation':'camera/image motion, not athletic motion'}},
                     'importance':{'heuristic':normalized,'model':None,'reviewed':None},
                     'events':[], 'objects':[], 'keypoints':[], 'semanticRef':None})
    return {'schemaVersion':1,'algorithm':VERSION,'assetId':asset_id,
            'durationMs':duration_ms,'source':source,'calibration':{
                'median':round(center,6),'robustScale':round(scale,6),
                'scope':'per-video','comparableAcrossVideos':False},'bins':bins}


def generate_timelines(workspace:Path, *, source:str='pixel-change', bin_ms:int=2000, attach_semantics:bool=True) ->dict:
    # Analysis run must be completed first; we don't re-decode the video for the timeline.
    result={'generated':0,'missing':0,'paths':[]}
    with connect(workspace) as db:
        rows=list(db.execute('SELECT id,duration FROM assets'))
        for a in rows:
            aid=a['id']
            candidates=sorted((workspace/'features').glob(f'{aid}-*.json'))
            target=next((p for p in candidates if ('pixel-change' if source=='pixel-change'
                                                   else 'optical-flow') in p.name),None)
            if target is None:
                result['missing']+=1
                continue
            data=json.loads(target.read_text())
            document=timeline_from_series(aid,int((a['duration'] or 0)*1000),
                                          data['series'],source=source,bin_ms=bin_ms)
            events=list(db.execute('SELECT id,kind,start_ms,end_ms,score,confidence,origin '
                                   'FROM events WHERE asset_id=? ORDER BY start_ms',(aid,)))
            for b in document['bins']:
                for e in events:
                    if e['start_ms']<b['endMs'] and b['startMs']<e['end_ms']:
                        b['events'].append({'id':e['id'],'kind':e['kind'],'confidence':e['confidence'],
                                            'score':e['score'],'origin':e['origin']})
            if attach_semantics:
                for semantic_file in sorted((workspace/'semantics').glob(f'{aid}-*.json')):
                    description=json.loads(semantic_file.read_text())
                    for frame in description.get('frames',[]):
                        # Assign sparse descriptions to the one timeline bin containing the frame.
                        t=frame.get('tMs',-1)
                        if t<0 or t>=duration_ms:
                            continue
                        bin_index=int(t//bin_ms)
                        bin_record=document['bins'][bin_index]
                        bin_record['semanticRef']=semantic_file.name
                        for obj in frame.get('objects',[]):
                            bin_record['objects'].append({'tMs':t,**obj})
                        for joint in frame.get('keypoints',[]):
                            bin_record['keypoints'].append({'tMs':t,**joint})
                        if frame.get('embedding') is not None:
                            bin_record.setdefault('embeddingFrames',[]).append({'tMs':t,
                                'semanticFile':semantic_file.name,'frameIndex':description['frames'].index(frame)})
            folder=workspace/'timelines' 
            folder.mkdir(exist_ok=True,parents=True)
            output=folder/f'{aid}-{source}-{bin_ms}.json'
            tmp=output.with_suffix('.tmp')
            tmp.write_text(json.dumps(document,indent=2)+'\n')
            tmp.replace(output)
            result['generated']+=1
            result['paths'].append(str(output))
    return result

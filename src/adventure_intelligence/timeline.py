"""Calibrated, evidence-preserving per-video timelines.

All signals are observational. Image motion is not a confirmed jump, turn, or speed.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
from statistics import median
from .catalog import connect
from .artifacts import read_artifact
from .sources import resolve_source
from .semantic_jobs import completed_document

VERSION='timeline-pts-v2'


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
        if duration_ms and t >= duration_ms:
            continue
        observations.append((int(t),raw))
    observations.sort()
    center,scale=robust_scale([v for _,v in observations])
    if not duration_ms:
        duration_ms=observations[-1][0]+1 if observations else 0
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


def generate_timelines(workspace:Path, *, source:str='pixel-change', bin_ms:int=2000, attach_semantics:bool=True, asset_ids:set[str] | None=None) ->dict:
    # Analysis run must be completed first; we don't re-decode the video for the timeline.
    result={'generated':0,'missing':0,'paths':[], 'failed':[]}
    with connect(workspace) as db:
        rows=list(db.execute('SELECT * FROM assets'))
        for a in rows:
            aid=a['id']
            if asset_ids is not None and aid not in asset_ids:
                continue
            stage = 'motion' if source=='pixel-change' else 'flow'
            analysis = db.execute('SELECT version,status FROM analyses WHERE asset_id=? AND stage=?',
                                  (aid,stage)).fetchone()
            data = (read_artifact(db,workspace,aid,analysis['version'].split('|')[0])
                    if analysis and analysis['status']=='completed' else None)
            if data is None:
                result['missing']+=1
                continue
            try:
                resolve_source(db,a)
            except OSError as exc:
                result['failed'].append({'assetId':aid,'error':str(exc)})
                continue
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
                for job in db.execute("SELECT j.* FROM semantic_jobs j JOIN semantic_active a ON a.asset_id=j.asset_id AND a.config_id=j.config_id WHERE j.asset_id=? AND j.status='completed' ORDER BY a.provider",(aid,)).fetchall():
                    description=completed_document(workspace,job)
                    if description is None:
                        continue
                    semantic_file=Path(job['filename'])
                    tracking=description.get('tracking',{})
                    if isinstance(tracking,dict) and tracking.get('tracks'):
                        document.setdefault('semanticTracks',[]).extend(
                            {'semanticFile':semantic_file.name,**track}
                            for track in tracking['tracks'])
                    for frame_index,frame in enumerate(description.get('frames',[])):
                        # Prefer measured source PTS when it belongs to the video-local
                        # timeline. Preserve the requested seek even when PTS is usable.
                        requested=frame.get('requestedMs',frame.get('tMs',-1))
                        observed=frame.get('decodedPtsMs')
                        use_observed=(isinstance(observed,int) and not isinstance(observed,bool)
                                      and 0<=observed<document['durationMs'])
                        t=observed if use_observed else requested
                        if not isinstance(t,int) or t<0 or t>=document['durationMs']:
                            continue
                        basis=('decoded_frame_pts' if use_observed
                               else 'requested_seek_fallback')
                        timing={'tMs':t,'requestedMs':requested,
                                'decodedPtsMs':observed,
                                'ptsTimeBase':frame.get('ptsTimeBase'),
                                'timestampBasis':basis}
                        bin_index=t//bin_ms
                        bin_record=document['bins'][bin_index]
                        bin_record['semanticRef']=semantic_file.name
                        refs=bin_record.setdefault('semanticRefs',[])
                        if semantic_file.name not in refs:
                            refs.append(semantic_file.name)
                        for obj in frame.get('objects',[]):
                            bin_record['objects'].append({**timing,**obj})
                        for joint in frame.get('keypoints',[]):
                            bin_record['keypoints'].append({**timing,**joint})
                        if frame.get('embedding') is not None:
                            bin_record.setdefault('embeddingFrames',[]).append({
                                **timing,'semanticFile':semantic_file.name,
                                'frameIndex':frame_index})
            folder=workspace/'timelines' 
            folder.mkdir(exist_ok=True,parents=True)
            output=folder/f'{aid}-{source}-{bin_ms}.json'
            tmp=output.with_suffix('.tmp')
            tmp.write_text(json.dumps(document,indent=2)+'\n')
            tmp.replace(output)
            result['generated']+=1
            result['paths'].append(str(output))
    return result

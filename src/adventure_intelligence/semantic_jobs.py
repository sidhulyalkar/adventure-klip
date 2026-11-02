"""Per-frame semantic checkpoints and verified completed documents."""
import hashlib
import json
import math
import time
from pathlib import Path
from .catalog import connect
from .sources import resolve_source, verified_digest


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',', ':'),allow_nan=False)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def completed_document(workspace, job):
    if job['status'] != 'completed' or not job['filename']:
        return None
    path = workspace/'semantics'/job['filename']
    if path.parent.resolve() != (workspace/'semantics').resolve():
        return None
    try:
        raw = path.read_text()
        doc = json.loads(raw)
        if (digest(raw)!=job['sha256'] or doc['assetId']!=job['asset_id']
                or doc['configId']!=job['config_id']):
            return None
        return doc
    except (OSError,ValueError,KeyError,TypeError):
        return None


def frame_descriptor(provider, raw, t, timing=None):
    description = provider.describe(raw)
    vector = description.get('embedding')
    if vector is not None:
        if (not isinstance(vector,list) or not 0 < len(vector) <= 8192
            or not all(isinstance(v,(float,int)) and not isinstance(v,bool)
                       and math.isfinite(v) and abs(v)<1e8 for v in vector)):
            raise ValueError('Invalid embedding')
        norm = math.sqrt(sum(v*v for v in vector))
        vector_info = {'dimensions':len(vector),'l2Norm':norm,
                       'l2Normalized':abs(norm-1)<1e-5}
    else:
        vector_info = None
    objects=description.get('objects',[])
    keypoints=description.get('keypoints',[])
    if not isinstance(objects,list) or not isinstance(keypoints,list):
        raise ValueError('Objects and keypoints must be lists')
    scene_scores=description.get('sceneScores')
    if scene_scores is not None:
        if (not isinstance(scene_scores,dict)
            or scene_scores.get('method')!='siglip2_image_text_cosine'
            or not isinstance(scene_scores.get('similarities'),dict)
            or not all(isinstance(v,(int,float)) and not isinstance(v,bool)
                       and math.isfinite(v) and -1.01<=v<=1.01
                       for v in scene_scores['similarities'].values())):
            raise ValueError('Invalid scene similarities')
    result={'tMs':t,'requestedMs':t,
            'timestampBasis':(timing or {}).get('timestampBasis','requested_seek_pts_unavailable'),
            'decodedPts':(timing or {}).get('decodedPts'),
            'ptsTimeBase':(timing or {}).get('ptsTimeBase'),
            'decodedPtsMs':(timing or {}).get('decodedPtsMs'),
            'sceneScores':scene_scores,
            'embedding':vector,'embeddingInfo':vector_info,
            'objects':[{**obj,'reviewed':False} for obj in objects],
            'keypoints':[{**point,'reviewed':False} for point in keypoints],
            'notes':description.get('notes',[])}
    encoded(result)  # Reject NaN/Infinity or non-JSON model outputs before committing.
    return result


def activate(db,aid,provider,config_id):
    db.execute("""INSERT INTO semantic_active VALUES (?,?,?)
        ON CONFLICT(asset_id,provider) DO UPDATE SET config_id=excluded.config_id""",
        (aid,provider,config_id))


def run_semantics(workspace, provider, *, sample_seconds=4.0, max_frames=300,
                  limit=None, asset_ids=None, progress=None,
                  sample_schedule=None, schedule_id=None, decode_mode='single'):
    from .semantic import frame_png, VERSION
    if decode_mode not in {'single', 'batched'}:
        raise ValueError('Unknown decoder mode')
    if not math.isfinite(sample_seconds) or sample_seconds<=0 or max_frames<=0:
        raise ValueError('Sample interval and frame bound must be positive and finite')
    if sample_schedule is not None:
        if not isinstance(schedule_id,str) or len(schedule_id)!=64 or not isinstance(sample_schedule,dict):
            raise ValueError('Adaptive schedules require a verified plan ID')
        if asset_ids is None or set(sample_schedule)!=set(asset_ids):
            raise ValueError('Adaptive schedule assets must exactly match the selected assets')
    elif schedule_id is not None:
        raise ValueError('A plan ID requires an adaptive schedule')
    config={'sampler':VERSION,'provider':provider.name,'modelVersion':provider.version,
            'providerConfig':getattr(provider,'cache_config',{}),
            'sampleSeconds':sample_seconds,'maxFrames':max_frames,'width':384,
            'format':'png','schemaVersion':3,'trackerVersion':'mutual-iou-v1'}
    from importlib.metadata import version, PackageNotFoundError
    config['runtimeVersions']={}
    for package in ('pillow','torch','transformers','mediapipe'):
        try:
            config['runtimeVersions'][package]=version(package)
        except PackageNotFoundError:
            pass
    if sample_schedule is not None:
        config['adaptiveScheduleId']=schedule_id
        config['samplingStrategy']='cached_interest_with_periodic_coverage_v1'
    if decode_mode == 'batched':
        # Different decoder behavior must never masquerade as an old cache hit.
        config['frameDecoder']='select-pts-png-v1-fail-closed'
    config_json=encoded(config)
    config_id=digest(config_json)
    result={'processed':0,'cached':0,'missing':0,'errors':[],
            'frames_computed':0,'frames_cached':0,'configId':config_id,
            'timingSeconds':{'sourceVerification':0.,'frameDecoding':0.,
                             'modelInference':0.,'finalVerification':0.},
            'scheduleId':schedule_id}
    if decode_mode == 'batched':
        result['decodeDiagnostics']={'batchAttempts':0,'batchSucceeded':0,
            'batchFallbacks':0,'singleSeekFrames':0,'batchedFrames':0}
    with connect(workspace) as db:
        assets=[asset for asset in db.execute('SELECT * FROM assets ORDER BY created_at,id').fetchall()
                if asset_ids is None or asset['id'] in asset_ids]
        if progress is not None:
            progress.start('semantics', len(assets))
        for asset_number, asset in enumerate(assets, 1):
            aid=asset['id']
            if asset_ids is not None and aid not in asset_ids:
                continue
            if limit is not None and result['processed']>=limit:
                break
            # A legacy catalog can contain AppleDouble metadata or damaged
            # footage indexed before duration checks. Skip without touching
            # the SSD or hashing source bytes; this is not a model failure.
            if not asset['duration'] or asset['duration']<=0:
                result['missing']+=1
                if progress is not None:
                    progress.item('semantics', asset_number, len(assets),
                                  f'{aid[:12]} skipped: no valid video duration')
                continue
            job=db.execute('SELECT * FROM semantic_jobs WHERE asset_id=? AND config_id=?',
                           (aid,config_id)).fetchone()
            path=None
            try:
                if progress is not None:
                    progress.note('semantics', f'[{asset_number}/{len(assets)}] checking source {aid[:12]}')
                tick=time.perf_counter()
                path=resolve_source(db,asset,progress=progress)
                result['timingSeconds']['sourceVerification']+=time.perf_counter()-tick
                source_stat=path.stat()
                duration=int((asset['duration'] or 0)*1000)
                if duration<=0:
                    raise ValueError('A positive probed duration is required')
                if job is not None and completed_document(workspace,job) is not None:
                    activate(db,aid,provider.name,config_id)
                    db.commit()
                    result['cached']+=1
                    if progress is not None:
                        progress.item('semantics', asset_number, len(assets), f'{path.name} cached')
                    continue
                db.execute('''INSERT INTO semantic_jobs(asset_id,config_id,config_json,status)
                    VALUES (?,?,?,'running') ON CONFLICT(asset_id,config_id)
                    DO UPDATE SET status='running',error=NULL''',(aid,config_id,config_json))
                activate(db,aid,provider.name,config_id)
                db.commit()
                if sample_schedule is None:
                    step=max(1,int(sample_seconds*1000),math.ceil(duration/max_frames))
                    requested_times=range(0,duration,step)
                else:
                    step=None
                    requested_times=sample_schedule.get(aid)
                    if (not isinstance(requested_times,list) or not requested_times
                            or len(requested_times)>512 or requested_times!=sorted(set(requested_times))
                            or any(not isinstance(t,int) or isinstance(t,bool)
                                   or not 0<=t<duration for t in requested_times)):
                        raise ValueError('Adaptive plan timestamps do not match catalog duration')
                frames=[]
                if decode_mode == 'batched':
                    from .batched_decode import decode_scheduled
                    # Do not waste batched decoding on checkpoints already verified.
                    # Use the same digest + timestamp checks as the processing loop.
                    missing_times=[]
                    for sample_t in requested_times:
                        saved=db.execute('''SELECT payload,sha256 FROM semantic_frames
                            WHERE asset_id=? AND config_id=? AND t_ms=?''',
                            (aid,config_id,sample_t)).fetchone()
                        valid=False
                        if saved and digest(saved['payload'])==saved['sha256']:
                            try:
                                valid=json.loads(saved['payload'])['tMs']==sample_t
                            except (ValueError,KeyError,TypeError):
                                pass
                        if not valid:
                            missing_times.append(sample_t)
                    decoded_iter=iter(decode_scheduled(path,missing_times,width=384,
                        mode='batched',counters=result['decodeDiagnostics']))
                expected=len(requested_times)
                stage_name=f'semantics:{path.name}'
                if progress is not None:
                    progress.start(stage_name,expected)
                for t in requested_times:
                    saved=db.execute('''SELECT payload,sha256 FROM semantic_frames
                        WHERE asset_id=? AND config_id=? AND t_ms=?''',(aid,config_id,t)).fetchone()
                    frame=None
                    if saved and digest(saved['payload'])==saved['sha256']:
                        try:
                            candidate=json.loads(saved['payload'])
                            if candidate['tMs']==t:
                                frame=candidate
                        except (ValueError,KeyError,TypeError):
                            pass
                    if frame is None:
                        fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
                        before=path.stat()
                        if any(getattr(before,f)!=getattr(source_stat,f) for f in fields):
                            db.execute('DELETE FROM semantic_frames WHERE asset_id=? AND config_id=?',(aid,config_id))
                            db.commit()
                            raise OSError('Source changed during semantic extraction')
                        tick=time.perf_counter()
                        if decode_mode == 'batched':
                            decoded_t, raw_png, timing = next(decoded_iter)
                            if decoded_t != t:
                                raise RuntimeError('Batch decoder timestamp order mismatch')
                            decoded=(raw_png,timing)
                        else:
                            decoded=frame_png(path,t,384,True)
                        result['timingSeconds']['frameDecoding']+=time.perf_counter()-tick
                        if isinstance(decoded,tuple) and len(decoded)==2:
                            raw_png,timing=decoded
                        else:
                            # Compatibility with legacy bytes-only inference providers.
                            raw_png,timing=decoded,None
                        tick=time.perf_counter()
                        frame=frame_descriptor(provider,raw_png,t,timing)
                        result['timingSeconds']['modelInference']+=time.perf_counter()-tick
                        after=path.stat()
                        if any(getattr(before,f)!=getattr(after,f) for f in fields):
                            db.execute('DELETE FROM semantic_frames WHERE asset_id=? AND config_id=?',(aid,config_id))
                            db.commit()
                            raise OSError('Source changed during semantic extraction')
                        payload=encoded(frame)
                        db.execute('''INSERT INTO semantic_frames VALUES (?,?,?,?,?)
                            ON CONFLICT(asset_id,config_id,t_ms) DO UPDATE SET
                            payload=excluded.payload,sha256=excluded.sha256''',
                            (aid,config_id,t,payload,digest(payload)))
                        db.commit()
                        result['frames_computed']+=1
                    else:
                        result['frames_cached']+=1
                    frames.append(frame)
                    if progress is not None:
                        progress.frames(stage_name, len(frames), expected, path.name)
                source_check=(lambda done,total: progress.bytes(
                    'source-check',done,total,path.name) if progress is not None else None)
                tick=time.perf_counter()
                matches=verified_digest(path,progress=source_check)==aid
                result['timingSeconds']['finalVerification']+=time.perf_counter()-tick
                if not matches:
                    # Observations from a changing source must never be reused.
                    db.execute('DELETE FROM semantic_frames WHERE asset_id=? AND config_id=?',(aid,config_id))
                    db.commit()
                    raise OSError('Source changed during semantic extraction')
                from .tracking import associate_boxes
                tracking=associate_boxes(frames)
                doc={'schemaVersion':3,'assetId':aid,'configId':config_id,
                     'provider':provider.name,'modelVersion':provider.version,
                     'config':config,'sampleIntervalMs':step,'frames':frames,
                     'tracking':tracking}
                filename=f'{aid}-{config_id}.json'
                target=workspace/'semantics'/filename
                target.parent.mkdir(parents=True,exist_ok=True)
                raw=encoded(doc)
                tmp=target.with_suffix('.tmp')
                try:
                    with tmp.open('w') as handle:
                        import os
                        handle.write(raw); handle.flush(); os.fsync(handle.fileno())
                    tmp.replace(target)
                finally:
                    tmp.unlink(missing_ok=True)
                db.execute('''UPDATE semantic_jobs SET status='completed',error=NULL,
                    filename=?,sha256=? WHERE asset_id=? AND config_id=?''',
                    (filename,digest(raw),aid,config_id))
                db.commit()
                result['processed']+=1
                if progress is not None:
                    progress.item('semantics', asset_number, len(assets), f'{path.name} analyzed')
            except Exception as exc:
                db.rollback()
                # If source verification fails, discard potentially contaminated checkpoints.
                try:
                    if path is not None and verified_digest(path)!=aid:
                        db.execute('DELETE FROM semantic_frames WHERE asset_id=? AND config_id=?',(aid,config_id))
                except OSError:
                    pass
                db.execute('''INSERT INTO semantic_jobs(asset_id,config_id,config_json,status,error)
                    VALUES (?,?,?,'failed',?) ON CONFLICT(asset_id,config_id)
                    DO UPDATE SET status='failed',error=excluded.error''',
                    (aid,config_id,config_json,str(exc)))
                activate(db,aid,provider.name,config_id)
                db.commit()
                result['errors'].append({'asset':aid,'error':str(exc)})
                if progress is not None:
                    progress.item('semantics', asset_number, len(assets), f'{aid[:12]} failed: {exc}')
    result['timingSeconds']={k:round(v,3) for k,v in result['timingSeconds'].items()}
    return result

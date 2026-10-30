"""Per-frame semantic checkpoints and verified completed documents."""
import hashlib
import json
import math
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


def frame_descriptor(provider, raw, t):
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
    result={'tMs':t,'timestampBasis':'requested_seek_not_measured_pts',
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
                  limit=None, asset_ids=None):
    from .semantic import frame_png, VERSION
    if not math.isfinite(sample_seconds) or sample_seconds<=0 or max_frames<=0:
        raise ValueError('Sample interval and frame bound must be positive and finite')
    config={'sampler':VERSION,'provider':provider.name,'modelVersion':provider.version,
            'providerConfig':getattr(provider,'cache_config',{}),
            'sampleSeconds':sample_seconds,'maxFrames':max_frames,'width':384,
            'format':'png','schemaVersion':2}
    from importlib.metadata import version, PackageNotFoundError
    config['runtimeVersions']={}
    for package in ('pillow','torch','transformers','mediapipe'):
        try:
            config['runtimeVersions'][package]=version(package)
        except PackageNotFoundError:
            pass
    config_json=encoded(config)
    config_id=digest(config_json)
    result={'processed':0,'cached':0,'missing':0,'errors':[],
            'frames_computed':0,'frames_cached':0,'configId':config_id}
    with connect(workspace) as db:
        for asset in db.execute('SELECT * FROM assets ORDER BY created_at,id').fetchall():
            aid=asset['id']
            if asset_ids is not None and aid not in asset_ids:
                continue
            if limit is not None and result['processed']>=limit:
                break
            job=db.execute('SELECT * FROM semantic_jobs WHERE asset_id=? AND config_id=?',
                           (aid,config_id)).fetchone()
            path=None
            try:
                path=resolve_source(db,asset)
                source_stat=path.stat()
                duration=int((asset['duration'] or 0)*1000)
                if duration<=0:
                    raise ValueError('A positive probed duration is required')
                if job is not None and completed_document(workspace,job) is not None:
                    activate(db,aid,provider.name,config_id)
                    db.commit()
                    result['cached']+=1
                    continue
                db.execute('''INSERT INTO semantic_jobs(asset_id,config_id,config_json,status)
                    VALUES (?,?,?,'running') ON CONFLICT(asset_id,config_id)
                    DO UPDATE SET status='running',error=NULL''',(aid,config_id,config_json))
                activate(db,aid,provider.name,config_id)
                db.commit()
                step=max(1,int(sample_seconds*1000),math.ceil(duration/max_frames))
                frames=[]
                for t in range(0,duration,step):
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
                        frame=frame_descriptor(provider,frame_png(path,t),t)
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
                if verified_digest(path)!=aid:
                    # Observations from a changing source must never be reused.
                    db.execute('DELETE FROM semantic_frames WHERE asset_id=? AND config_id=?',(aid,config_id))
                    db.commit()
                    raise OSError('Source changed during semantic extraction')
                doc={'schemaVersion':2,'assetId':aid,'configId':config_id,
                     'provider':provider.name,'modelVersion':provider.version,
                     'config':config,'sampleIntervalMs':step,'frames':frames}
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
    return result

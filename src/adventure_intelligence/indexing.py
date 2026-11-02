"""Read-only registration of footage in persistent folders (no video copied)."""
from __future__ import annotations
import json
from pathlib import Path
from .catalog import connect
from .ingest import iter_videos, quick_signature
from .media import probe
from .sources import verified_digest
from .workspace import init


def index_folder(source: Path, workspace: Path, *, activity: str = 'unknown',
                 dry_run: bool = False, allow_removable: bool = False,
                 progress=None) -> dict:
    source = source.expanduser().resolve()
    workspace = workspace.expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f'Source folder missing: {source}')
    if workspace == source or workspace.is_relative_to(source) or source.is_relative_to(workspace):
        raise ValueError('Index workspace cannot overlap source')
    if activity not in {'unknown','ski','bike','snorkeling'}:
        raise ValueError('Unsupported activity')
    # A removable card will vanish when ejected; default to verified import instead.
    if not allow_removable and str(source).startswith('/Volumes/'):
        raise ValueError('Read-only indexing of /Volumes is unsafe by default: use "klip ingest" '
                         'for verified copying, or explicitly --allow-removable')
    files = list(iter_videos(source))
    if dry_run:
        return {'mode':'index-dry-run','discovered':len(files), 'paths':[str(p) for p in files]}
    init(workspace)
    stats = {'mode':'zero-copy','discovered':len(files),'indexed':0,'skipped':0,
             'duplicates':0,'errors':[], 'asset_ids':[]}
    if progress is not None:
        progress.start('index', len(files))
    with connect(workspace) as db:
        for file_number, p in enumerate(files, 1):
            try:
                before=p.stat()
                sig=quick_signature(p)
                key=str(p)
                known=db.execute('''SELECT i.asset_id,i.size_bytes,i.mtime_ns,i.quick_signature,
                    a.stored_path,a.duration FROM imports i JOIN assets a ON a.id=i.asset_id
                    WHERE i.source_key=? ORDER BY i.imported_at DESC''',(key,)).fetchall()
                if progress is not None:
                    progress.note('index', f'[{file_number}/{len(files)}] hashing {p.name}')
                digest=verified_digest(p, progress=(
                    lambda done, total: progress.bytes('index-hash', done, total, p.name)
                    if progress is not None else None))
                if any(k['asset_id']==digest and Path(k['stored_path'])==p
                       and k['duration'] and k['duration']>0 for k in known):
                    if activity!='unknown':
                        db.execute("UPDATE assets SET activity=? WHERE id=? AND activity='unknown'",
                                   (activity,digest))
                    db.commit()
                    stats['asset_ids'].append(digest)
                    stats['skipped']+=1
                    if progress is not None:
                        progress.item('index', file_number, len(files), f'{p.name} unchanged')
                    continue
                found=db.execute('SELECT id,stored_path,duration FROM assets WHERE id=?',(digest,)).fetchone()
                if found:
                    stats['duplicates']+=1
                    stored=Path(found['stored_path'])
                    # If the original disappeared, refresh the reference to this valid path.
                    try:
                        valid = verified_digest(stored) == digest
                    except OSError:
                        valid = False
                    if not valid:
                        db.execute('UPDATE assets SET stored_path=? WHERE id=?',(str(p),digest))
                    if not found['duration'] or found['duration']<=0:
                        meta=probe(p)
                        if not meta.get('duration') or meta['duration']<=0:
                            raise ValueError('Video metadata has no positive duration')
                        db.execute('UPDATE assets SET duration=?,capture_time=?,metadata_json=? WHERE id=?',
                                   (meta.get('duration'),meta.get('capture_time'),json.dumps(meta),digest))
                else:
                    # Report unprobeable files rather than register a fake video
                    # with duration=0 that will fail every later pipeline stage.
                    meta=probe(p)
                    if not meta.get('duration') or meta['duration']<=0:
                        raise ValueError('Video metadata has no positive duration')
                    db.execute('''INSERT INTO assets
                        (id,sha256,stored_path,size_bytes,suffix,duration,capture_time,activity,metadata_json)
                        VALUES (?,?,?,?,?,?,?,?,?)''',
                        (digest,digest,str(p),before.st_size,p.suffix.lower(),
                         meta.get('duration'),meta.get('capture_time'),activity,json.dumps(meta)))
                db.execute('''INSERT OR REPLACE INTO imports
                    (source_key,asset_id,original_name,original_path,size_bytes,mtime_ns,quick_signature)
                    VALUES (?,?,?,?,?,?,?)''',
                    (key,digest,p.name,str(p),before.st_size,before.st_mtime_ns,sig))
                if activity!='unknown':
                    db.execute("UPDATE assets SET activity=? WHERE id=? AND activity='unknown'",
                               (activity,digest))
                db.commit()
                stats['asset_ids'].append(digest)
                stats['indexed']+=1
                if progress is not None:
                    progress.item('index', file_number, len(files), f'{p.name} indexed')
            except Exception as exc:
                db.rollback()
                stats['errors'].append({'path':str(p),'error':str(exc)})
                if progress is not None:
                    progress.item('index', file_number, len(files), f'{p.name} failed: {exc}')
    return stats

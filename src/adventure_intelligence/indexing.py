"""Read-only registration of footage in persistent folders (no video copied)."""
from __future__ import annotations
import json
import os
from pathlib import Path
from .catalog import connect
from .ingest import iter_videos, quick_signature, sha_file
from .media import probe
from .workspace import init


def index_folder(source: Path, workspace: Path, *, activity: str = 'unknown',
                 dry_run: bool = False, allow_removable: bool = False) -> dict:
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
             'duplicates':0,'errors':[]}
    with connect(workspace) as db:
        for p in files:
            try:
                before=p.stat()
                sig=quick_signature(p)
                key=str(p)
                known=db.execute('''SELECT i.asset_id,i.size_bytes,i.mtime_ns,i.quick_signature,
                    a.stored_path FROM imports i JOIN assets a ON a.id=i.asset_id
                    WHERE i.source_key=? ORDER BY i.imported_at DESC''',(key,)).fetchall()
                if any(k['size_bytes']==before.st_size and k['mtime_ns']==before.st_mtime_ns
                       and k['quick_signature']==sig and Path(k['stored_path']).is_file()
                       for k in known):
                    stats['skipped']+=1
                    continue
                digest=sha_file(p)
                after=p.stat()
                if before.st_size!=after.st_size or before.st_mtime_ns!=after.st_mtime_ns:
                    raise OSError(f'Source changed during fingerprinting: {p}')
                found=db.execute('SELECT id,stored_path FROM assets WHERE id=?',(digest,)).fetchone()
                if found:
                    stats['duplicates']+=1
                    stored=Path(found['stored_path'])
                    # If the original disappeared, refresh the reference to this valid path.
                    if not stored.is_file():
                        db.execute('UPDATE assets SET stored_path=? WHERE id=?',(str(p),digest))
                else:
                    try:
                        meta=probe(p)
                    except Exception as exc:
                        meta={'duration':0,'capture_time':None,'probe_error':str(exc)}
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
                stats['indexed']+=1
            except Exception as exc:
                db.rollback()
                stats['errors'].append({'path':str(p),'error':str(exc)})
    return stats

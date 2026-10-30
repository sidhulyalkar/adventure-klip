"""Checksum-verified extraction cache; commit records only after atomic writes."""
import json
from hashlib import sha256


def read_artifact(db, workspace, asset_id, version):
    row = db.execute('SELECT * FROM artifacts WHERE asset_id=? AND version=?',
                     (asset_id, version)).fetchone()
    if row is None:
        return None
    path = workspace/'features'/row['filename']
    if path.parent.resolve() != (workspace/'features').resolve():
        return None
    try:
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != row['sha256']:
            return None
        document = json.loads(raw)
        if (document['assetId'] != asset_id or document['version'] != version
                or not isinstance(document['series'], list)):
            return None
        return document
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_artifact(db, workspace, asset_id, version, series):
    filename = f'{asset_id}-{sha256(version.encode()).hexdigest()[:20]}.json'
    path = workspace/'features'/filename
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps({'assetId': asset_id, 'version': version, 'series': series},
                     separators=(',', ':'), allow_nan=False).encode()
    tmp = path.with_suffix('.tmp')
    try:
        with tmp.open('wb') as handle:
            import os
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    db.execute('''INSERT INTO artifacts(asset_id,version,filename,sha256) VALUES (?,?,?,?)
        ON CONFLICT(asset_id,version) DO UPDATE SET filename=excluded.filename,
        sha256=excluded.sha256''', (asset_id, version, filename, sha256(raw).hexdigest()))
    db.commit()

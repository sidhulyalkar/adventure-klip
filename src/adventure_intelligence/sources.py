"""Resolve content identities without trusting a mutable filename."""
from pathlib import Path
from .ingest import sha_file


def verified_digest(path: Path, progress=None) -> str:
    before = path.stat()
    digest = sha_file(path, progress=progress)
    after = path.stat()
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        raise OSError(f'Source changed during verification: {path}')
    return digest


def resolve_source(db, asset, progress=None) -> Path:
    candidates = [asset['stored_path']]
    candidates.extend(row[0] for row in db.execute(
        'SELECT original_path FROM imports WHERE asset_id=? ORDER BY original_path',
        (asset['id'],)))
    for name in dict.fromkeys(candidates):
        path = Path(name)
        try:
            check=(lambda done,total: progress.bytes('source-check', done, total, path.name)
                   if progress is not None else None)
            if verified_digest(path, progress=check) == asset['id']:
                return path
        except OSError:
            continue
    raise OSError(f"No available source matches content identity {asset['id']}; "
                  'restore the original or index the changed footage')

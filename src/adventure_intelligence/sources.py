"""Resolve content identities without trusting a mutable filename."""
from pathlib import Path
from .ingest import sha_file


def verified_digest(path: Path) -> str:
    before = path.stat()
    digest = sha_file(path)
    after = path.stat()
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        raise OSError(f'Source changed during verification: {path}')
    return digest


def resolve_source(db, asset) -> Path:
    candidates = [asset['stored_path']]
    candidates.extend(row[0] for row in db.execute(
        'SELECT original_path FROM imports WHERE asset_id=? ORDER BY original_path',
        (asset['id'],)))
    for name in dict.fromkeys(candidates):
        path = Path(name)
        try:
            if verified_digest(path) == asset['id']:
                return path
        except OSError:
            continue
    raise OSError(f"No available source matches content identity {asset['id']}; "
                  'restore the original or index the changed footage')

"""Non-destructive, idempotent import of SD-card footage to local storage."""
from __future__ import annotations
import hashlib
import os
import uuid
from pathlib import Path
from .workspace import asset_path, init
from .catalog import connect
from .media import probe

VIDEO_EXT = {".mp4", ".mov", ".m4v"}
SIDECAR_EXT = {".lrf", ".srt", ".thm", ".jpg", ".xml"}
CHUNK = 4 * 1024 * 1024


def sha_file(path: Path, progress=None) -> str:
    hasher = hashlib.sha256()
    total = path.stat().st_size
    done = 0
    with path.open("rb") as stream:
        for piece in iter(lambda: stream.read(CHUNK), b""):
            hasher.update(piece)
            done += len(piece)
            if progress is not None:
                progress(done, total)
    if progress is not None and total == 0:
        progress(0, 0)
    return hasher.hexdigest()


def quick_signature(path: Path) -> str:
    """Fast check of previously imported source, not proof of identical content."""
    h = hashlib.blake2b(digest_size=20)
    size = path.stat().st_size
    h.update(str(size).encode())
    with path.open("rb") as stream:
        h.update(stream.read(1024 * 1024))
        if size > 1024 * 1024:
            stream.seek(max(0, size - 1024 * 1024))
            h.update(stream.read(1024 * 1024))
    return h.hexdigest()


def copy_verified(src: Path, dest: Path, staging: Path) -> str:
    """Streaming copy plus destination read-back verification before atomic installation."""
    staging.mkdir(parents=True, exist_ok=True)
    tmp = staging / f"{uuid.uuid4().hex}.partial"
    expected = hashlib.sha256()
    try:
        with src.open("rb") as reader, tmp.open("xb") as writer:
            while block := reader.read(CHUNK):
                writer.write(block)
                expected.update(block)
            writer.flush()
            os.fsync(writer.fileno())
        if sha_file(tmp) != expected.hexdigest():
            raise IOError(f"Destination verification failed for {src}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            if sha_file(dest) != expected.hexdigest():
                raise IOError(f"Existing destination checksum mismatch: {dest}")
            tmp.unlink()
        else:
            os.replace(tmp, dest)  # Same filesystem: staging lives below workspace.
        return expected.hexdigest()
    finally:
        tmp.unlink(missing_ok=True)


def iter_videos(source: Path):
    for p in sorted(source.rglob("*")):
        if not p.is_symlink() and p.is_file() and p.suffix.lower() in VIDEO_EXT:
            yield p


def import_sidecars(source: Path, dst_dir: Path, staging: Path) -> int:
    count = 0
    for p in sorted(source.parent.iterdir()):
        if p.is_symlink() or not p.is_file() or p.stem.lower() != source.stem.lower():
            continue
        if p.suffix.lower() not in SIDECAR_EXT:
            continue
        dest = dst_dir / p.name
        copy_verified(p, dest, staging)
        count += 1
    return count


def ingest(source: Path, workspace: Path, *, activity: str = "unknown",
           dry_run: bool = False) -> dict:
    source = source.expanduser().resolve()
    workspace = workspace.expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"Input directory does not exist: {source}")
    if workspace == source or workspace.is_relative_to(source) or source.is_relative_to(workspace):
        raise ValueError("Workspace and source must not contain one another")
    if activity not in {"unknown", "ski", "bike", "snorkeling"}:
        raise ValueError("activity must be unknown, ski, bike or snorkeling")
    files = list(iter_videos(source))
    if dry_run:
        return {"discovered": len(files), "would_import": [str(f) for f in files],
                "mode": "dry-run"}
    init(workspace)
    stats = {"discovered":len(files),"imported":0,"skipped":0,"duplicates":0,
             "sidecars":0,"errors":[]}
    with connect(workspace) as db:
        for p in files:
            try:
                st = p.stat()
                signature = quick_signature(p)
                key = str(p)  # compare signature too: reused SD-card paths are common
                known = db.execute("""SELECT i.asset_id,i.size_bytes,i.mtime_ns,i.quick_signature,
                      a.stored_path FROM imports i JOIN assets a ON a.id=i.asset_id
                      WHERE i.source_key=? ORDER BY i.imported_at DESC""", (key,)).fetchall()
                verified = next((k for k in known if
                       k["size_bytes"]==st.st_size and k["mtime_ns"]==st.st_mtime_ns and
                       k["quick_signature"]==signature and
                       Path(k["stored_path"]).resolve().is_relative_to(workspace/"originals") and
                       Path(k["stored_path"]).is_file() and
                       sha_file(Path(k["stored_path"])) == k["asset_id"] and
                       sha_file(p) == k["asset_id"]), None)
                if verified is not None:
                    # A prior video import may have been interrupted before its sidecars.
                    stats["sidecars"] += import_sidecars(p, workspace/"sidecars"/verified["asset_id"],
                                                        workspace/"staging")
                    stats["skipped"] += 1
                    continue
                # Copy to content-addressed path using a temporary file.
                temp_path = workspace / "staging" / f"{uuid.uuid4().hex}.partial"
                temp_path.parent.mkdir(parents=True, exist_ok=True)
                h = hashlib.sha256()
                try:
                    with p.open("rb") as reader, temp_path.open("xb") as writer:
                        while block := reader.read(CHUNK):
                            h.update(block)
                            writer.write(block)
                        writer.flush()
                        os.fsync(writer.fileno())
                    digest = h.hexdigest()
                    after_stat = p.stat()
                    if after_stat.st_size != st.st_size or after_stat.st_mtime_ns != st.st_mtime_ns:
                        raise IOError(f"Source changed during import: {p}")
                    if sha_file(temp_path) != digest:
                        raise IOError(f"Copy verification failed: {p}")
                    dest = asset_path(workspace, digest, p.suffix)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    # Assets use the digest as id across file extensions.
                    existing = db.execute("SELECT stored_path FROM assets WHERE id=?", (digest,)).fetchone()
                    if existing:
                        # Indexing may point at a removable source. Import must install
                        # a verified managed copy even when the content is already known.
                        if dest.exists() and sha_file(dest) != digest:
                            raise IOError(f"Managed destination checksum mismatch: {dest}")
                        if not dest.exists():
                            os.replace(temp_path, dest)
                        target = dest
                        db.execute("UPDATE assets SET stored_path=? WHERE id=?", (str(dest),digest))
                        stats["duplicates"] += 1
                    else:
                        os.replace(temp_path, dest)
                        target = dest
                        try:
                            meta = probe(dest)
                        except Exception as exc:
                            # Preserve raw import, record the probe error, allow retry later.
                            meta = {"probe_error":str(exc),"duration":0,"capture_time":None}
                        import json
                        db.execute("""INSERT INTO assets
                          (id,sha256,stored_path,size_bytes,suffix,duration,capture_time,activity,metadata_json)
                          VALUES (?,?,?,?,?,?,?,?,?)""",
                          (digest,digest,str(dest),st.st_size,p.suffix.lower(),
                           meta.get("duration"),meta.get("capture_time"),activity,
                           json.dumps(meta,sort_keys=True)))
                    db.execute("""INSERT OR REPLACE INTO imports
                        (source_key,asset_id,original_name,original_path,size_bytes,mtime_ns,quick_signature)
                        VALUES (?,?,?,?,?,?,?)""",
                        (key,digest,p.name,str(p),st.st_size,st.st_mtime_ns,signature))
                    if activity!="unknown":
                        db.execute("UPDATE assets SET activity=? WHERE id=? AND activity='unknown'",
                                   (activity,digest))
                    stats["sidecars"] += import_sidecars(p, workspace/"sidecars"/digest,
                                                          workspace/"staging")
                    db.commit()
                    stats["imported"] += 1
                finally:
                    temp_path.unlink(missing_ok=True)
            except Exception as exc:
                stats["errors"].append({"file":str(p),"error":str(exc)})
                db.rollback()
    return stats


def looks_like_dji_card(volume: Path) -> bool:
    dcim = volume / "DCIM"
    if not dcim.is_dir() or volume.is_symlink():
        return False
    return any(p.name.upper().startswith("DJI_") for p in iter_videos(dcim))

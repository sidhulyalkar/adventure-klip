"""Virtual, read-only library tree with user-curated organization metadata.

No actual video files are moved or renamed. Capture date is provenance-aware:
ffprobe capture time first, recognizable date in filename second, otherwise
Unknown date. Regions/collections are only explicitly written by the user.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import re

from .catalog import connect
from .examples import latest_examples

_ACTIVITY={"","unknown","ski","bike","snorkeling"}
_FIELD_LIMITS={"region":90,"place":90,"collection":90,"note":240}
_DJI_DATE=re.compile(r"(?:^|[_-])((?:19|20)\d{6})(?:[_T-]?\d{6})?(?=[_.-]|$)")
_ISO_DATE=re.compile(r"^(\d{4})[-:/](\d{1,2})[-:/](\d{1,2})")


def _day(capture, filename):
    if isinstance(capture,str):
        m=_ISO_DATE.match(capture.strip())
        if m:
            try:
                return datetime(*map(int,m.groups())).date().isoformat(),"camera_metadata"
            except ValueError:
                pass
    m=_DJI_DATE.search(filename)
    if m:
        try:
            return datetime.strptime(m.group(1),"%Y%m%d").date().isoformat(),"filename_estimate"
        except ValueError:
            pass
    return None,"unknown"


def library_index(workspace:Path) -> dict:
    with connect(workspace) as db:
        records=[dict(x) for x in db.execute("""SELECT a.id,a.activity,a.capture_time,
                   a.stored_path,a.duration,a.size_bytes,
                   o.region,o.place,o.collection,o.activity_override,o.note,
                   (SELECT i.original_name FROM imports i WHERE i.asset_id=a.id
                     ORDER BY i.imported_at DESC LIMIT 1) AS original_name
                   FROM assets a LEFT JOIN asset_organization o ON o.asset_id=a.id
                   ORDER BY a.created_at,a.id""")]
        clips=latest_examples(db)
        counts={}
        for c in clips:
            entry=counts.setdefault(c["asset_id"],{"reviewed":0,"kept":0,"skipped":0})
            entry["reviewed"]+=1
            if c["decision"]=="confirmed" and c["highlight"]=="keep":
                entry["kept"]+=1
            if c["decision"]=="confirmed" and c["highlight"]=="skip":
                entry["skipped"]+=1
    items=[]
    for r in records:
        source=Path(r["stored_path"])
        title=r["original_name"] or source.name
        date,provenance=_day(r["capture_time"],title)
        activity=r["activity_override"] or r["activity"]
        stat=counts.get(r["id"],{"reviewed":0,"kept":0,"skipped":0})
        items.append({"assetId":r["id"],"title":title,"day":date,
            "dayProvenance":provenance,
            "sourceFolder":source.parent.name or "Root",
            "activity":activity,"indexedActivity":r["activity"],
            "activityReviewed":bool(r["activity_override"]),
            "region":r["region"] or None,"place":r["place"] or None,
            "collection":r["collection"] or None,"note":r["note"] or "",
            "durationMs":round((r["duration"] or 0)*1000),
            "sizeBytes":r["size_bytes"],**stat})
    return {"schemaVersion":1,"items":items,"total":len(items),
            "views":["day","region","activity","folder","collection"],
            "regionPolicy":"Regions and places are user-authored, never inferred from scene appearance",
            "fileSafety":"Virtual library only. Originals stay at indexed source paths."}


def organize_asset(workspace:Path, *, asset_id,region=None,place=None,
                   collection=None,activity=None,note=None) -> dict:
    if not isinstance(asset_id,str) or not re.fullmatch("[a-f0-9]{64}",asset_id):
        raise ValueError("Invalid asset ID")
    updates={"region":region,"place":place,"collection":collection,"note":note}
    for key,value in updates.items():
        if value is not None and (not isinstance(value,str)
             or len(value)>_FIELD_LIMITS[key] or "\x00" in value
             or any(ord(ch)<32 for ch in value)):
            raise ValueError("Invalid "+key)
    if activity is not None and activity not in _ACTIVITY:
        raise ValueError("Invalid reviewed activity")
    if not any(v is not None for v in (*updates.values(),activity)):
        raise ValueError("No organization fields supplied")
    with connect(workspace) as db:
        exists=db.execute("SELECT id FROM assets WHERE id=?",(asset_id,)).fetchone()
        if exists is None:
            raise ValueError("Video not found in indexed library")
        db.execute("INSERT OR IGNORE INTO asset_organization(asset_id) VALUES (?)",(asset_id,))
        for field,value in updates.items():
            if value is not None:
                db.execute("UPDATE asset_organization SET "+field+"=?,updated_at=CURRENT_TIMESTAMP WHERE asset_id=?",
                           (value.strip(),asset_id))
        if activity is not None:
            db.execute("UPDATE asset_organization SET activity_override=?,updated_at=CURRENT_TIMESTAMP WHERE asset_id=?",
                       (activity,asset_id))
    return {"status":"saved","assetId":asset_id,
            "note":"Catalog classification is unchanged; user metadata overrides are separate"}

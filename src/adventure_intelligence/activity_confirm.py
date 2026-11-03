"""Explicit human-reviewed, single-asset activity confirmation with backup.

Only user-confirmed recordings can become activity labels through this command.
Refinement candidates, adjacent-recording dates and blue-water cues do not
automatically influence the indexed activity. Source files are immutable.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from .catalog import connect
from .sources import resolve_source
from .locking import lock_workspace


def confirm_activity(workspace:Path,asset_id:str,activity:str,*,
                     reviewed:bool=False,apply:bool=False)->dict:
    if (not isinstance(asset_id,str) or len(asset_id)!=64
            or any(c not in "0123456789abcdef" for c in asset_id)):
        raise ValueError("A full lowercase SHA-256 asset ID is required")
    if activity not in ("snorkeling","ski","bike"):
        raise ValueError("Confirm only supported activity labels: snorkeling, ski, bike")
    workspace=Path(workspace).expanduser().resolve()
    with connect(workspace) as db:
        row=db.execute("SELECT * FROM assets WHERE id=?",(asset_id,)).fetchone()
        if row is None:
            raise ValueError("Asset not indexed")
        previous=row["activity"]
        if previous not in ("unknown",activity):
            raise ValueError("Already labeled as a different activity; refuse overwrite")
        result={"assetId":asset_id,"basename":Path(row["stored_path"]).name,
                "oldActivity":previous,"proposedActivity":activity,
                "reviewedByUser":reviewed,"applied":False,
                "videoSourceModified":False,"backup":None}
        if not apply:
            return result
        if not reviewed:
            raise ValueError("Use --reviewed after visually checking the source recording")
    stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup=workspace/"reports"/f"catalog.before-manual-activity-{stamp}.sqlite3"
    backup.parent.mkdir(parents=True,exist_ok=True)
    with lock_workspace(workspace):
        with connect(workspace) as db:
            current=db.execute("SELECT * FROM assets WHERE id=?",(asset_id,)).fetchone()
            if current is None or current["activity"]!=previous:
                raise ValueError("Catalog activity changed since preflight; no writes")
            # Verify the exact indexed bytes before accepting a human label.
            resolved=resolve_source(db,current)
            if previous==activity:
                result["alreadyCorrect"]=True
                return result
            with sqlite3.connect(backup) as destination:
                db.backup(destination)
            result["backup"]=str(backup)
            changed=db.execute(
                "UPDATE assets SET activity=? WHERE id=? AND activity='unknown'",
                (activity,asset_id)).rowcount
            if changed!=1:
                raise RuntimeError("Atomic activity update failed")
            db.commit()
            result["applied"]=True
            result["verifiedSourcePath"]=str(resolved)
    audit=workspace/"reports"/f"activity-reviewed-{stamp}.json"
    with audit.open("x",encoding="utf-8") as handle:
        handle.write(json.dumps(result,sort_keys=True,indent=2)+"\n")
    result["audit"]=str(audit)
    return result

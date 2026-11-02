"""Cheap first-pass video evidence and candidate temporal event detection."""
from __future__ import annotations
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
import math
from .catalog import add_events, all_events, connect, put_episodes
from .episodes import build_episodes
from .media import motion_series
from .plugins import detector_named

MOTION_VERSION = "pixel-change-v1-fps2-64x36"
EPISODE_VERSION = "episodes-v1"


def detect_motion_events(asset_id: str, series: list[dict]) -> list[dict]:
    if not series:
        return []
    vals = sorted(s["change"] for s in series)
    base = median(vals)
    p90 = vals[int((len(vals)-1)*0.9)]
    threshold = max(0.045,base*2.0,p90*0.85)
    high = [s for s in series if s["change"]>=threshold]
    if not high:
        return []
    # Merge burst points separated by at most two seconds.
    groups: list[list[dict]]=[]
    for s in high:
        if not groups or s["t_ms"]-groups[-1][-1]["t_ms"]>2000:
            groups.append([s])
        else:
            groups[-1].append(s)
    events=[]
    for points in groups:
        start=max(0,points[0]["t_ms"]-500)
        end=points[-1]["t_ms"]+500
        if end<=start:
            continue
        strength=max(p["change"] for p in points)
        score=min(1.0,strength/max(threshold*2,0.1))
        event_id="ev-"+sha256(f"{asset_id}|{MOTION_VERSION}|{start}|{end}".encode()).hexdigest()[:20]
        events.append({"id":event_id,"kind":"visual_activity","start_ms":start,
                       "end_ms":end,"confidence":round(score,4),"score":round(score,4),
                       "evidence":{"peak_pixel_change":strength,"threshold":threshold,
                                   "disclaimer":"Camera motion; not verified action"}})
    return events


def analyze(workspace: Path, limit: int | None = None, force: bool = False,
            detector_name: str = "baseline") -> dict:
    detector=detector_named(detector_name)
    from .vision import FLOW_VERSION
    series_version = FLOW_VERSION if detector.name == "flow" else MOTION_VERSION
    stage = "flow" if detector.name == "flow" else "motion"
    version_id=f"{series_version}|{detector.name}:{detector.version}"
    result={"analyzed":0,"cached":0,"failed":[]}
    with connect(workspace) as db:
        assets=list(db.execute("SELECT * FROM assets ORDER BY created_at,id"))
        for row in assets:
            if limit is not None and result["analyzed"]>=limit:
                break
            asset_id=row["id"]
            version=db.execute("SELECT status,version FROM analyses WHERE asset_id=? AND stage=?",
                               (asset_id,stage)).fetchone()
            if not force and version and version["status"]=="completed" and version["version"]==version_id:
                result["cached"]+=1
                continue
            try:
                path=Path(row["stored_path"])
                if not path.is_file():
                    raise FileNotFoundError(f"Missing original: {path}")
                extractor = getattr(detector, "extract", motion_series)
                series=extractor(path)
                features=workspace/"features"/f"{asset_id}-{series_version}.json"
                features.parent.mkdir(parents=True,exist_ok=True)
                tmp=features.with_suffix(".tmp")
                tmp.write_text(json.dumps({"assetId":asset_id,"version":series_version,
                                           "series":series},separators=(",",":")))
                tmp.replace(features)
                evs=detector.detect(asset_id,series,json.loads(row["metadata_json"]))
                seen_ids: set[str] = set()
                duration_ms=int((row["duration"] or 0)*1000)
                for ev in evs:
                    if (not isinstance(ev.get("id"), str) or not ev["id"] or ev["id"] in seen_ids
                        or not isinstance(ev.get("kind"),str) or not ev["kind"]
                        or not all(isinstance(ev.get(key), (float,int)) and
                            math.isfinite(ev[key]) for key in ("start_ms","end_ms","score","confidence"))
                        or ev["start_ms"] < 0 or ev["end_ms"] <= ev["start_ms"]
                        or (duration_ms and ev["end_ms"] > duration_ms+1500)
                        or not (0 <= ev["confidence"] <= 1 and 0 <= ev["score"] <= 1)):

                        raise ValueError(f"Invalid detector event: {ev}")
                    seen_ids.add(ev["id"])
                add_events(db,asset_id,evs,stage,version_id)
                stored=all_events(db,asset_id)
                put_episodes(db,asset_id,build_episodes(asset_id,stored,
                             int((row["duration"] or 0)*1000)),EPISODE_VERSION)
                db.execute("""INSERT INTO analyses(asset_id,stage,version,status,error)
                    VALUES (?,?,?,'completed',NULL)
                    ON CONFLICT(asset_id,stage) DO UPDATE SET version=excluded.version,
                    status='completed',error=NULL,updated_at=CURRENT_TIMESTAMP""",
                    (asset_id,stage,version_id))
                db.commit()
                result["analyzed"]+=1
            except Exception as exc:
                db.rollback()
                db.execute("""INSERT INTO analyses(asset_id,stage,version,status,error)
                    VALUES (?,?,?,'failed',?)
                    ON CONFLICT(asset_id,stage) DO UPDATE SET status='failed',error=excluded.error,
                    updated_at=CURRENT_TIMESTAMP""",
                    (asset_id,stage,version_id,str(exc)))
                db.commit()
                result["failed"].append({"assetId":asset_id,"error":str(exc)})
    return result

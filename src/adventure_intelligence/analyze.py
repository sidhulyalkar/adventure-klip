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
from .sources import resolve_source, verified_digest
from .artifacts import read_artifact, write_artifact

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
            detector_name: str = "baseline", asset_ids: set[str] | None = None,
            progress=None) -> dict:
    detector=detector_named(detector_name)
    from .vision import FLOW_VERSION
    series_version = FLOW_VERSION if detector.name == "flow" else MOTION_VERSION
    stage = "flow" if detector.name == "flow" else "motion"
    # Custom extractors conservatively invalidate on their detector version.
    if detector.name not in {'baseline', 'flow'} and hasattr(detector, 'extract'):
        series_version = f"custom:{detector.name}:{detector.version}"
    version_id=f"{series_version}|{detector.name}:{detector.version}|{EPISODE_VERSION}|cache-v2"
    result={"analyzed":0,"cached":0,"extracted":0,"features_cached":0,"failed":[]}
    with connect(workspace) as db:
        assets=list(db.execute("SELECT * FROM assets ORDER BY created_at,id"))
        assets=[r for r in assets if asset_ids is None or r["id"] in asset_ids]
        if progress is not None:
            progress.start("analysis", len(assets))
        for file_number, row in enumerate(assets, 1):
            if asset_ids is not None and row["id"] not in asset_ids:
                continue
            if limit is not None and result["analyzed"]>=limit:
                break
            asset_id=row["id"]
            version=db.execute("SELECT status,version FROM analyses WHERE asset_id=? AND stage=?",
                               (asset_id,stage)).fetchone()
            try:
                if progress is not None:
                    progress.note("analysis", f"[{file_number}/{len(assets)}] checking {asset_id[:12]}")
                if not row['duration'] or row['duration'] <= 0:
                    raise ValueError('A positive probed duration is required before analysis')
                path=resolve_source(db, row, progress=progress)
                document = None if force else read_artifact(db, workspace, asset_id, series_version)
                if (document is not None and version and version["status"]=="completed"
                        and version["version"]==version_id):
                    result["cached"]+=1
                    if progress is not None:
                        progress.item("analysis", file_number, len(assets), f"{path.name} cached")
                    continue
                db.execute("""INSERT INTO analyses(asset_id,stage,version,status,error)
                    VALUES (?,?,?,'running',NULL)
                    ON CONFLICT(asset_id,stage) DO UPDATE SET version=excluded.version,
                    status='running',error=NULL,updated_at=CURRENT_TIMESTAMP""",
                    (asset_id,stage,version_id))
                db.commit()
                if document is None:
                    extractor = getattr(detector, "extract", motion_series)
                    if progress is not None and detector.name in {"flow", "baseline"}:
                        stage_name=f"{detector.name}:{path.name}"
                        progress.start(stage_name, round(float(row["duration"]) * (4 if detector.name=="flow" else 2)))
                        series=extractor(path,total_seconds=float(row["duration"]),
                                         progress=lambda done, total: progress.frames(stage_name, done, total, path.name))
                    else:
                        series=extractor(path)
                    if progress is not None:
                        progress.note('analysis', f'verifying {path.name} after decoding')
                    verification=(lambda done,total: progress.bytes('source-check', done, total, path.name)
                                  if progress is not None else None)
                    if verified_digest(path, progress=verification) != asset_id:
                        raise OSError('Source changed during extraction')
                    write_artifact(db,workspace,asset_id,series_version,series)
                    result['extracted']+=1
                else:
                    series=document['series']
                    result['features_cached']+=1
                evs=detector.detect(asset_id,series,json.loads(row["metadata_json"]))
                seen_ids: set[str] = set()
                duration_ms=int((row["duration"] or 0)*1000)
                if detector.name in {'baseline', 'flow'}:
                    # Built-in sample windows can extend past the final source frame.
                    for ev in evs:
                        ev['end_ms'] = min(ev['end_ms'], duration_ms)
                    evs = [ev for ev in evs if ev['start_ms'] < ev['end_ms']]
                for ev in evs:
                    if (not isinstance(ev.get("id"), str) or not ev["id"] or ev["id"] in seen_ids
                        or not isinstance(ev.get("kind"),str) or not ev["kind"]
                        or not all(isinstance(ev.get(key), (float,int)) and
                            math.isfinite(ev[key]) for key in ("start_ms","end_ms","score","confidence"))
                        or ev["start_ms"] < 0 or ev["end_ms"] <= ev["start_ms"]
                        or ev["end_ms"] > duration_ms
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
                if progress is not None:
                    progress.item("analysis", file_number, len(assets), f"{path.name} analyzed")
            except Exception as exc:
                db.rollback()
                db.execute("""INSERT INTO analyses(asset_id,stage,version,status,error)
                    VALUES (?,?,?,'failed',?)
                    ON CONFLICT(asset_id,stage) DO UPDATE SET version=excluded.version,status='failed',error=excluded.error,
                    updated_at=CURRENT_TIMESTAMP""",
                    (asset_id,stage,version_id,str(exc)))
                db.commit()
                result["failed"].append({"assetId":asset_id,"error":str(exc)})
                if progress is not None:
                    progress.item("analysis", file_number, len(assets), f"{asset_id[:12]} failed: {exc}")
    return result

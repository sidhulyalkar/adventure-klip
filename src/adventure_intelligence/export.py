"""Versioned, privacy-minimal export format for web adapters."""
from __future__ import annotations
import json
from pathlib import Path
from urllib.parse import urlparse
from .catalog import connect


def _public_url(url: str) -> bool:
    p=urlparse(url)
    return p.scheme=="https" and bool(p.netloc) and not p.username and not p.password


def export_manifest(workspace: Path, output: Path, media_map: Path | None = None) -> dict:
    """Only approved video links become playable. No local paths or camera GPS leak."""
    mapping=json.loads(media_map.read_text()) if media_map else {}
    if not isinstance(mapping,dict):
        raise ValueError("media-map must be a JSON object keyed by asset id")
    with connect(workspace) as db:
        approved=db.execute("""SELECT b.*,e.asset_id,e.kind,e.start_ms,e.end_ms,
           e.confidence, a.activity FROM bindings b JOIN events e ON e.id=b.event_id
           JOIN assets a ON a.id=e.asset_id WHERE b.review_state='approved'
           ORDER BY b.world_entity_id,b.id""").fetchall()
        episodes=list(db.execute("""SELECT e.id,e.asset_id,e.kind,e.start_ms,e.end_ms,e.score,
           e.event_ids_json,a.activity FROM episodes e JOIN assets a ON a.id=e.asset_id
           ORDER BY e.asset_id,e.start_ms"""))
    published=[]
    missing=[]
    for b in approved:
        asset=b["asset_id"]
        media=mapping.get(asset,{})
        if not isinstance(media,dict) or not _public_url(media.get("src", "")) or not _public_url(media.get("poster", "")):
            missing.append({"binding":b["id"],"assetId":asset,"reason":"missing approved HTTPS media URLs"})
            continue
        published.append({"id":b["id"],"worldEntityId":b["world_entity_id"],
                          "sourceAssetId":asset,"activity":b["activity"],
                          "eventKind":b["kind"],"matchType":b["match_type"],
                          "startMs":b["start_ms"],"endMs":b["end_ms"],
                          "src":media["src"],"posterSrc":media["poster"]})
    doc={"schemaVersion":1,"generator":"adventure-intelligence/0.1.0",
         "worldBindings":published,"unpublished":missing,
         "episodes":[{"id":e["id"],"assetId":e["asset_id"],"kind":e["kind"],
                      "startMs":e["start_ms"],"endMs":e["end_ms"],"score":e["score"],
                      "activity":e["activity"],"eventIds":json.loads(e["event_ids_json"])}
                     for e in episodes]}
    output.parent.mkdir(parents=True,exist_ok=True)
    temp=output.with_suffix(output.suffix+".tmp")
    temp.write_text(json.dumps(doc,indent=2)+"\n",encoding="utf-8")
    temp.replace(output)
    return {"output":str(output),"publishedBindings":len(published),
            "missingMedia":len(missing),"episodes":len(episodes)}

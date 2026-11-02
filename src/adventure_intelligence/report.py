"""Static local review report with source video fragment links (no web service needed)."""
from __future__ import annotations
from collections import Counter
from html import escape
from pathlib import Path
import os
from .catalog import connect

CSS="""
:root{color-scheme:dark}body{font-family:system-ui,-apple-system,sans-serif;background:#0a111c;color:#ecf4ff;max-width:1050px;margin:40px auto;padding:0 20px}
h1{font-size:2.1rem;font-weight:500;letter-spacing:-.03em}small,.muted{color:#96aabb}article{background:#111f2e;border:1px solid #283b4d;border-radius:14px;padding:20px;margin:16px 0}
.bar{height:9px;background:#263646;border-radius:20px;overflow:hidden;margin-top:8px}.fill{height:100%;background:#60a6c6;border-radius:20px}
a{color:#85cee9;text-decoration:none}a:hover{text-decoration:underline}.tag{border:1px solid #395165;border-radius:999px;font-size:.74rem;padding:3px 9px}
.header{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}.pill{color:#92bdd0;font-size:.8rem}p{line-height:1.6}
"""


def write_report(workspace: Path, output: Path) -> dict:
    with connect(workspace) as db:
        assets=[dict(x) for x in db.execute("SELECT * FROM assets ORDER BY created_at,id")]
        eps=[dict(x) for x in db.execute("SELECT * FROM episodes ORDER BY score DESC,start_ms")]
        ev_counts=dict(db.execute("SELECT asset_id,COUNT(*) FROM events GROUP BY asset_id").fetchall())
    by_asset={}
    for e in eps:
        by_asset.setdefault(e["asset_id"],[]).append(e)
    lines=["<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width, initial-scale=1'>",
           "<title>Adventure Intelligence | Local Review</title>",
           f"<style>{CSS}</style></head><body><header><h1>Adventure Intelligence</h1>",
           "<p class='muted'>Local-only analysis review · Visual motion signals are not verified athletic maneuvers.</p></header>"]
    for asset in assets:
        asset_id=asset["id"]
        activity=escape(asset["activity"])
        path=Path(asset["stored_path"])
        relative=Path(os.path.relpath(path, output.parent)).as_posix()
        link=escape(relative,quote=True)
        lines.extend(["<article>","<div class='header'>",
                      f"<strong>{activity.title()} · {escape(asset_id[:12])}</strong>",
                      f"<span class='tag'>{float(asset['duration'] or 0):.1f}s · {ev_counts.get(asset_id,0)} events</span>",
                      "</div><p><a href='"+link+"'>Open original video</a></p>"])
        for e in by_asset.get(asset_id,[]):
            dur=float(asset["duration"] or 0)
            frac=min(100,max(0,100*(e["end_ms"]-e["start_ms"])/max(1,dur*1000)))
            begin=e["start_ms"]/1000;end=e["end_ms"]/1000
            title=escape(e["kind"].replace("_"," ").title())
            lines.extend(["<div class='header'>",
                          f"<span>{title}</span><span class='pill'>{begin:.1f}–{end:.1f}s · score {e['score']:.2f}</span></div>",
                          f"<div class='bar'><div class='fill' style='width:{frac:.2f}%'></div></div>",
                          f"<p><a href='{link}#t={begin:.2f},{end:.2f}'>Review episode</a></p>"])
        lines.append("</article>")
    if not assets:
        lines.append("<article>No assets yet. Import DJI video files to begin.</article>")
    lines.append("</body></html>")
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text("\n".join(lines),encoding="utf-8")
    return {"report":str(output),"assets":len(assets),"episodes":len(eps)}

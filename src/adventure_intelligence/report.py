"""Static local review report with source video fragment links (no web service needed)."""
from __future__ import annotations
from collections import Counter
from html import escape
from pathlib import Path
import os
from .catalog import connect
from .safety import protect_source_output
from .editorial_quality import editorial_candidates,select_diverse

CSS="""
:root{color-scheme:dark}body{font-family:system-ui,-apple-system,sans-serif;background:#0a111c;color:#ecf4ff;max-width:1050px;margin:40px auto;padding:0 20px}
h1{font-size:2.1rem;font-weight:500;letter-spacing:-.03em}small,.muted{color:#96aabb}article{background:#111f2e;border:1px solid #283b4d;border-radius:14px;padding:20px;margin:16px 0}
.bar{height:9px;background:#263646;border-radius:20px;overflow:hidden;margin-top:8px;position:relative}.fill{height:100%;background:#60a6c6;border-radius:20px;position:absolute}
a{color:#85cee9;text-decoration:none}a:hover{text-decoration:underline}.tag{border:1px solid #395165;border-radius:999px;font-size:.74rem;padding:3px 9px}
.header{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}.pill{color:#92bdd0;font-size:.8rem}p{line-height:1.6}
"""


def write_report(workspace: Path, output: Path) -> dict:
    with connect(workspace) as db:
        protect_source_output(db, output)
        assets=[dict(x) for x in db.execute("SELECT * FROM assets ORDER BY created_at,id")]
        eps=[dict(x) for x in db.execute("SELECT * FROM episodes ORDER BY score DESC,start_ms")]
        ev_counts=dict(db.execute("SELECT asset_id,COUNT(*) FROM events GROUP BY asset_id").fetchall())
        proposals,_=editorial_candidates(db,workspace,
            [e for e in eps if e['kind']!='recording_summary'])
    suggested={}
    for asset in assets:
        choices=[x for x in proposals if x['sourceAssetId']==asset['id']]
        suggested[asset['id']]=select_diverse(choices,max_seconds=75,max_per_asset=3)
    by_asset={}
    for e in eps:
        by_asset.setdefault(e["asset_id"],[]).append(e)
    lines=["<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>",
           "<meta name='viewport' content='width=device-width, initial-scale=1'>",
           "<title>Adventure Klip | Local Review</title>",
           f"<style>{CSS}</style></head><body><header><h1>Adventure Klip</h1>",
           "<p class='muted'>Local-only analysis review · Visual motion signals are not verified athletic maneuvers.</p></header>"]
    for asset in assets:
        asset_id=asset["id"]
        activity=escape(asset["activity"])
        path=Path(asset["stored_path"])
        relative=Path(os.path.relpath(path, output.parent)).as_posix()
        link=escape(relative,quote=True)
        lines.extend(["<article>","<div class='header'>",
                      f"<strong>{activity.title()} · {escape(path.name)} · {escape(asset_id[:12])}</strong>",
                      f"<span class='tag'>{float(asset['duration'] or 0):.1f}s · {ev_counts.get(asset_id,0)} events</span>",
                      "</div><p><a href='"+link+"'>Open original video</a></p>"])
        picks=suggested.get(asset_id,[])
        if picks:
            lines.append("<h3>Suggested short highlights <small>· review required · heuristic scores</small></h3>")
        for pick in picks:
            start=pick['startMs']/1000;end=pick['endMs']/1000
            reasons=', '.join(pick.get('reasonCodes',[])).replace('_',' ')
            continuity=pick.get('underwaterContinuity')
            underwater=(f" · underwater continuity {100*continuity['supportedFraction']:.0f}% (unverified)"
                        if isinstance(continuity,dict) and
                           continuity.get('intervalCount') else "")
            sound=pick.get('acousticEvidence',{})
            acoustic=(f" · {sound.get('transientCount',0)} audio transients"
                      if sound.get('available') else "")
            lines.extend([
                "<div class='header'>",
                f"<span>{escape(pick['kind'].replace('_',' ').title())}</span>",
                f"<span class='pill'>{start:.1f}–{end:.1f}s · editorial {pick['score']:.2f}</span>",
                "</div>",
                f"<p class='muted'>{escape(reasons)}{escape(underwater)}{escape(acoustic)}</p>",
                f"<p><a href='{link}#t={start:.2f},{end:.2f}'>Review suggested clip</a></p>"
            ])
        lines.append("<details><summary>All raw motion and event proposals</summary>")
        for e in by_asset.get(asset_id,[]):
            dur=float(asset["duration"] or 0)
            frac=min(100,max(0,100*(e["end_ms"]-e["start_ms"])/max(1,dur*1000)))
            left=min(100,max(0,100*e["start_ms"]/max(1,dur*1000)))
            begin=e["start_ms"]/1000;end=e["end_ms"]/1000
            title=escape(e["kind"].replace("_"," ").title())
            lines.extend(["<div class='header'>",
                          f"<span>{title}</span><span class='pill'>{begin:.1f}–{end:.1f}s · score {e['score']:.2f}</span></div>",
                          f"<div class='bar'><div class='fill' style='left:{left:.2f}%;width:{frac:.2f}%'></div></div>",
                          f"<p><a href='{link}#t={begin:.2f},{end:.2f}'>Review episode</a></p>"])
        lines.append("</details></article>")
    if not assets:
        lines.append("<article>No assets yet. Import DJI video files to begin.</article>")
    lines.append("</body></html>")
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text("\n".join(lines),encoding="utf-8")
    return {"report":str(output),"assets":len(assets),"episodes":len(eps)}

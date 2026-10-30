"""Sequence-aware edit ordering; distinguish chronology and cinematic progression.

The editor never relabels action from optical flow. Each selection retains source spans.
"""
from __future__ import annotations
from datetime import datetime
from .reels import plan_reel
from .catalog import connect
from pathlib import Path


def _date(raw:str|None)->datetime|None:
    if not raw:
        return None
    try:
        dt=datetime.fromisoformat(raw.replace('Z','+00:00'))
        return dt if dt.tzinfo else None
    except ValueError:
        return None


def plan_storyboard(workspace:Path,recipe:str='best-sequences',max_seconds:int=75,
                    style:str='chronological')->dict:
    if style not in ('chronological','crescendo'):
        raise ValueError('style must be chronological or crescendo')
    document=plan_reel(workspace,recipe,max_seconds)
    with connect(workspace) as db:
        dates={r['id']:_date(r['capture_time']) for r in db.execute('SELECT id,capture_time FROM assets')}
    items=document['segments']
    # Within an asset, preserve source order regardless of visual score.
    if style=='chronological':
        items.sort(key=lambda x:(dates.get(x['sourceAssetId']) is None,
            dates.get(x['sourceAssetId']).isoformat() if dates.get(x['sourceAssetId']) else '',
            x['sourceAssetId'],x['startMs']))
    else:
        # Across independent assets, place lower scores first. Keep each source's
        # local order by grouping its sequences as one ordered block.
        groups:dict[str,list[dict]]={}
        for item in items:
            groups.setdefault(item['sourceAssetId'],[]).append(item)
        for group in groups.values():
            group.sort(key=lambda x:x['startMs'])
        items[:]=[item for _,group in sorted(groups.items(),
            key=lambda x:(sum(y['score'] for y in x[1])/len(x[1]),x[0])) for item in group]
    document['editor']={'version':'storyboard-v1','style':style,
       'selection':'episode scores, non-overlap, duration budget',
       'ordering':'verified chronology when available' if style=='chronological'
                  else 'rising quality across source groups, source-local order preserved',
       'limitations':['No proof of athlete maneuver from camera motion',
                      'No claim of physical continuity between separate recordings']}
    return document

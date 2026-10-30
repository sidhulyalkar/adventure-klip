"""Sequence-aware edit ordering; distinguish chronology and cinematic progression.

The editor never relabels action from optical flow. Each selection retains source spans.
"""
from __future__ import annotations
from datetime import datetime
from .reels import plan_reel, RECIPES
from .editorial_quality import editorial_candidates, select_diverse, VERSION as RANK_VERSION
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
                    style:str='chronological',ranking:str='legacy',
                    context_policy:str='suggest')->dict:
    if style not in ('chronological','crescendo'):
        raise ValueError('style must be chronological or crescendo')
    if ranking not in ('legacy', 'context'):
        raise ValueError('ranking must be legacy or context')
    if max_seconds < 1:
        raise ValueError('max_seconds must be positive')
    if ranking == 'context':
        if recipe not in RECIPES:
            raise ValueError('Unknown recipe')
        rule = RECIPES[recipe]
        with connect(workspace) as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM episodes ORDER BY asset_id,start_ms')]
            activity={r['id']:r['activity'] for r in db.execute('SELECT id,activity FROM assets')}
            rows=[r for r in rows if r['kind'] in rule['kinds']
                  and (rule['activity'] is None or activity.get(r['asset_id'])==rule['activity'])]
            candidates, rejected=editorial_candidates(db, workspace, rows,
                                                       policy=context_policy)
        for candidate in candidates:
            candidate['score']=round(candidate['score']*
                                     rule['kinds'].get(candidate['kind'],0),4)
        chosen=select_diverse(candidates,max_seconds=max_seconds)
        document={'schemaVersion':1,'recipe':recipe,'maxSeconds':max_seconds,
                  'totalSeconds':round(sum(c['duration'] for c in chosen),3),
                  'segments':chosen,'status':'draft',
                  'quality':{'version':RANK_VERSION,'contextPolicy':context_policy,
                             'proposals':len(candidates),'excluded':rejected,
                             'warning':'Unverified scene labels require review before publication'}}
    else:
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
    document['editor']={'version':'storyboard-v2' if ranking=='context' else 'storyboard-v1',
       'ranking':ranking,'style':style,
       'selection':'episode scores, non-overlap, duration budget',
       'ordering':'verified chronology when available' if style=='chronological'
                  else 'rising quality across source groups, source-local order preserved',
       'limitations':['No proof of athlete maneuver from camera motion',
                      'No claim of physical continuity between separate recordings']}
    return document

import json
import shutil
import subprocess
import pytest
from pathlib import Path
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.catalog import connect
from adventure_intelligence.timeline import timeline_from_series,generate_timelines
from adventure_intelligence.semantic import SceneStack,describe_asset
from adventure_intelligence.storyboard import plan_storyboard
from adventure_intelligence.analyze import analyze
from adventure_intelligence.curation import import_labels
from adventure_intelligence.reels import render_reel


def test_timeline_local_calibration():
    x=timeline_from_series('a',4500,[{'t_ms':800,'change':0.2},
         {'t_ms':1800,'change':0.9},{'t_ms':3200,'change':0.2}])
    assert len(x['bins'])==3
    assert x['calibration']['comparableAcrossVideos'] is False
    assert x['bins'][0]['importance']['heuristic'] is not None
    assert x['bins'][2]['importance']['model'] is None
    assert x['bins'][0]['events']==[]


def test_semantic_stack_composition():
    class Stub:
        name='stub';version='1'
        def describe(self,image_bytes):
            return {'embedding':[0.1,0.2],'objects':[{'label':'tree','score':0.6}],
                    'keypoints':[{'x':0.1,'y':0.5}],'notes':['proposed']}
    info=SceneStack([Stub()]).describe(b'x')
    assert len(info['embedding'])==2 and len(info['objects'])==1
    assert len(info['keypoints'])==1


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='ffmpeg missing')
def test_readonly_index_timeline_and_crossfade(tmp_path):
    source=tmp_path/'DJI_RAW';source.mkdir()
    video=source/'DJI_0001.MP4'
    subprocess.run(['ffmpeg','-loglevel','error','-y','-f','lavfi','-i',
                    'testsrc2=size=160x90:rate=12','-t','6','-c:v','libx264',str(video)],check=True)
    workspace=tmp_path/'work'
    indexed=index_folder(source,workspace,activity='ski')
    assert indexed['indexed']==1
    assert index_folder(source,workspace)['skipped']==1
    assert video.exists()
    with connect(workspace) as db:
        aid=db.execute('SELECT id FROM assets').fetchone()[0]
        assert db.execute('SELECT stored_path FROM assets').fetchone()[0]==str(video)
    assert analyze(workspace)['analyzed']==1
    timeline=generate_timelines(workspace)
    assert timeline['generated']==1
    assert len(json.loads(Path(timeline['paths'][0]).read_text())['bins'])>=2
    labels={'asset_id':aid,'events':[{'kind':'jump','start_ms':300,'end_ms':1750},
                  {'kind':'jump','start_ms':2550,'end_ms':4200}]}
    lab=tmp_path/'labels.json';lab.write_text(json.dumps(labels))
    import_labels(workspace,lab)
    plan=plan_storyboard(workspace,'ski-highlights',15,'chronological')
    assert plan['editor']['version']=='storyboard-v1'
    assert plan['segments']
    # Also test fade on two separate, temporally valid clips.
    segments=[{'sourceAssetId':aid,'startMs':100,'endMs':1900},
              {'sourceAssetId':aid,'startMs':2700,'endMs':5000}]
    output=tmp_path/'crossfade.mp4'
    result=render_reel(workspace,{'schemaVersion':1,'segments':segments},output,480,'fade',0.25)
    assert result['transition']=='fade'
    assert output.stat().st_size>1000
    assert video.exists()


def test_index_removable_guard(tmp_path):
    # On macOS, /Volumes is protected by the default indexing guard.
    from adventure_intelligence.indexing import index_folder
    with pytest.raises(ValueError):
        index_folder(Path('/Volumes/FAKE'),tmp_path)

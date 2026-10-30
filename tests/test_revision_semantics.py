import json
import importlib
import sqlite3
from pathlib import Path
import pytest
from adventure_intelligence.catalog import connect, add_events
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.curation import bind_event
from adventure_intelligence.history import event_history
from adventure_intelligence.semantic_jobs import run_semantics, completed_document
from adventure_intelligence.export import export_manifest


@pytest.fixture
def setup(tmp_path,monkeypatch):
    folder=tmp_path/'source'; folder.mkdir()
    video=folder/'sample.mp4'; video.write_bytes(b'original')
    work=tmp_path/'work'
    monkeypatch.setattr('adventure_intelligence.indexing.probe',lambda p:{'duration':3})
    aid=index_folder(folder,work)['asset_ids'][0]
    monkeypatch.setattr('adventure_intelligence.semantic.frame_png',lambda *args:b'frame')
    return work,aid,video


def event(**changes):
    return {'id':'e','kind':'ray','start_ms':0,'end_ms':1000,'score':.8,
            'confidence':1.,'evidence':{'taxon':'ray'},**changes}


def seed(work,aid):
    with connect(work) as db:
        add_events(db,aid,[event()],'manual','v1')
    return bind_event(work,'e','world.ray',approve=True)['id']


def test_identical_event_preserves_approval_and_revision(setup):
    work,aid,_=setup
    bid=seed(work,aid)
    with connect(work) as db:
        add_events(db,aid,[event()],'manual','v1')
        assert db.execute('SELECT review_state FROM bindings WHERE id=?',(bid,)).fetchone()[0]=='approved'
        assert json.loads(db.execute('SELECT evidence_json FROM events').fetchone()[0])=={'taxon':'ray'}
    history=event_history(work,aid)
    assert len(history['revisions'])==1 and len(history['reviews'])==1


@pytest.mark.parametrize('events,version', [([event(end_ms=1100)],'v1'),([],'v1'),([event()],'v2')])
def test_revised_removed_or_reversioned_events_archive_approval(setup,events,version):
    work,aid,_=setup
    seed(work,aid)
    with connect(work) as db:
        add_events(db,aid,events,'manual',version)
        assert db.execute('SELECT COUNT(*) FROM bindings').fetchone()[0]==0
    history=event_history(work,aid)
    assert history['reviews'][-1]['reason']=='invalidated_by_event_revision'
    assert history['reviews'][-1]['decision']=='approved'
    assert json.loads(history['revisions'][0]['payload'])['asset_id']==aid


def test_invalid_revision_rolls_back_history_and_approval(setup):
    work,aid,_=setup; seed(work,aid)
    with pytest.raises(sqlite3.IntegrityError):
        with connect(work) as db:
            add_events(db,aid,[event(score=2)],'manual','v2')
    with connect(work) as db:
        assert db.execute('SELECT review_state FROM bindings').fetchone()[0]=='approved'
        assert db.execute('SELECT score FROM events').fetchone()[0]==.8
    assert len(event_history(work,aid)['reviews'])==1


def test_cross_origin_collision_cannot_replace_manual_event(setup):
    work,aid,_=setup; seed(work,aid)
    with pytest.raises(ValueError,match='collides'):
        with connect(work) as db:
            add_events(db,aid,[event()],'motion','v1')


def test_export_requires_matching_source_and_omits_history(setup,tmp_path):
    work,aid,video=setup; seed(work,aid)
    mapping=tmp_path/'map.json'; mapping.write_text(json.dumps({aid:{
        'src':'https://example.org/video.mp4','poster':'https://example.org/poster.jpg'}}))
    output=tmp_path/'out.json'
    assert export_manifest(work,output,mapping)['publishedBindings']==1
    video.write_bytes(b'changed')
    assert export_manifest(work,output,mapping)['publishedBindings']==0
    doc=json.loads(output.read_text())
    assert 'reviews' not in doc and str(video) not in output.read_text()


class Provider:
    name='stub'; version='1'
    def __init__(self):
        self.calls=0
    def describe(self,raw):
        self.calls+=1
        return {'embedding':[1.,0.], 'objects':[{'label':'ray','reviewed':True}]}


def test_semantics_verified_cache_and_missing_source(setup):
    work,aid,video=setup; provider=Provider()
    first=run_semantics(work,provider,sample_seconds=1)
    assert first['frames_computed']==3 and first['processed']==1
    assert run_semantics(work,provider,sample_seconds=1)['cached']==1
    assert provider.calls==3
    with connect(work) as db:
        doc=completed_document(work,db.execute('SELECT * FROM semantic_jobs').fetchone())
        assert doc['frames'][0]['embeddingInfo']['dimensions']==2
        assert doc['frames'][0]['objects'][0]['reviewed'] is False
        assert str(video) not in json.dumps(doc)
    video.unlink()
    assert len(run_semantics(work,provider,sample_seconds=1)['errors'])==1


def test_semantic_interruption_resumes_only_remaining_frames(setup):
    work,aid,_=setup
    class Interrupt(Provider):
        def describe(self,raw):
            if self.calls==1:
                raise KeyboardInterrupt()
            return super().describe(raw)
    with pytest.raises(KeyboardInterrupt):
        run_semantics(work,Interrupt(),sample_seconds=1)
    with connect(work) as db:
        assert db.execute('SELECT status FROM semantic_jobs').fetchone()[0]=='running'
    provider=Provider()
    result=run_semantics(work,provider,sample_seconds=1)
    assert result['frames_cached']==1 and result['frames_computed']==2
    assert provider.calls==2


def test_semantic_artifact_and_checkpoint_corruption(setup):
    work,aid,_=setup; provider=Provider()
    run_semantics(work,provider,sample_seconds=1)
    next((work/'semantics').glob('*.json')).write_text('corrupt')
    with connect(work) as db:
        db.execute("UPDATE semantic_frames SET payload='{}' WHERE t_ms=1000")
    result=run_semantics(work,provider,sample_seconds=1)
    assert result['frames_computed']==1 and result['frames_cached']==2
    assert result['processed']==1


def test_semantic_config_invalidates_only_semantics(setup):
    work,aid,_=setup; provider=Provider()
    first=run_semantics(work,provider,sample_seconds=1)
    provider.cache_config={'threshold':.6}
    second=run_semantics(work,provider,sample_seconds=1)
    assert first['configId']!=second['configId'] and second['frames_computed']==3
    third=run_semantics(work,provider,sample_seconds=2)
    assert third['configId']!=second['configId'] and third['frames_computed']==2


def test_source_mutation_during_frame_discards_checkpoints(setup):
    work,aid,video=setup
    class Mutate(Provider):
        def describe(self,raw):
            if self.calls==1:
                video.write_bytes(b'changed')
            return super().describe(raw)
    result=run_semantics(work,Mutate(),sample_seconds=1)
    assert len(result['errors'])==1
    with connect(work) as db:
        assert db.execute('SELECT COUNT(*) FROM semantic_frames').fetchone()[0]==0
        assert db.execute('SELECT status FROM semantic_jobs').fetchone()[0]=='failed'


@pytest.mark.parametrize('interval',[0,-1,float('nan'),float('inf')])
def test_invalid_sampling_rejected_before_jobs(setup,interval):
    work,_,_=setup
    with pytest.raises(ValueError):
        run_semantics(work,Provider(),sample_seconds=interval)


def test_failed_new_config_cannot_attach_old_semantics(setup,monkeypatch):
    from adventure_intelligence.timeline import generate_timelines
    analysis=importlib.import_module('adventure_intelligence.analyze')
    work,aid,_=setup
    monkeypatch.setattr(analysis,'motion_series',lambda p:[{'t_ms':0,'change':0}])
    analysis.analyze(work)
    run_semantics(work,Provider())
    assert json.loads(Path(generate_timelines(work)['paths'][0]).read_text())['bins'][0]['objects']
    class Broken(Provider):
        version='2'
        def describe(self,raw):
            raise RuntimeError('inference failed')
    assert run_semantics(work,Broken())['errors']
    doc=json.loads(Path(generate_timelines(work)['paths'][0]).read_text())
    assert not any(b['objects'] for b in doc['bins'])


def test_failed_frame_preserves_good_checkpoint_for_retry(setup):
    work,aid,_=setup
    class Fail(Provider):
        def describe(self,raw):
            if self.calls==1:
                raise RuntimeError('temporary failure')
            return super().describe(raw)
    assert run_semantics(work,Fail(),sample_seconds=1)['errors']
    result=run_semantics(work,Provider(),sample_seconds=1)
    assert result['frames_cached']==1 and result['frames_computed']==2


def test_nonfinite_prediction_is_not_checkpointed(setup):
    work,aid,_=setup
    class Bad(Provider):
        def describe(self,raw):
            return {'objects':[{'score':float('nan')}]}
    assert run_semantics(work,Bad())['errors']
    with connect(work) as db:
        assert db.execute('SELECT COUNT(*) FROM semantic_frames').fetchone()[0]==0


def test_legacy_approved_binding_archived_before_replacement(setup):
    work,aid,_=setup
    with connect(work) as db:
        db.execute("""INSERT INTO events VALUES ('old',?,'ray',0,1000,1,.8,'manual','v0','{}')""",(aid,))
        db.execute("INSERT INTO bindings(id,event_id,world_entity_id,match_type,review_state) VALUES ('legacy','old','ray','exact','approved')")
        add_events(db,aid,[],'manual','v1')
    history=event_history(work,aid)
    assert history['reviews'][0]['binding_id']=='legacy'
    assert history['reviews'][0]['decision']=='approved'


def test_reverting_event_payload_does_not_restore_old_approval(setup):
    work,aid,_=setup; seed(work,aid)
    with connect(work) as db:
        add_events(db,aid,[event(score=.7)],'manual','v1')
        add_events(db,aid,[event()],'manual','v1')
        assert db.execute('SELECT COUNT(*) FROM bindings').fetchone()[0]==0
    assert len(event_history(work,aid)['revisions'])==2


def test_changed_review_retains_prior_decision(setup):
    work,aid,_=setup; seed(work,aid)
    bind_event(work,'e','world.ray',approve=False)
    history=event_history(work,aid)['reviews']
    assert history[-2]['decision']=='approved'
    assert history[-2]['reason']=='superseded_by_explicit_review'
    assert history[-1]['decision']=='proposed'

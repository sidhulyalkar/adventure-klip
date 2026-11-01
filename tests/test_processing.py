"""Failure/restart tests use controlled bytes; CLI smoke uses real FFmpeg media."""
import importlib
import json
import os
from pathlib import Path

import pytest

from adventure_intelligence.catalog import connect
from adventure_intelligence.indexing import index_folder
from adventure_intelligence.sources import resolve_source
from adventure_intelligence.process import process_folder
from adventure_intelligence.timeline import generate_timelines
from adventure_intelligence.reels import render_reel

analysis = importlib.import_module('adventure_intelligence.analyze')


@pytest.fixture
def footage(tmp_path, monkeypatch):
    monkeypatch.setattr('adventure_intelligence.indexing.probe',
                        lambda p: {'duration': 3, 'audio': False})
    folder = tmp_path/'DJI'; folder.mkdir()
    video = folder/'DJI_001.mp4'; video.write_bytes(b'original recording')
    workspace = tmp_path/'work'
    indexed = index_folder(folder, workspace)
    calls = []
    def extract(path):
        calls.append(path)
        return [{'t_ms': 500, 'change': 0}, {'t_ms': 1000, 'change': .2}]
    monkeypatch.setattr(analysis, 'motion_series', extract)
    return folder, video, workspace, indexed['asset_ids'][0], calls


def test_cache_requires_available_matching_source(footage):
    _, video, workspace, aid, calls = footage
    assert analysis.analyze(workspace)['analyzed'] == 1
    assert analysis.analyze(workspace)['cached'] == 1
    video.write_bytes(b'changed recording')
    failed = analysis.analyze(workspace)
    assert failed['cached'] == 0 and len(failed['failed']) == 1
    assert len(calls) == 1
    video.write_bytes(b'original recording')
    assert analysis.analyze(workspace)['features_cached'] == 1
    video.unlink()
    assert len(analysis.analyze(workspace)['failed']) == 1


def test_resolves_verified_duplicate_when_primary_changes(footage):
    folder, video, workspace, aid, _ = footage
    duplicate = folder/'copy.mp4'; duplicate.write_bytes(video.read_bytes())
    index_folder(folder, workspace)
    video.write_bytes(b'replacement')
    with connect(workspace) as db:
        row = db.execute('SELECT * FROM assets WHERE id=?', (aid,)).fetchone()
        assert resolve_source(db, row) == duplicate
    assert analysis.analyze(workspace)['analyzed'] == 1


def test_index_detects_middle_mutation_with_unchanged_metadata(footage):
    folder, video, workspace, _, _ = footage
    video.write_bytes(b'a' * (3*1024*1024))
    first = index_folder(folder, workspace)['asset_ids'][0]
    before = video.stat()
    with video.open('r+b') as handle:
        handle.seek(1500000); handle.write(b'b')
    os.utime(video, ns=(before.st_atime_ns, before.st_mtime_ns))
    second = index_folder(folder, workspace)
    assert second['indexed'] == 1 and second['asset_ids'][0] != first


@pytest.mark.parametrize('damage', ['delete', 'alter'])
def test_artifact_damage_triggers_extraction(footage, damage):
    _, _, workspace, _, calls = footage
    analysis.analyze(workspace)
    path = next((workspace/'features').glob('*.json'))
    if damage == 'delete':
        path.unlink()
    else:
        document = json.loads(path.read_text())
        document['series'][0]['change'] = .99
        path.write_text(json.dumps(document))
    assert analysis.analyze(workspace)['extracted'] == 1
    assert len(calls) == 2


def test_interruption_recovers_committed_features(footage, monkeypatch):
    _, _, workspace, aid, calls = footage
    from adventure_intelligence.plugins import MotionBaseline
    class Interrupted(MotionBaseline):
        def detect(self, *args):
            raise KeyboardInterrupt()
    monkeypatch.setattr(analysis, 'detector_named', lambda name: Interrupted())
    with pytest.raises(KeyboardInterrupt):
        analysis.analyze(workspace)
    with connect(workspace) as db:
        assert db.execute('SELECT status FROM analyses').fetchone()[0] == 'running'
    monkeypatch.setattr(analysis, 'detector_named', lambda name: MotionBaseline())
    resumed = analysis.analyze(workspace)
    assert resumed['analyzed'] == 1 and resumed['features_cached'] == 1
    assert len(calls) == 1


def test_detector_version_change_reuses_extraction(footage, monkeypatch):
    _, _, workspace, _, calls = footage
    analysis.analyze(workspace)
    from adventure_intelligence.plugins import MotionBaseline
    class NewDetector(MotionBaseline):
        version = '2'
    monkeypatch.setattr(analysis, 'detector_named', lambda name: NewDetector())
    result = analysis.analyze(workspace)
    assert result['features_cached'] == 1 and result['analyzed'] == 1
    assert len(calls) == 1


def test_mutation_during_extraction_does_not_commit_artifact(footage, monkeypatch):
    _, video, workspace, _, _ = footage
    def mutate(path):
        video.write_bytes(b'new contents')
        return []
    monkeypatch.setattr(analysis, 'motion_series', mutate)
    assert len(analysis.analyze(workspace)['failed']) == 1
    with connect(workspace) as db:
        assert db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0


def test_process_scopes_folder_and_resumes(footage):
    folder, video, workspace, aid, calls = footage
    other = folder.parent/'other'; other.mkdir()
    (other/'other.mp4').write_bytes(b'unrelated')
    index_folder(other, workspace)
    first = process_folder(folder, workspace, activity='ski')
    assert first['status'] == 'completed' and first['analysis']['analyzed'] == 1
    assert first['timelines']['generated'] == 1
    second = process_folder(folder, workspace, activity='ski')
    assert second['analysis']['cached'] == 1 and len(calls) == 1
    (folder/'next.mp4').write_bytes(b'new video')
    third = process_folder(folder, workspace)
    assert third['analysis']['analyzed'] == 1 and third['analysis']['cached'] == 1
    assert len(calls) == 2


def test_process_rejects_overlapping_workspace_before_writing(tmp_path):
    with pytest.raises(ValueError):
        process_folder(tmp_path, tmp_path/'unsafe')
    assert not (tmp_path/'unsafe').exists()


def test_timeline_attaches_semantics_and_rejects_stale_features(footage, monkeypatch):
    _, _, workspace, aid, _ = footage
    analysis.analyze(workspace)
    semantics = workspace/'semantics'; semantics.mkdir()
    (semantics/f'{aid}-stub.json').write_text(json.dumps({'frames': [
        {'tMs': 1000, 'objects': [{'label': 'tree'}], 'embedding': [.1]},
        {'tMs': 9000, 'objects': [{'label': 'out of bounds'}]}]}))
    from adventure_intelligence.semantic_jobs import run_semantics
    monkeypatch.setattr('adventure_intelligence.semantic.frame_png',lambda *args: b'frame')
    class Provider:
        name='stub'; version='1'
        def describe(self, raw):
            return {'objects':[{'label':'tree'}],'embedding':[.1]}
    run_semantics(workspace,Provider(),sample_seconds=1)
    result = generate_timelines(workspace)
    doc = json.loads(Path(result['paths'][0]).read_text())
    assert doc['bins'][0]['objects'][0]['label'] == 'tree'
    next((workspace/'features').glob('*.json')).unlink()
    assert generate_timelines(workspace)['missing'] == 1


def test_render_refuses_changed_source(footage, tmp_path):
    _, video, workspace, aid, _ = footage
    video.write_bytes(b'replaced')
    plan = {'schemaVersion': 1, 'segments': [
        {'sourceAssetId': aid, 'startMs': 0, 'endMs': 1000}]}
    with pytest.raises(OSError, match='content identity'):
        render_reel(workspace, plan, tmp_path/'out.mp4')
    assert not (tmp_path/'out.mp4').exists()


def test_source_duration_bounds_are_preserved(footage, monkeypatch):
    from adventure_intelligence.timeline import timeline_from_series
    _, _, workspace, _, _ = footage
    monkeypatch.setattr(analysis, 'motion_series', lambda p: [
        {'t_ms': 0, 'change': 0}, {'t_ms': 1000, 'change': 0},
        {'t_ms': 2900, 'change': 1.0}])
    assert analysis.analyze(workspace)['analyzed'] == 1
    with connect(workspace) as db:
        assert db.execute('SELECT MAX(end_ms) FROM events').fetchone()[0] == 3000
    doc = timeline_from_series('a', 3000, [
        {'t_ms': 2999, 'change': .1}, {'t_ms': 3500, 'change': 1}])
    assert doc['durationMs'] == 3000
    assert sum(b['signals']['visualMotion']['samples'] for b in doc['bins']) == 1


def test_process_cli_real_media_incremental_and_failure(tmp_path, capsys):
    import shutil
    import subprocess
    from adventure_intelligence.cli import main
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg unavailable')
    folder = tmp_path/'DJI'; folder.mkdir()
    video = folder/'DJI_001.mp4'
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-f', 'lavfi', '-i',
        'testsrc2=size=160x90:rate=12', '-t', '2', '-c:v', 'libx264', str(video)], check=True)
    from adventure_intelligence.ingest import sha_file
    digest = sha_file(video)
    args = ['--workspace', str(tmp_path/'work'), 'process', str(folder), '--activity', 'ski']
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first['analysis']['extracted'] == 1 and first['timelines']['generated'] == 1
    assert main(args) == 0
    second = json.loads(capsys.readouterr().out)
    assert second['analysis']['cached'] == 1 and second['analysis']['extracted'] == 0
    assert sha_file(video) == digest
    (folder/'corrupt.mp4').write_bytes(b'not a video')
    assert main(args) == 1
    failed = json.loads(capsys.readouterr().out)
    # Corrupt input is rejected early during indexing, rather than inserted
    # into the catalog only to fail again during every later stage.
    assert failed['status'] == 'partial_failure'
    assert len(failed['index']['errors']) == 1
    assert failed['analysis']['failed'] == []


def test_render_rejects_output_over_unselected_source(footage):
    folder, _, workspace, aid, _ = footage
    other = folder/'other.mp4'; other.write_bytes(b'another recording')
    index_folder(folder, workspace)
    plan = {'schemaVersion': 1, 'segments': [
        {'sourceAssetId': aid, 'startMs': 0, 'endMs': 1000}]}
    with pytest.raises(ValueError, match='overwrite a source'):
        render_reel(workspace, plan, other)
    assert other.read_bytes() == b'another recording'


def test_index_retries_failed_probe_without_changing_identity(footage, monkeypatch):
    folder, _, workspace, aid, _ = footage
    with connect(workspace) as db:
        db.execute('UPDATE assets SET duration=0')
    result = index_folder(folder, workspace)
    assert result['asset_ids'] == [aid]
    with connect(workspace) as db:
        assert db.execute('SELECT duration FROM assets').fetchone()[0] == 3


def test_process_with_real_histogram_semantics(tmp_path):
    import shutil
    import subprocess
    pytest.importorskip('PIL')
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg unavailable')
    folder=tmp_path/'DJI'; folder.mkdir()
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',
        'testsrc2=size=160x90:rate=12','-t','2','-c:v','libx264',str(folder/'sample.mp4')],check=True)
    work=tmp_path/'work'
    first=process_folder(folder,work,semantic_backend='histogram',sample_seconds=1)
    assert first['status']=='completed' and first['semantics']['frames_computed']==2
    doc=json.loads(Path(first['timelines']['paths'][0]).read_text())
    assert len(doc['bins'][0]['embeddingFrames'])==2
    second=process_folder(folder,work,semantic_backend='histogram',sample_seconds=1)
    assert second['semantics']['cached']==1 and second['analysis']['cached']==1

"""Sequential, restartable folder processing using the workspace writer lock."""
from .analyze import analyze
from .indexing import index_folder
from .locking import lock_workspace
from .timeline import generate_timelines


def process_folder(source, workspace, *, activity='unknown', detector='baseline',
                   allow_removable=False, semantic_backend=None, sample_seconds=4.0,
                   progress=None, audio_evidence=False):
    # Validate before taking the lock: never create a workspace inside the source.
    index_folder(source, workspace, activity=activity, dry_run=True,
                 allow_removable=allow_removable)
    if progress is not None:
        progress.note('pipeline', f'source {source}; workspace {workspace}')
    with lock_workspace(workspace):
        indexed = index_folder(source, workspace, activity=activity,
                               allow_removable=allow_removable,progress=progress)
        asset_ids = set(indexed['asset_ids'])
        analysis = analyze(workspace, detector_name=detector, asset_ids=asset_ids,
                           progress=progress)
        semantics = None
        if semantic_backend is not None:
            from .semantic import generate_semantics
            semantics = generate_semantics(workspace,backend=semantic_backend,
                sample_seconds=sample_seconds,asset_ids=asset_ids,progress=progress)
        if progress is not None:
            progress.note('timeline', 'generating aligned timelines')
        acoustics = None
        if audio_evidence:
            from .acoustics import analyze_audio
            acoustics=analyze_audio(workspace,asset_ids=asset_ids,progress=progress)
        timelines = generate_timelines(workspace, asset_ids=asset_ids,
            source='optical-flow' if detector=='flow' else 'pixel-change')
    ok = not (indexed['errors'] or analysis['failed'] or timelines['failed'] or timelines['missing']
              or (semantics is not None and semantics['errors'])
              or (acoustics is not None and acoustics['failed']))
    if progress is not None:
        progress.note('pipeline', 'completed' if ok else 'partial failure; inspect JSON errors')
    return {'status': 'completed' if ok else 'partial_failure', 'index': indexed,
            'analysis': analysis, 'semantics':semantics, 'acoustics':acoustics,
            'timelines': timelines}
